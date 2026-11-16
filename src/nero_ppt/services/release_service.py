"""发布服务：整稿组装与最终发布事务。"""
from __future__ import annotations

import hashlib
import json
import shutil

from ..contracts import (
    ManifestEntry,
    ParsedContent,
    ReleaseManifest,
    SlideReleaseRef,
    SlideSpec,
)
from ..qa import run_repair_loop


def _latest_specs(step) -> list[SlideSpec]:
    row = step.ctx.conn.execute(
        "SELECT data_json FROM revisions WHERE job_id=? AND kind='specs' ORDER BY revision DESC LIMIT 1",
        (step.job_id,)).fetchone()
    if row is None:
        raise RuntimeError("任务没有页面规格快照，无法组装")
    return [SlideSpec.model_validate(s) for s in json.loads(row["data_json"])["specs"]]


def _template_path(step, profile):
    """模板文件解析：documents 实际存储路径（哈希匹配）→ POTX 规范化副本 → 内置模板。"""
    for d in step.ctx.records.documents_for_job(step.job_id, kind="template"):
        try:
            p = step.ctx.files.resolve(d["stored_path"])
        except ValueError:
            continue
        if not p.exists():
            continue
        if d["file_ext"] == profile.file_ext and d["file_hash"] == profile.file_hash:
            return p
        if d["file_ext"] == ".potx" and profile.file_ext == ".pptx":
            norm = p.with_name(p.stem + "-norm.pptx")
            if norm.exists():
                return norm
    builtin = step.ctx.settings.data_dir / "templates" / "builtin-classic.pptx"
    if builtin.exists():
        return builtin
    raise RuntimeError("任务模板文件缺失且内置模板不可用；请重新上传模板")


def _load_profile(step):
    job = step.ctx.jobs.get(step.job_id)
    if not job["template_profile_id"]:
        raise RuntimeError("任务未绑定模板档案")
    row = step.ctx.conn.execute(
        "SELECT profile_json FROM template_profiles WHERE id=?", (job["template_profile_id"],)).fetchone()
    from ..contracts import TemplateProfile

    return TemplateProfile.model_validate_json(row["profile_json"])


def assemble_impl(step) -> dict:
    """整稿组装：完整 PPTX（含全部批准页）→ PDF → Markdown 讲稿 → 全稿 QA → final/awaiting_review。

    样张页面与最终页面来自同一模板包与受控 RenderPlan；不从 ZIP 拼接单页文件。
    """
    from ..rendering.builder import build_from_specs
    from ..rendering.libreoffice import convert_to_pdf
    from ..rendering.notes import export_notes_markdown
    from ..rendering.preview import render_thumbnails
    from ..workflow import states

    from .resources import ResourceLimitError, check_disk

    try:
        check_disk(step.ctx, for_render=True)
    except ResourceLimitError as exc:
        raise RuntimeError(str(exc))
    specs = _latest_specs(step)
    profile = _load_profile(step)
    template_path = _template_path(step, profile)

    revision = step.ctx.records.next_revision(step.job_id)
    out_dir = f"jobs/{step.job_id}/revisions/{revision}"
    pptx_rel = f"{out_dir}/final.pptx"
    pdf_rel = f"{out_dir}/final.pdf"
    notes_rel = f"{out_dir}/notes.md"

    result, plans = build_from_specs(
        template_path, profile, specs,
        step.ctx.settings.data_dir / pptx_rel,
        tmp_dir=step.ctx.settings.data_dir / "tmp" / f"assemble-{step.step_id}")

    tmp_work = step.ctx.settings.data_dir / "tmp" / f"assemble-render-{step.step_id}"
    tmp_work.mkdir(parents=True, exist_ok=True)
    pdf_result = convert_to_pdf(result.output_path, tmp_work, timeout=300,
                                out_path=step.ctx.settings.data_dir / pdf_rel)

    # Markdown 讲稿（与页面同序同版本）
    title = step.ctx.jobs.get(step.job_id)["title"]
    md = export_notes_markdown(specs, title=title)
    step.ctx.files.write_bytes_atomic(notes_rel, md.encode("utf-8"))

    # 全稿 QA（结构/几何/内容；视觉在 PDF 侧已有页数校验）
    known_sources = {r["id"] for r in step.ctx.records.documents_for_job(step.job_id, kind="material")}
    fact_values: dict[str, str] = {}
    for d in step.ctx.records.documents_for_job(step.job_id, kind="material"):
        p = step.ctx.files.resolve(f"jobs/{step.job_id}/parsed/{d['id']}.json")
        if p.exists():
            content = ParsedContent.model_validate_json(p.read_bytes())
            fact_values.update({f.fact_id: f.normalized_value for f in content.facts})
    reports, still_blocking = run_repair_loop(
        result.output_path, specs, profile, plans,
        max_rounds=step.settings.max_repair_rounds,
        known_source_ids=known_sources, fact_values=fact_values)

    # 校验：PPTX 页数 == PDF 页数 == 讲稿页数（顺序一致）
    from ..rendering.libreoffice import pdf_page_count

    pdf_pages = pdf_page_count(pdf_result.pdf_path if pdf_result.pdf_path.exists()
                               else step.ctx.files.resolve(pdf_rel))
    if pdf_pages != len(specs):
        raise RuntimeError(f"页数不一致：PPTX {len(specs)} vs PDF {pdf_pages}")
    md_pages = md.count("## 第 ")
    if md_pages != len(specs):
        raise RuntimeError(f"讲稿页数不一致：{md_pages} vs {len(specs)}")

    # 登记产物
    pptx_bytes = step.ctx.files.resolve(pptx_rel).read_bytes()
    notes_bytes = step.ctx.files.resolve(notes_rel).read_bytes()
    pptx_hash = hashlib.sha256(pptx_bytes).hexdigest()
    pdf_hash = pdf_result.file_hash
    notes_hash = hashlib.sha256(notes_bytes).hexdigest()
    step.ctx.records.add_artifact(step.job_id, "pptx", revision, pptx_rel, pptx_hash, len(pptx_bytes))
    step.ctx.records.add_artifact(step.job_id, "pdf", revision, pdf_rel, pdf_hash, pdf_result.byte_size)
    step.ctx.records.add_artifact(step.job_id, "notes", revision, notes_rel, notes_hash, len(notes_bytes))
    thumbs = render_thumbnails(step.ctx.files.resolve(pdf_rel), tmp_work / "thumbs")
    for i, t in enumerate(thumbs):
        t_rel = f"{out_dir}/thumbs/{t.name}"
        step.ctx.files.write_bytes_atomic(t_rel, t.read_bytes())
        step.ctx.records.add_artifact(step.job_id, "thumbnail", revision, t_rel,
                                      hashlib.sha256(t.read_bytes()).hexdigest(), t.stat().st_size,
                                      slide_id=specs[i].slide_id if i < len(specs) else None)

    # 发布清单草案（发布事务在用户最终批准时执行）
    manifest = ReleaseManifest(
        release_id=f"rel-{step.job_id[:8]}-{revision}",
        job_id=step.job_id,
        job_revision=step.ctx.jobs.get(step.job_id)["job_revision"],
        template_version=profile.version,
        slides=[SlideReleaseRef(page_index=i, slide_id=s.slide_id, slide_revision=1)
                for i, s in enumerate(specs)],
        files=[
            ManifestEntry(artifact_id="final-pptx", kind="pptx", relative_path=pptx_rel,
                          file_hash=pptx_hash, byte_size=len(pptx_bytes)),
            ManifestEntry(artifact_id="final-pdf", kind="pdf", relative_path=pdf_rel,
                          file_hash=pdf_hash, byte_size=pdf_result.byte_size),
            ManifestEntry(artifact_id="final-notes", kind="notes", relative_path=notes_rel,
                          file_hash=notes_hash, byte_size=len(notes_bytes)),
        ],
    )
    step.ctx.records.save_revision(
        step.job_id, "release_draft",
        {"revision": revision, "manifest": manifest.model_dump(mode="json"),
         "qa_blocking": [c.model_dump() for r in reports for c in r.blocking()]},
        input_revision=step.input_revision)

    cur = states.State("final", "running")
    new = states.transition(cur, "final_done")
    new_rev = step.update_job_state(phase=new.phase, status=new.status)
    shutil.rmtree(tmp_work, ignore_errors=True)
    return {"revision": revision, "job_revision": new_rev,
            "pptx": pptx_rel, "pdf": pdf_rel, "notes": notes_rel,
            "release_id": manifest.release_id,
            "still_blocking": still_blocking,
            "qa_blocking": [c.model_dump() for r in reports for c in r.blocking()]}


