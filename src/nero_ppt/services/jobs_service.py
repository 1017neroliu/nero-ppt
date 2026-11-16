"""任务业务服务：创建、审核事务、变更应用、取消/重试。

审核事务在同一事务内验证：归属、expected_revision、可审核状态、QA 条件，
写审核记录并创建后续步骤。机械结构错误不能通过批准变成合法产物。
"""
from __future__ import annotations

import json
import sqlite3

from ..contracts import Brief, ChangePlan, Outline, SlideSpec, from_envelope
from ..repositories import ConflictError, FileStore, new_id, now_iso
from ..services.context import WebContext
from ..workflow import states

ALLOWED_MATERIAL_EXTS = {".pdf", ".docx", ".txt", ".md", ".pptx"}
ALLOWED_TEMPLATE_EXTS = {".pptx", ".potx"}

# 阶段 → 审核对象映射
REVIEW_STATE_RULES = {
    "outline": ("outline", "awaiting_review", "approve_outline"),
    "sample": ("sample", "awaiting_review", "approve_sample"),
    "batch": ("batch", "awaiting_review", None),  # approve_batch_more/final 由批次进度决定
    "final": ("final", "awaiting_review", "approve_final"),
}


class JobError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400,
                 details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class JobsService:
    def __init__(self, ctx: WebContext):
        self.ctx = ctx

    # ---------------------------------------------------------------- 创建与列表

    def create_job(self, owner_id: str, title: str, scene_id: str, scene_version: int,
                   brief: dict | None = None, request_id: str | None = None,
                   model_id: str | None = None) -> dict:
        scene = self._scene(scene_id, scene_version)
        if model_id:
            from ..services.models import load_catalog

            if load_catalog(self.ctx.settings.data_dir, self.ctx.settings).get(model_id) is None:
                raise JobError("validation", f"模型配置不存在：{model_id}", 422)
        if request_id:
            cached = self._idempotent(owner_id, "create_job", request_id)
            if cached is not None:
                return cached
        brief_data = dict(brief or {})
        brief_data.setdefault("topic", title)
        brief_data["scene_id"] = scene_id
        brief_data["scene_version"] = scene_version
        brief_model = self._validate_brief(brief_data)
        job_id = self.ctx.jobs.create(owner_id, title, scene_id, scene_version,
                                      brief_model.model_dump(), model_profile_id=model_id)
        result = {"job_id": job_id, "revision": 1}
        if request_id:
            self._put_idempotent(owner_id, "create_job", request_id, result)
        return result

    def list_jobs(self, owner_id: str) -> list[dict]:
        return [
            {"id": r["id"], "title": r["title"], "scene_id": r["scene_id"],
             "phase": r["phase"], "status": r["status"], "revision": r["job_revision"],
             "created_at": r["created_at"], "error": r["error"]}
            for r in self.ctx.jobs.list_for_owner(owner_id)
        ]

    def job_view(self, owner_id: str, job_id: str) -> dict:
        """状态、版本、页面索引、待审核项。归属不匹配统一 404。"""
        job = self.ctx.jobs.get_owned(job_id, owner_id)
        if job is None:
            raise JobError("not_found", "任务不存在", 404)
        # 大纲：取最新 outline 修订（生成成功会推进 job_revision，按当前修订号会错位丢失）
        outline_row = self.ctx.conn.execute(
            "SELECT data_json FROM revisions WHERE job_id=? AND kind='outline' ORDER BY revision DESC LIMIT 1",
            (job_id,)).fetchone()
        outline = json.loads(outline_row["data_json"]) if outline_row else None
        specs = self._current_specs(job)
        pending_step = self.ctx.conn.execute(
            "SELECT kind, status FROM steps WHERE job_id=? AND status IN ('pending','running')"
            " ORDER BY created_at LIMIT 1", (job_id,)).fetchone()
        # 步骤执行历史（时间线展示 + 分步重试）
        steps_history = [
            {"id": r["id"], "kind": r["kind"], "status": r["status"],
             "error": r["error"], "updated_at": r["updated_at"]}
            for r in self.ctx.conn.execute(
                "SELECT id, kind, status, error, updated_at FROM steps"
                " WHERE job_id=? ORDER BY created_at DESC LIMIT 12", (job_id,))
        ]
        # 产物：取最新含产物的修订（渲染产物登记在渲染时 revision）
        latest_art_rev = self.ctx.conn.execute(
            "SELECT MAX(revision) AS r FROM artifacts WHERE job_id=?", (job_id,)).fetchone()["r"]
        artifacts = self.ctx.records.artifacts_for_revision(job_id, latest_art_rev) if latest_art_rev else []
        model_info = None
        if job["model_profile_id"]:
            from ..services.models import load_catalog

            entry = load_catalog(self.ctx.settings.data_dir, self.ctx.settings).get(job["model_profile_id"])
            if entry is not None:
                model_info = entry.public()
        return {
            "id": job["id"], "title": job["title"],
            "phase": job["phase"], "status": job["status"],
            "revision": job["job_revision"], "batch_index": job["batch_index"],
            "model": model_info,
            "error": job["error"],
            "outline": outline,
            "slides": [{"slide_id": s.slide_id, "headline": s.headline,
                        "revision": 1, "visual_intent": s.visual_intent,
                        "target_seconds": s.target_seconds} for s in specs],
            "specs_detail": [s.model_dump(mode="json") for s in specs],
            "pending_step": dict(pending_step) if pending_step else None,
            "steps": steps_history,
            "artifacts": [{"id": a["id"], "kind": a["kind"], "slide_id": a["slide_id"],
                           "relative_path": a["relative_path"]} for a in artifacts],
        }

    # ---------------------------------------------------------------- 上传

    def add_document(self, owner_id: str, job_id: str, display_name: str, kind: str,
                     data: bytes) -> dict:
        from ..services.resources import ResourceLimitError, check_disk

        try:
            check_disk(self.ctx)
        except ResourceLimitError as exc:
            raise JobError("disk_limit", str(exc), 507)
        job = self._owned_in_upload_phase(owner_id, job_id)
        ext = self._validate_upload_bytes(display_name, data,
                                          ALLOWED_MATERIAL_EXTS if kind == "material" else ALLOWED_TEMPLATE_EXTS)
        # 限额：单任务输入总量
        total = sum(r["byte_size"] for r in self.ctx.records.documents_for_job(job_id))
        if total + len(data) > self.ctx.settings.max_job_input_mb * 1024 * 1024:
            raise JobError("quota", "任务输入总量超限", 413)
        path, sha, size = self.ctx.files.write_stream(job_id, "inputs", display_name, iter([data]))
        doc_id = self.ctx.records.add_document(
            job_id, display_name, kind, ext, sha, size,
            str(path.relative_to(self.ctx.settings.data_dir)))
        if kind == "template":
            new_rev = self._register_template(job, doc_id, display_name, path)
            return {"document_id": doc_id, "file_hash": sha, "byte_size": size,
                    "revision": new_rev}
        return {"document_id": doc_id, "file_hash": sha, "byte_size": size,
                "revision": job["job_revision"]}

    def _register_template(self, job, doc_id: str, display_name: str, path) -> int:
        """模板建档：检查器 + 档案 + 绑定任务；返回新 revision（模板属任务变更）。"""
        from ..templates_engine import build_profile, inspect_template

        try:
            insp = inspect_template(path)
        except ValueError as exc:
            raise JobError("template_invalid", str(exc), 422)
        profile = build_profile(insp, f"job-{job['id']}-{doc_id}", source_file=str(path))
        # POTX 规范化副本
        work_path = path
        if profile.file_ext == ".potx":
            from ..templates_engine.ooxml import normalize_potx_to_pptx

            norm = path.with_name(path.stem + "-norm.pptx")
            normalize_potx_to_pptx(str(path), str(norm))
            work_path = norm
        profile_id = self.ctx.records.save_profile(
            f"job-{job['id']}", 1, profile.file_hash,
            json.loads(profile.model_dump_json()), job_id=job["id"])
        self.ctx.records.set_document_parse_status(doc_id, "ok")
        return self.ctx.jobs.update_state(job["id"], job["job_revision"],
                                          template_profile_id=profile_id)

    # ---------------------------------------------------------------- start / 状态操作

    def start(self, owner_id: str, job_id: str, expected_revision: int,
              request_id: str | None = None) -> dict:
        job = self._owned(owner_id, job_id)
        if request_id:
            cached = self._idempotent(owner_id, f"start:{job_id}", request_id)
            if cached is not None:
                return cached
        if job["status"] != "ready" or job["phase"] != "intake":
            raise JobError("state", f"当前状态 {job['phase']}/{job['status']} 不能启动", 409)
        docs = self.ctx.records.documents_for_job(job_id, kind="material")
        if not docs:
            raise JobError("no_material", "请先上传资料", 422)
        # 模型结构校验（目录有模型时须可用；页面测试按钮负责真实连通性验证）
        from ..services.model_resolver import check_job_model_available

        try:
            check_job_model_available(self.ctx, job["model_profile_id"])
        except RuntimeError as exc:
            raise JobError("model_required", str(exc), 409)
        # 先做乐观锁状态推进，再绑定内置模板（避免内部 +1 引发假冲突）
        try:
            new_rev = self.ctx.jobs.update_state(job_id, expected_revision, status="running")
        except ConflictError as exc:
            raise JobError("revision_conflict", str(exc), 409,
                           {"current_revision": exc.current_revision})
        if not job["template_profile_id"]:
            new_rev = self._ensure_builtin_template(job, base_revision=new_rev)
        step_id = self.ctx.steps.enqueue(job_id, "parse", input_revision=new_rev)
        if request_id:
            self._put_idempotent(owner_id, f"start:{job_id}", request_id,
                                 {"step_id": step_id, "revision": new_rev})
        return {"step_id": step_id, "revision": new_rev}

    def cancel(self, owner_id: str, job_id: str, expected_revision: int) -> dict:
        job = self._owned(owner_id, job_id)
        cur = states.State(job["phase"], job["status"])
        try:
            new = states.transition(cur, "cancel")
        except states.TransitionError as exc:
            raise JobError("state", str(exc), 409)
        try:
            new_rev = self.ctx.jobs.update_state(job_id, expected_revision,
                                                 phase=new.phase, status=new.status)
        except ConflictError as exc:
            raise JobError("revision_conflict", str(exc), 409,
                           {"current_revision": exc.current_revision})
        # pending 步骤作废
        self.ctx.conn.execute(
            "UPDATE steps SET status='cancelled', updated_at=? WHERE job_id=? AND status='pending'",
            (now_iso(), job_id))
        return {"revision": new_rev, "status": new.status}

    def retry_step(self, owner_id: str, job_id: str, step_id: str,
                   expected_revision: int) -> dict:
        """分步重试：指定 failed/interrupted 步骤重新入队；任务回到 running。"""
        job = self._owned(owner_id, job_id)
        cur = states.State(job["phase"], job["status"])
        try:
            states.transition(cur, "retry")
        except states.TransitionError as exc:
            raise JobError("state", str(exc), 409)
        row = self.ctx.conn.execute(
            "SELECT * FROM steps WHERE id=? AND job_id=?", (step_id, job_id)).fetchone()
        if row is None:
            raise JobError("not_found", "步骤不存在", 404)
        if row["status"] not in ("failed", "interrupted", "cancelled"):
            raise JobError("state", f"步骤状态 {row['status']} 不可重试", 409)
        try:
            new_rev = self.ctx.jobs.update_state(job_id, expected_revision, status="running",
                                                 error="")  # 清除上次的失败信息
        except ConflictError as exc:
            raise JobError("revision_conflict", str(exc), 409,
                           {"current_revision": exc.current_revision})
        self.ctx.steps.requeue(row["id"])
        return {"revision": new_rev, "step_id": step_id}

    def retry(self, owner_id: str, job_id: str, expected_revision: int) -> dict:
        job = self._owned(owner_id, job_id)
        cur = states.State(job["phase"], job["status"])
        try:
            states.transition(cur, "retry")
        except states.TransitionError as exc:
            raise JobError("state", str(exc), 409)
        try:
            new_rev = self.ctx.jobs.update_state(job_id, expected_revision, status="running",
                                                 error=None)
        except ConflictError as exc:
            raise JobError("revision_conflict", str(exc), 409,
                           {"current_revision": exc.current_revision})
        # 重新入队最近的失败/中断步骤
        row = self.ctx.conn.execute(
            "SELECT id, kind, input_revision, scope_key, payload_json FROM steps"
            " WHERE job_id=? AND status IN ('failed','interrupted') ORDER BY updated_at DESC LIMIT 1",
            (job_id,)).fetchone()
        if row is None:
            raise JobError("no_retryable", "没有可重试的步骤", 409)
        self.ctx.steps.requeue(row["id"])
        return {"revision": new_rev, "step_id": row["id"]}

    # ---------------------------------------------------------------- 大纲编辑与审核

    def update_outline(self, owner_id: str, job_id: str, pages: list[dict],
                       expected_revision: int) -> dict:
        """用户编辑大纲（增删/排序/改标题）→ 新修订。全局结构变化使后续产物失效。"""
        job = self._owned(owner_id, job_id)
        if job["phase"] != "outline" or job["status"] not in ("awaiting_review", "running", "failed"):
            raise JobError("state", f"当前状态 {job['phase']}/{job['status']} 不能编辑大纲", 409)
        rev_row = self._latest_outline(job_id)
        if rev_row is None:
            raise JobError("no_outline", "大纲尚未生成", 409)
        data = json.loads(rev_row["data_json"])
        brief = data["brief"]
        old_pages = {p["slide_id"]: p for p in data["pages"]}
        new_pages = []
        for p in pages:
            base = old_pages.get(p.get("slide_id"))
            if base is None:
                base = {"purpose": "", "key_message": "", "evidence": [],
                        "visual_intents": ["bullets"], "fact_ids": [], "target_seconds": 45}
            merged = {**base, **p}
            new_pages.append(merged)
        outline = Outline.model_validate({"brief": brief, "pages": new_pages})
        new_rev = self.ctx.records.next_revision(job_id)
        payload = outline.model_dump(mode="json")
        payload["revision"] = new_rev
        self.ctx.records.save_revision(job_id, "outline", payload,
                                       input_revision=job["job_revision"])
        try:
            job_rev = self.ctx.jobs.update_state(job_id, expected_revision,
                                                 phase="outline", status="awaiting_review")
        except ConflictError as exc:
            raise JobError("revision_conflict", str(exc), 409,
                           {"current_revision": exc.current_revision})
        return {"outline_revision": new_rev, "revision": job_rev}

    def review(self, owner_id: str, job_id: str, target_kind: str, decision: str,
               comment: str, expected_revision: int, request_id: str | None = None) -> dict:
        """批准/退回：同一事务验证归属、expected_revision、可审核状态。"""
        if decision not in ("approve", "reject"):
            raise JobError("validation", "decision 必须是 approve/reject", 422)
        if target_kind not in REVIEW_STATE_RULES:
            raise JobError("validation", f"未知审核对象 {target_kind}", 422)
        job = self._owned(owner_id, job_id)
        if request_id:
            cached = self._idempotent(owner_id, f"review:{job_id}", request_id)
            if cached is not None:
                return cached
        if job["job_revision"] != expected_revision:
            raise JobError("revision_conflict", f"版本已变化（当前 {job['job_revision']}）", 409,
                           {"current_revision": job["job_revision"]})
        phase_rule = REVIEW_STATE_RULES[target_kind]
        expect_phase, expect_status = phase_rule[0], phase_rule[1]
        cur = states.State(job["phase"], job["status"])
        if (cur.phase, cur.status) != (expect_phase, expect_status):
            raise JobError("state", f"{target_kind} 审核要求 {expect_phase}/{expect_status}", 409)

        review_id = self.ctx.records.save_review(
            job_id, target_kind, f"rev-{expected_revision}", expected_revision,
            owner_id, decision, comment)
        if review_id is None:
            # 已批准过同一对象：幂等返回，不创建第二个后续步骤
            return {"revision": job["job_revision"], "idempotent": True}

        if decision == "approve":
            if target_kind == "final":
                # 最终批准：发布事务内部原子完成 releases 登记 + completed
                from ..services.release_service import publish

                try:
                    published = publish(self.ctx, owner_id, job_id, expected_revision)
                except RuntimeError as exc:
                    # 发布失败：状态回退 awaiting_review 并报告（不产生半发布）
                    self.ctx.jobs.update_state(job_id, expected_revision, status="awaiting_review",
                                               error=f"发布失败：{exc}")
                    raise JobError("publish_failed", f"发布失败：{exc}", 409)
                result = {"revision": expected_revision + 1, "published": True,
                          "release_id": published["release_id"],
                          "files": published["files"]}
            else:
                event = phase_rule[2]
                bump_batch = None
                if target_kind == "batch":
                    specs = self._current_specs(job)
                    more = self._remaining_batches(job, specs)
                    event = "approve_batch_more" if more else "approve_batch_final"
                    if more:
                        bump_batch = job["batch_index"] + 1
                try:
                    new = states.transition(cur, event)
                except states.TransitionError as exc:
                    raise JobError("state", str(exc), 409)
                new_rev = self.ctx.jobs.update_state(job_id, expected_revision,
                                                     phase=new.phase, status=new.status,
                                                     error="",  # 清除历史失败信息
                                                     batch_index=bump_batch)
                next_step = self._enqueue_after_approve(job_id, target_kind, new, new_rev)
                result = {"revision": new_rev, "next_step": next_step}
        else:
            # 退回：回对应阶段 running，意见已记录，可提 ChangePlan 或编辑
            new = states.transition(cur, "fail") if False else states.State(cur.phase, "running")
            new_rev = self.ctx.jobs.update_state(job_id, expected_revision,
                                                 phase=new.phase, status=new.status)
            result = {"revision": new_rev, "rejected": True, "comment": comment}
        if request_id:
            self._put_idempotent(owner_id, f"review:{job_id}", request_id, result)
        return result

    def _enqueue_after_approve(self, job_id: str, target_kind: str,
                               new: states.State, new_rev: int) -> str | None:
        if new.phase == "sample":
            # 大纲批准后先生成逐页规格（SlideSpec），完成后再渲染样张
            return self.ctx.steps.enqueue(job_id, "generate_specs", input_revision=new_rev,
                                          scope_key="sample",
                                          payload={"scope_key": "sample"})
        if new.phase == "batch":
            return self.ctx.steps.enqueue(job_id, "render_batch", input_revision=new_rev,
                                          scope_key=f"batch-{self.ctx.jobs.get(job_id)['batch_index']}",
                                          payload={"scope_key": f"batch-{self.ctx.jobs.get(job_id)['batch_index']}"})
        if new.phase == "final":
            return self.ctx.steps.enqueue(job_id, "assemble", input_revision=new_rev)
        return None

    def _remaining_batches(self, job, specs: list[SlideSpec]) -> int:
        """批准当前批后是否仍有下一批（每批 5 页）。"""
        batch_size = 5
        total = len(specs)
        next_start = (job["batch_index"] + 1) * batch_size
        return 1 if next_start < total else 0

    # ---------------------------------------------------------------- 变更计划

    def plan_change(self, owner_id: str, job_id: str, instruction: str) -> dict:
        """解释修改范围（需要模型可用）。"""
        job = self._owned(owner_id, job_id)
        provider = self._provider()
        outline_row = self._latest_outline(job_id)
        if outline_row is None:
            raise JobError("no_outline", "大纲尚未生成", 409)
        outline = Outline.model_validate(json.loads(outline_row["data_json"]))
        from ..planning.slides import plan_change as _plan_change

        plan = _plan_change(provider, instruction, outline,
                            target_revision=job["job_revision"])
        change_id = self.ctx.records.save_change_request(job_id, plan.model_dump())
        plan.change_id = change_id
        return plan.model_dump()

    def apply_change(self, owner_id: str, job_id: str, change_id: str,
                     expected_revision: int, provider) -> dict:
        """确认执行变更计划：重新生成受影响页并渲染。"""
        job = self._owned(owner_id, job_id)
        row = self.ctx.conn.execute(
            "SELECT * FROM change_requests WHERE id=? AND job_id=?", (change_id, job_id)).fetchone()
        if row is None or row["status"] != "proposed":
            raise JobError("not_found", "变更计划不存在或已处理", 404)
        plan = ChangePlan.model_validate(json.loads(row["plan_json"]))
        specs = self._current_specs(job)
        known = {s.slide_id for s in specs}
        from ..planning.slides import validate_change_plan

        problems = validate_change_plan(plan, known)
        if problems:
            raise JobError("plan_invalid", "；".join(problems), 422)
        # 全局影响：重新走样张审核
        if plan.scope == "global":
            self.ctx.jobs.update_state(job_id, expected_revision,
                                       phase="sample", status="running")
            self.ctx.conn.execute(
                "UPDATE change_requests SET status='approved' WHERE id=?", (change_id,))
            step_id = self.ctx.steps.enqueue(job_id, "render_batch", input_revision=job["job_revision"],
                                             scope_key="sample", payload={"scope_key": "sample"})
            return {"revision": job["job_revision"], "step_id": step_id, "scope": "global"}
        # 页面级：入队重新生成（由模型步骤处理；无模型时报错）
        if not self.ctx.settings.model_available:
            raise JobError("model_required", "页面内容重新生成需要模型可用", 409)
        self.ctx.conn.execute("UPDATE change_requests SET status='approved' WHERE id=?", (change_id,))
        step_id = self.ctx.steps.enqueue(
            job_id, "regenerate_pages", input_revision=job["job_revision"],
            scope_key=change_id,
            payload={"change_id": change_id, "page_ids": [pid for it in plan.items for pid in it.page_ids]})
        return {"revision": job["job_revision"], "step_id": step_id, "scope": "page"}

    # ---------------------------------------------------------------- 产物下载

    def artifact_response(self, owner_id: str, job_id: str, artifact_id: str):
        """鉴权下载：归属校验 + 相对路径解析（拒绝逃逸）。"""
        job = self._owned(owner_id, job_id)
        row = self.ctx.conn.execute(
            "SELECT * FROM artifacts WHERE id=? AND job_id=?", (artifact_id, job_id)).fetchone()
        if row is None:
            raise JobError("not_found", "产物不存在", 404)
        try:
            path = self.ctx.files.resolve(row["relative_path"])
        except ValueError as exc:
            raise JobError("path", str(exc), 400)
        if not path.exists():
            raise JobError("not_found", "产物文件缺失", 404)
        return path, row["kind"]

    # ---------------------------------------------------------------- 内部工具

    def _owned(self, owner_id: str, job_id: str):
        job = self.ctx.jobs.get_owned(job_id, owner_id)
        if job is None:
            raise JobError("not_found", "任务不存在", 404)
        return job

    def _owned_in_upload_phase(self, owner_id: str, job_id: str):
        job = self._owned(owner_id, job_id)
        if job["phase"] != "intake" or job["status"] not in ("ready", "needs_input"):
            raise JobError("state", "当前阶段不允许上传；请创建新任务或新修订", 409)
        return job

    def _provider(self, job_id: str | None = None):
        """任务级模型解析：job 绑定模型 → 默认档回退。"""
        from ..services.model_resolver import resolve_job_provider

        model_profile_id = None
        if job_id:
            job = self.ctx.jobs.get(job_id)
            model_profile_id = job["model_profile_id"] if job else None
        try:
            return resolve_job_provider(self.ctx, model_profile_id)
        except RuntimeError as exc:
            raise JobError("model_required", str(exc), 409)

    def _current_specs(self, job) -> list[SlideSpec]:
        row = self.ctx.conn.execute(
            "SELECT data_json FROM revisions WHERE job_id=? AND kind='specs' ORDER BY revision DESC LIMIT 1",
            (job["id"],)).fetchone()
        if row is None:
            return []
        data = json.loads(row["data_json"])
        return [SlideSpec.model_validate(s) for s in data.get("specs", [])]

    def _latest_outline(self, job_id: str):
        return self.ctx.conn.execute(
            "SELECT * FROM revisions WHERE job_id=? AND kind='outline' ORDER BY revision DESC LIMIT 1",
            (job_id,)).fetchone()

    def _scene(self, scene_id: str, version: int):
        from ..planning.scenarios import get_scene

        try:
            return get_scene(scene_id, version)
        except KeyError:
            raise JobError("validation", f"场景不存在 {scene_id}", 422)

    def _validate_brief(self, data: dict) -> Brief:
        try:
            return Brief.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            raise JobError("validation", f"需求校验失败：{exc}", 422)

    def _validate_upload_bytes(self, filename: str, data: bytes, allowed: set[str]) -> str:
        from ..services.uploads import UploadError, validate_upload

        ext_map = {ext: "" for ext in allowed}
        try:
            return validate_upload(filename, data, allowed_exts=ext_map,
                                   max_bytes=self.ctx.settings.max_upload_mb * 1024 * 1024)
        except UploadError as exc:
            raise JobError("upload_invalid", str(exc), 422)

    def _ensure_builtin_template(self, job, base_revision: int) -> int:
        """未上传模板时绑定内置模板；返回绑定后的最新 revision。"""
        from ..templates_engine import build_profile, inspect_template

        builtin = self.ctx.settings.data_dir / "templates" / "builtin-classic.pptx"
        if not builtin.exists():
            raise JobError("template_missing", "内置模板缺失，请先运行 scripts/build_builtin_templates.py", 409)
        profile = build_profile(inspect_template(builtin), "builtin-classic")
        profile_id = self.ctx.records.save_profile(
            "builtin-classic", 1, profile.file_hash, json.loads(profile.model_dump_json()))
        return self.ctx.jobs.update_state(job["id"], base_revision,
                                          template_profile_id=profile_id)

    def _idempotent(self, owner_id: str, action: str, request_id: str):
        key = f"{owner_id}:{action}:{request_id}"
        row = self.ctx.conn.execute("SELECT result_json FROM idempotency WHERE key=?", (key,)).fetchone()
        return json.loads(row["result_json"]) if row else None

    def _put_idempotent(self, owner_id: str, action: str, request_id: str, result: dict) -> None:
        key = f"{owner_id}:{action}:{request_id}"
        self.ctx.conn.execute(
            "INSERT OR REPLACE INTO idempotency (key, result_json) VALUES (?,?)",
            (key, json.dumps(result, ensure_ascii=False)))
