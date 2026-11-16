"""步骤处理器：parse / outline / render_batch / assemble。

每个处理器接收 StepContext；返回 dict 结果。处理器内部不得长时间持有数据库写锁。
模型相关处理器在 provider 不可用时显式失败，不伪造成功。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..repositories import now_iso

from ..contracts import ParsedContent
from ..parsers import parse_document
from ..services.context import WebContext
from . import states


@dataclass
class StepContext:
    ctx: WebContext
    job_id: str
    step_id: str
    input_revision: int
    payload: dict
    job_row: object = None
    checkpoint: dict = field(default_factory=dict)

    @property
    def settings(self):
        return self.ctx.settings

    def update_job_state(self, **kw) -> int:
        """基于当前 job_revision 的乐观锁状态更新（步骤内统一入口）。"""
        job = self.ctx.jobs.get(self.job_id)
        return self.ctx.jobs.update_state(self.job_id, job["job_revision"], **kw)

    def renew_lease(self, minutes: int = 10) -> None:
        """长任务（逐页模型调用等）执行中续租，防止租约过期后被重复领取。"""
        from datetime import datetime, timedelta, timezone

        expires = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        self.ctx.conn.execute(
            "UPDATE steps SET lease_expires_at=?, updated_at=? WHERE id=?",
            (expires, now_iso(), self.step_id))

    def save_checkpoint(self, data: dict) -> None:
        """保存检查点（可从中断恢复）。"""
        self.checkpoint.update(data)
        self.ctx.conn.execute(
            "UPDATE steps SET checkpoint_json=?, updated_at=datetime('now') WHERE id=?",
            (json.dumps(self.checkpoint, ensure_ascii=False), self.step_id))

    def job_cancelled(self) -> bool:
        row = self.ctx.jobs.get(self.job_id)
        return row is None or row["status"] in ("cancelled", "completed")


HandlerResult = dict
Handler = Callable[[StepContext], HandlerResult]


# ---------------------------------------------------------------- parse


def handle_parse(step: StepContext) -> HandlerResult:
    """解析任务资料；全部成功 → outline/running 并入队 outline 步骤；部分不可解析 → needs_input。"""
    docs = step.ctx.records.documents_for_job(step.job_id, kind="material")
    pending = [d for d in docs if d["parse_status"] == "pending"]
    parsed_summary = []
    needs_input = []
    for d in pending:
        if step.job_cancelled():
            return {"cancelled": True}
        path = step.ctx.files.resolve(d["stored_path"])
        try:
            content = parse_document(path.read_bytes(), d["display_name"])
        except Exception as exc:  # noqa: BLE001
            step.ctx.records.set_document_parse_status(d["id"], "needs_input")
            needs_input.append({"document": d["display_name"], "reason": str(exc)})
            continue
        # 保存解析产物（含事实）到任务目录并登记
        rel = f"jobs/{step.job_id}/parsed/{d['id']}.json"
        step.ctx.files.write_bytes_atomic(rel, content.model_dump_json().encode())
        step.ctx.records.set_document_parse_status(d["id"], content.document.parse_status)
        if content.document.parse_status != "ok":
            needs_input.append({"document": d["display_name"], "reason": content.document.parse_error})
            continue
        parsed_summary.append({
            "document_id": d["id"],
            "display_name": d["display_name"],
            "artifact_relative_path": rel,
            "block_count": len(content.blocks),
            "fact_count": len(content.facts),
        })
    if needs_input and not parsed_summary:
        new_state = states.transition(states.State("intake", "running"), "parse_needs_input")
        step.update_job_state(status=new_state.status,
                              error=f"资料不可解析：{needs_input[0]['reason']}")
        return {"needs_input": needs_input}
    # 快照：解析结果索引
    rev = step.ctx.records.next_revision(step.job_id)
    step.ctx.records.save_revision(
        step.job_id, "parsed",
        {"revision": rev, "documents": parsed_summary},
        input_revision=step.input_revision)
    step.update_job_state(phase="outline", status="running")
    if needs_input:
        # 部分可解析：继续，但记录缺口
        step.ctx.records.save_change_request(step.job_id, {"kind": "parse_warnings", "items": needs_input})
    current_rev = step.ctx.jobs.get(step.job_id)["job_revision"]
    outline_step = step.ctx.steps.enqueue(step.job_id, "outline", input_revision=current_rev)
    return {"parsed": parsed_summary, "needs_input": needs_input, "outline_step": outline_step}


# ---------------------------------------------------------------- outline（模型步骤占位，S10 接入）


def handle_outline(step: StepContext) -> HandlerResult:
    """大纲生成：使用任务绑定的模型（页面配置）；不可用时显式失败（含原因）。"""
    from ..planning.outline import generate_outline

    return generate_outline(step)


# ---------------------------------------------------------------- generate_specs（大纲批准后生成逐页规格）


def handle_generate_specs(step: StepContext) -> HandlerResult:
    """批准的大纲 → 逐页 SlideSpec（带槽位预算）→ 保存规格快照 → 入队渲染。"""
    import json as _json

    from ..contracts import Outline
    from ..planning.slides import generate_specs
    from ..services.model_resolver import resolve_job_provider
    from ..services.render_service import _load_profile

    provider = resolve_job_provider(step.ctx, step.job_row["model_profile_id"] if step.job_row else None)
    job = step.ctx.jobs.get(step.job_id)
    outline_row = step.ctx.conn.execute(
        "SELECT data_json FROM revisions WHERE job_id=? AND kind='outline' ORDER BY revision DESC LIMIT 1",
        (step.job_id,)).fetchone()
    if outline_row is None:
        raise RuntimeError("找不到已批准的大纲")
    outline_data = _json.loads(outline_row["data_json"])
    outline_data.pop("revision", None)  # 快照信封字段不属协议
    outline = Outline.model_validate(outline_data)

    contents = []
    for d in step.ctx.records.documents_for_job(step.job_id, kind="material"):
        p = step.ctx.files.resolve(f"jobs/{step.job_id}/parsed/{d['id']}.json")
        if p.exists():
            from ..contracts import ParsedContent

            contents.append(ParsedContent.model_validate_json(p.read_bytes()))

    profile = _load_profile(step)
    # 逐页调用模型耗时较长（每页 20-60s），先续租再执行
    step.renew_lease(minutes=10)
    specs, usage = generate_specs(provider, outline, contents, profile, outline.brief.scene_id,
                                  on_page=lambda i, total: step.renew_lease(minutes=10))

    revision = step.ctx.records.next_revision(step.job_id)
    step.ctx.records.save_revision(
        step.job_id, "specs",
        {"revision": revision, "specs": [s.model_dump(mode="json") for s in specs]},
        input_revision=step.input_revision)
    step.update_job_state(status="running")  # 保持 sample/running
    current_rev = step.ctx.jobs.get(step.job_id)["job_revision"]
    render_step = step.ctx.steps.enqueue(
        step.job_id, "render_batch", input_revision=current_rev,
        scope_key="sample", payload={"scope_key": "sample", "specs_revision": revision})
    return {"specs_revision": revision, "render_step": render_step,
            "usage": usage.__dict__ if usage else {}}


# ---------------------------------------------------------------- render_batch（渲染已就绪；spec 来源 S11 接模型）


def handle_render_batch(step: StepContext) -> HandlerResult:
    """渲染一个批次：SlideSpecs → PPTX+Notes → PDF → QA。

    自愈：断环修复前的旧任务直接入队了渲染（无规格），此处自动转交
    规格生成步骤，避免任务卡死。
    """
    from ..qa import run_repair_loop
    from ..rendering.builder import build_from_specs
    from ..rendering.libreoffice import convert_to_pdf
    from ..rendering.preview import render_thumbnails
    from ..services.render_service import render_batch_impl

    try:
        return render_batch_impl(step)
    except RuntimeError as exc:
        if "没有页面规格快照" not in str(exc):
            raise
        job = step.ctx.jobs.get(step.job_id)
        if job is None:
            raise
        gen_step = step.ctx.steps.enqueue(
            step.job_id, "generate_specs", input_revision=step.input_revision,
            scope_key=step.payload.get("scope_key", "sample"),
            payload={"scope_key": step.payload.get("scope_key", "sample")})
        return {"handed_off": True, "generate_specs_step": gen_step,
                "note": "旧任务缺规格快照，已自动转交规格生成"}


# ---------------------------------------------------------------- assemble（S14 完整实现）


def handle_assemble(step: StepContext) -> HandlerResult:
    from ..services.release_service import assemble_impl

    return assemble_impl(step)


def handle_regenerate_pages(step: StepContext) -> HandlerResult:
    """页面级修改：重新生成受影响页的 SlideSpec → 新快照 → 入队渲染。"""
    from ..planning.slides import apply_page_change
    from ..providers.base import build_provider

    from ..services.model_resolver import resolve_job_provider

    provider = resolve_job_provider(step.ctx, step.job_row["model_profile_id"] if step.job_row else None)
    change_row = step.ctx.conn.execute(
        "SELECT plan_json FROM change_requests WHERE id=?", (step.payload["change_id"],)).fetchone()
    if change_row is None:
        raise RuntimeError("变更计划不存在")
    plan = json.loads(change_row["plan_json"])
    job = step.ctx.jobs.get(step.job_id)
    specs_row = step.ctx.records.get_revision(step.job_id, job["job_revision"])
    if specs_row is None or specs_row["kind"] != "specs":
        raise RuntimeError("当前修订无页面规格")
    specs = [SlideSpec.model_validate(s) for s in json.loads(specs_row["data_json"])["specs"]]
    contents = []
    for d in step.ctx.records.documents_for_job(step.job_id, kind="material"):
        p = step.ctx.files.resolve(f"jobs/{step.job_id}/parsed/{d['id']}.json")
        if p.exists():
            contents.append(ParsedContent.model_validate_json(p.read_bytes()))
    from ..providers.base import SYSTEM_RULES, validate_model_output_loose

    page_ids = set(step.payload.get("page_ids", []))
    new_specs = list(specs)
    for pid in page_ids:
        idx = next((i for i, s in enumerate(new_specs) if s.slide_id == pid), None)
        if idx is None:
            continue
        old = new_specs[idx]
        instruction = "；".join(it["instruction"] for it in plan.get("items", []) if pid in it.get("page_ids", []))
        user = (
            f"任务：按修改意见重新生成单页规格。\n页面：{old.headline}\n修改意见：{instruction}\n"
            f"原规格：{old.model_dump_json()[:3000]}\n"
            "输出新的 SlideSpec JSON（slide_id 必须保持不变；数字与限定条件不得丢失）。"
        )
        data, _ = provider.generate_json(SYSTEM_RULES, user, max_tokens=2500)
        data.setdefault("slide_id", pid)
        new_spec = validate_model_output_loose(SlideSpec, data)
        new_specs, _ = apply_page_change(new_specs, pid, new_spec)
    rev = step.ctx.records.next_revision(step.job_id)
    step.ctx.records.save_revision(
        step.job_id, "specs",
        {"revision": rev, "specs": [s.model_dump(mode="json") for s in new_specs]},
        input_revision=step.input_revision)
    step.update_job_state(status="running")
    render_step = step.ctx.steps.enqueue(
        step.job_id, "render_batch", input_revision=step.ctx.jobs.get(step.job_id)["job_revision"],
        scope_key=f"fix-{step.payload['change_id']}",
        payload={"scope_key": f"fix-{step.payload['change_id']}",
                 "specs_revision": rev})
    return {"specs_revision": rev, "render_step": render_step}


HANDLERS: dict[str, Handler] = {
    "parse": handle_parse,
    "outline": handle_outline,
    "generate_specs": handle_generate_specs,
    "render_batch": handle_render_batch,
    "assemble": handle_assemble,
    "regenerate_pages": handle_regenerate_pages,
}