def publish(ctx, owner_id: str, job_id: str, expected_revision: int) -> dict:
    """最终批准后的发布事务：校验 manifest 文件完整 → releases 登记 → completed。

    任一产物失败都不能 completed。
    """
    job = ctx.jobs.get_owned(job_id, owner_id)
    if job is None:
        raise RuntimeError("任务不存在")
    cur_state = (job["phase"], job["status"])
    if cur_state != ("final", "awaiting_review"):
        raise RuntimeError(f"发布要求 final/awaiting_review，当前 {cur_state[0]}/{cur_state[1]}")
    row = ctx.conn.execute(
        "SELECT data_json FROM revisions WHERE job_id=? AND kind='release_draft' ORDER BY revision DESC LIMIT 1",
        (job_id,)).fetchone()
    if row is None:
        raise RuntimeError("缺少发布清单草案")
    draft = json.loads(row["data_json"])
    manifest = ReleaseManifest.model_validate(draft["manifest"])
    if draft.get("still_blocking"):
        raise RuntimeError("存在阻塞性 QA 失败，不能发布")
    # 校验三份核心产物存在且哈希匹配
    kinds = set()
    for entry in manifest.files:
        path = ctx.files.resolve(entry.relative_path)
        if not path.exists():
            raise RuntimeError(f"产物缺失：{entry.relative_path}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.file_hash:
            raise RuntimeError(f"产物哈希不匹配：{entry.relative_path}")
        if entry.byte_size != len(data):
            raise RuntimeError(f"产物大小不匹配：{entry.relative_path}")
        kinds.add(entry.kind)
    if not {"pptx", "pdf", "notes"} <= kinds:
        raise RuntimeError(f"发布清单缺少核心产物：{kinds}")
    # 原子发布：releases 登记 + completed（同事务）
    ctx.conn.execute("BEGIN IMMEDIATE")
    try:
        ctx.records.save_release(job_id, manifest.job_revision, manifest)
        ctx.jobs.update_state(job_id, expected_revision, status="completed")
        ctx.conn.execute("COMMIT")
    except Exception:
        ctx.conn.execute("ROLLBACK")
        raise
    return {"release_id": manifest.release_id, "status": "completed",
            "files": [e.relative_path for e in manifest.files]}
