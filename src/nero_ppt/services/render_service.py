"""渲染服务：批次/样张渲染步骤实现（builder + QA + PDF + 预览）。"""
from __future__ import annotations

import json
from pathlib import Path

from ..contracts import ParsedContent, SlideSpec, TemplateProfile, from_envelope
from ..qa import run_repair_loop


def _load_specs(step) -> list[SlideSpec]:
    """从 payload 或最新 specs 修订读取 SlideSpec 列表。"""
    if step.payload.get("specs_inline"):
        return [SlideSpec.model_validate(item) for item in step.payload["specs_inline"]]
    specs_revision = step.payload.get("specs_revision")
    if specs_revision is None:
        row = step.ctx.conn.execute(
            "SELECT revision FROM revisions WHERE job_id=? AND kind='specs' ORDER BY revision DESC LIMIT 1",
            (step.job_id,)).fetchone()
        if row is None:
            raise RuntimeError("任务没有页面规格快照，无法渲染")
        specs_revision = row["revision"]
    rev = step.ctx.records.get_revision(step.job_id, specs_revision)
    if rev is None:
        raise RuntimeError(f"找不到规格修订 {specs_revision}")
    data = json.loads(rev["data_json"])
    return [SlideSpec.model_validate(item) for item in data["specs"]]


