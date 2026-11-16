"""业务 API：任务、上传、审核、变更、产物下载、事件轮询。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...services.jobs_service import JobError, JobsService
from ..deps import get_ctx, get_current_user, require_same_origin
from ...services.context import WebContext


class CreateJobRequest(BaseModel):
    title: str
    scene_id: str = "general"
    scene_version: int = 1
    brief: dict | None = None
    model_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None


class ReviewRequest(BaseModel):
    target_kind: str
    decision: str
    comment: str = ""
    expected_revision: int
    request_id: str | None = None


class OutlineUpdateRequest(BaseModel):
    pages: list[dict]
    expected_revision: int


class PlanChangeRequest(BaseModel):
    instruction: str


class ModelUpsertRequest(BaseModel):
    name: str
    base_url: str = ""
    model_name: str
    api_key: str = ""  # 编辑时留空 = 沿用原密钥
    max_tokens: int | None = None  # 单次输出预算；推理模型建议 8000+


class ApplyChangeRequest(BaseModel):
    expected_revision: int


class ActionRequest(BaseModel):
    expected_revision: int
    request_id: str | None = None


def svc(ctx: WebContext) -> JobsService:
    return JobsService(ctx)


def _job_error(exc: JobError, request_id: str) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"error": {"code": exc.code, "message": exc.message,
                          "details": exc.details, "request_id": request_id}})


def register_api_routes(app) -> None:
    router = APIRouter(prefix="/api")

    @router.get("/scenarios")
    def scenarios(user=Depends(get_current_user)):
        from ...planning.scenarios import load_scenarios

        return {"items": load_scenarios()}

    @router.get("/models")
    def models(user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        """可用模型列表（不含任何密钥）。"""
        from ...services.models import load_catalog

        catalog = load_catalog(ctx.settings.data_dir, ctx.settings)
        return {"items": catalog.public_list(), "default": catalog.default_id,
                "source": catalog.source}

    @router.post("/models/{model_id}/test")
    def test_model(model_id: str, request: Request,
                   user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        """连通性测试：最小真实调用，验证网络/密钥/模型名/JSON 协议。结果 200 返回。"""
        from ...providers.base import test_model_connectivity
        from ...services.models import load_catalog

        catalog = load_catalog(ctx.settings.data_dir, ctx.settings)
        entry = catalog.get(model_id)
        if entry is None:
            raise _job_error(JobError("not_found", f"模型配置不存在：{model_id}", 404),
                             request.state.request_id)
        if not entry.available:
            return {"ok": False, "error": f"配置不可用：{entry.unavailable_reason}", "model": entry.name}
        try:
            provider = catalog.build_provider(model_id, timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "model": entry.name}
        result = test_model_connectivity(provider, timeout_note="测试超时 60s")
        result["model"] = entry.name
        result["model_id"] = entry.id
        return result

    @router.post("/models", status_code=201)
    def add_model_route(body: ModelUpsertRequest, request: Request,
                        user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        """页面添加模型配置（密钥保存于服务端 data/config/models.json）。"""
        require_same_origin(request)
        from ...services.models import add_model

        try:
            entry = add_model(ctx.settings.data_dir, body.name, body.base_url,
                              body.model_name, body.api_key, max_tokens=body.max_tokens)
        except ValueError as exc:
            raise _job_error(JobError("validation", str(exc), 422), request.state.request_id)
        return {"model": entry.public()}

    @router.put("/models/{model_id}")
    def update_model_route(model_id: str, body: ModelUpsertRequest, request: Request,
                           user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        from ...services.models import update_model

        try:
            entry = update_model(ctx.settings.data_dir, model_id, body.name,
                                 body.base_url, body.model_name, body.api_key,
                                 max_tokens=body.max_tokens)
        except ValueError as exc:
            raise _job_error(JobError("not_found", str(exc), 404), request.state.request_id)
        return {"model": entry.public()}

    @router.delete("/models/{model_id}")
    def delete_model_route(model_id: str, request: Request,
                           user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        from ...services.models import delete_model

        if not delete_model(ctx.settings.data_dir, model_id):
            raise _job_error(JobError("not_found", f"模型不存在：{model_id}", 404),
                             request.state.request_id)
        return {"ok": True}

    @router.get("/templates")
    def templates(user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        builtin = ctx.settings.data_dir / "templates" / "builtin-classic.pptx"
        items = []
        if builtin.exists():
            items.append({"id": "builtin-classic", "name": "内置经典模板", "source": "builtin"})
        items.append({"id": "user-upload", "name": "上传模板（PPTX/POTX）", "source": "upload"})
        return {"items": items}

    @router.post("/jobs", status_code=201)
    def create_job(body: CreateJobRequest, request: Request,
                   user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        try:
            return svc(ctx).create_job(user["id"], body.title, body.scene_id,
                                       body.scene_version, body.brief, body.request_id,
                                       body.model_id)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.get("/jobs")
    def list_jobs(user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        return {"items": svc(ctx).list_jobs(user["id"])}

    @router.get("/jobs/{job_id}")
    def job_view(job_id: str, request: Request,
                 user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        try:
            return svc(ctx).job_view(user["id"], job_id)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.post("/jobs/{job_id}/documents", status_code=201)
    async def upload_document(job_id: str, request: Request, file: UploadFile = File(...),
                              user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        try:
            data = await file.read()
            return svc(ctx).add_document(user["id"], job_id, file.filename or "upload.bin",
                                         "material", data)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.post("/jobs/{job_id}/template", status_code=201)
    async def upload_template(job_id: str, request: Request, file: UploadFile = File(...),
                              user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        try:
            data = await file.read()
            return svc(ctx).add_document(user["id"], job_id, file.filename or "template.pptx",
                                         "template", data)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.post("/jobs/{job_id}/start")
    def start(job_id: str, body: ActionRequest, request: Request,
              user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        try:
            return svc(ctx).start(user["id"], job_id, body.expected_revision, body.request_id)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.put("/jobs/{job_id}/outline")
    def update_outline(job_id: str, body: OutlineUpdateRequest, request: Request,
                       user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        try:
            return svc(ctx).update_outline(user["id"], job_id, body.pages, body.expected_revision)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.post("/jobs/{job_id}/reviews")
    def review(job_id: str, body: ReviewRequest, request: Request,
               user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        try:
            return svc(ctx).review(user["id"], job_id, body.target_kind, body.decision,
                                   body.comment, body.expected_revision, body.request_id)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.post("/jobs/{job_id}/changes/plan")
    def plan_change(job_id: str, body: PlanChangeRequest, request: Request,
                    user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        try:
            return svc(ctx).plan_change(user["id"], job_id, body.instruction)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.post("/jobs/{job_id}/changes/{change_id}/apply")
    def apply_change(job_id: str, change_id: str, body: ApplyChangeRequest, request: Request,
                     user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        try:
            service = svc(ctx)
            provider = service._provider()
            return service.apply_change(user["id"], job_id, change_id,
                                        body.expected_revision, provider)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.post("/jobs/{job_id}/steps/{step_id}/retry")
    def retry_step(job_id: str, step_id: str, body: ActionRequest, request: Request,
                   user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        try:
            return svc(ctx).retry_step(user["id"], job_id, step_id, body.expected_revision)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.post("/jobs/{job_id}/retry")
    def retry(job_id: str, body: ActionRequest, request: Request,
              user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        try:
            return svc(ctx).retry(user["id"], job_id, body.expected_revision)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str, body: ActionRequest, request: Request,
               user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        try:
            return svc(ctx).cancel(user["id"], job_id, body.expected_revision)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)

    @router.get("/jobs/{job_id}/artifacts/{artifact_id}")
    def download(job_id: str, artifact_id: str, request: Request,
                 user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        try:
            path, kind = svc(ctx).artifact_response(user["id"], job_id, artifact_id)
        except JobError as exc:
            raise _job_error(exc, request.state.request_id)
        media = {"pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                 "pdf": "application/pdf", "notes": "text/markdown",
                 "thumbnail": "image/png", "preview": "image/png"}.get(kind, "application/octet-stream")
        return FileResponse(path, media_type=media, filename=path.name)

    @router.get("/jobs/{job_id}/events")
    def events(job_id: str, request: Request, after: int = 0,
               user=Depends(get_current_user), ctx: WebContext = Depends(get_ctx)):
        """轻量事件轮询：seq 为单调递增整数（用步骤/审核记录 id 序模拟）。"""
        job = ctx.jobs.get_owned(job_id, user["id"])
        if job is None:
            raise _job_error(JobError("not_found", "任务不存在", 404), request.state.request_id)
        items = []
        seq = after
        for r in ctx.conn.execute(
                "SELECT id, kind, status, updated_at FROM steps WHERE job_id=? ORDER BY created_at",
                (job_id,)):
            seq += 1
            items.append({"seq": seq, "kind": f"step:{r['kind']}", "status": r["status"],
                          "at": r["updated_at"]})
        for r in ctx.conn.execute(
                "SELECT id, target_kind, decision, created_at FROM reviews WHERE job_id=? ORDER BY created_at",
                (job_id,)):
            seq += 1
            items.append({"seq": seq, "kind": f"review:{r['target_kind']}",
                          "status": r["decision"], "at": r["created_at"]})
        return {"events": [e for e in items if e["seq"] > after], "job_revision": job["job_revision"],
                "phase": job["phase"], "status": job["status"]}

    app.include_router(router)

    from .auth import register_auth_routes

    register_auth_routes(app)
