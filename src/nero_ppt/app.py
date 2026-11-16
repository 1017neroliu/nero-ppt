"""FastAPI 应用工厂与依赖装配。"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .settings import Settings, load_settings


class RequestContextMiddleware(BaseHTTPMiddleware):
    """每个请求注入 request_id 并写访问日志。"""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logging.getLogger("nero.access").exception(
                "request failed path=%s request_id=%s", request.url.path, request_id
            )
            response = JSONResponse(
                status_code=500,
                content={"error": {"code": "internal", "message": "内部错误", "request_id": request_id}},
            )
        response.headers["X-Request-ID"] = request_id
        # HTML/JSON 禁止浏览器启发式缓存（版本错位会造成"按钮没反应/数据过期"）；
        # 静态资源允许 ETag 304 验证（URL 已带内容指纹）
        response.headers["Cache-Control"] = (
            "no-cache" if request.url.path.startswith("/static") else "no-store")
        logging.getLogger("nero.access").info(
            "%s %s -> %s (%dms) request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.monotonic() - start) * 1000,
            request_id,
        )
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    from .services.context import build_context

    ctx = build_context(settings)

    app = FastAPI(title="Nero PPT", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.ctx = ctx
    app.add_middleware(RequestContextMiddleware)

    # 健康接口（无鉴权）
    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    # 注册业务路由（S12/S13 起逐步接入）
    from .web.routes import register_routes

    register_routes(app)

    # 静态资源：仅 web/static 目录；data 目录永不公开
    from fastapi.staticfiles import StaticFiles
    from pathlib import Path

    static_dir = Path(__file__).resolve().parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


def get_settings(request: Request) -> Settings:
    return request.app.state.ctx.settings


RequestSettingsDep = Callable[..., Settings]