def _load_profile(step) -> TemplateProfile:
    prof_id = step.payload.get("profile_id") or _job_profile_id(step)
    row = step.ctx.conn.execute(
        "SELECT * FROM template_profiles WHERE id=?", (prof_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"找不到模板档案 {prof_id}")
    return TemplateProfile.model_validate_json(row["profile_json"])


def _job_profile_id(step) -> str:
    job = step.ctx.jobs.get(step.job_id)
    if job is None or not job["template_profile_id"]:
        raise RuntimeError("任务未绑定模板档案")
    return job["template_profile_id"]


def _template_path(step, profile):
    """模板文件解析：按 documents 实际存储路径（而非约定文件名）；
    POTX 用规范化副本；最后回退内置模板。"""
    for d in step.ctx.records.documents_for_job(step.job_id, kind="template"):
        try:
            p = step.ctx.files.resolve(d["stored_path"])
        except ValueError:
            continue
        if not p.exists():
            continue
        if d["file_ext"] == profile.file_ext and d["file_hash"] == profile.file_hash:
            return p  # 哈希匹配的模板原件
        if d["file_ext"] == ".potx" and profile.file_ext == ".pptx":
            norm = p.with_name(p.stem + "-norm.pptx")
            if norm.exists():
                return norm  # POTX 规范化副本
    builtin = step.ctx.settings.data_dir / "templates" / "builtin-classic.pptx"
    if builtin.exists():
        return builtin
    raise RuntimeError("任务模板文件缺失且内置模板不可用；请重新上传模板")


def render_batch_impl(step) -> dict:
    """渲染批次：构建 PPTX+Notes → PDF → 缩略图 → QA（含有限修复）。"""
    from .resources import ResourceLimitError, check_disk

    try:
        check_disk(step.ctx, for_render=True)
    except ResourceLimitError as exc:
        raise RuntimeError(str(exc))
    specs = _load_specs(step)
    profile = _load_profile(step)
    template_path = _template_path(step, profile)
    resource_map = {k: str(v) for k, v in (step.payload.get("resource_map") or {}).items()}

    revision = step.ctx.records.next_revision(step.job_id)
    out_dir = f"jobs/{step.job_id}/revisions/{revision}"
    pptx_rel = f"{out_dir}/batch-{step.payload.get('scope_key', 'all')}.pptx"
    pdf_rel = f"{out_dir}/batch-{step.payload.get('scope_key', 'all')}.pdf"

    result, plans = _build_with_template(step, template_path, profile, specs,
                                         step.ctx.settings.data_dir / "tmp" / f"build-{step.step_id}", pptx_rel)
    # PDF（独立临时目录，成功后原子移动）
    from ..rendering.libreoffice import ConversionError, convert_to_pdf
    from ..rendering.preview import render_thumbnails

    tmp_work = step.ctx.settings.data_dir / "tmp" / f"render-{step.step_id}"
    tmp_work.mkdir(parents=True, exist_ok=True)
    pdf_result = convert_to_pdf(result.output_path, tmp_work, timeout=300,
                                out_path=step.ctx.settings.data_dir / pdf_rel)

    # QA 与有界修复
    known_sources = {row["id"] for row in step.ctx.records.documents_for_job(step.job_id, kind="material")}
    fact_values: dict[str, str] = {}
    for d in step.ctx.records.documents_for_job(step.job_id, kind="material"):
        parsed_rel = f"jobs/{step.job_id}/parsed/{d['id']}.json"
        p = step.ctx.files.resolve(parsed_rel)
        if p.exists():
            content = ParsedContent.model_validate_json(p.read_bytes())
            fact_values.update({f.fact_id: f.normalized_value for f in content.facts})
    reports, still_blocking = run_repair_loop(
        result.output_path, specs, profile, plans, max_rounds=step.settings.max_repair_rounds,
        known_source_ids=known_sources, fact_values=fact_values)

    # 登记产物（先保存渲染修订快照，占住 revision 号避免后续步骤撞号）
    import hashlib

    pptx_bytes = step.ctx.files.resolve(pptx_rel).read_bytes()
    step.ctx.records.save_revision(
        step.job_id, "render",
        {"revision": revision, "scope_key": step.payload.get("scope_key", "all"),
         "pptx": pptx_rel, "pdf": pdf_rel, "slide_count": len(specs)},
        input_revision=step.input_revision)
    step.ctx.records.add_artifact(step.job_id, "pptx", revision, pptx_rel,
                                  hashlib.sha256(pptx_bytes).hexdigest(), len(pptx_bytes))
    pdf_bytes = step.ctx.files.resolve(pdf_rel).read_bytes()
    step.ctx.records.add_artifact(step.job_id, "pdf", revision, pdf_rel,
                                  pdf_result.file_hash, pdf_result.byte_size)
    thumbs = render_thumbnails(step.ctx.files.resolve(pdf_rel), tmp_work / "thumbs")
    for i, t in enumerate(thumbs):
        t_rel = f"{out_dir}/thumbs/{t.name}"
        step.ctx.files.write_bytes_atomic(t_rel, t.read_bytes())
        step.ctx.records.add_artifact(step.job_id, "thumbnail", revision, t_rel,
                                      hashlib.sha256(t.read_bytes()).hexdigest(), t.stat().st_size,
                                      slide_id=specs[i].slide_id if i < len(specs) else None)

    # 状态转换
    from ..workflow import states

    job = step.ctx.jobs.get(step.job_id)
    cur = states.State(job["phase"], job["status"])
    event = "sample_done" if cur.phase == "sample" else "batch_done"
    new_state = states.transition(cur, event)
    new_rev = step.update_job_state(phase=new_state.phase, status=new_state.status)
    # 清理临时目录
    import shutil

    shutil.rmtree(tmp_work, ignore_errors=True)
    return {
        "revision": revision, "job_revision": new_rev, "pptx": pptx_rel, "pdf": pdf_rel,
        "qa_blocking": [c.model_dump() for r in reports for c in r.blocking()],
        "qa_reports": [r.model_dump() for r in reports],
        "still_blocking": still_blocking,
        "warnings": result.warnings,
    }


def _build_with_template(step, template_path, profile, specs, tmp_parent, pptx_rel):
    from ..rendering.builder import build_from_specs

    Path(tmp_parent).mkdir(parents=True, exist_ok=True)
    result, plans = build_from_specs(
        template_path, profile, specs,
        step.ctx.settings.data_dir / pptx_rel,
        resource_map=None, tmp_dir=tmp_parent)
    return result, plans
