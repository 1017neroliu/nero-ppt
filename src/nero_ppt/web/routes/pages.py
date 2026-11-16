"""登录与页面路由：新建任务 / 工作台。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...services.access import SESSION_COOKIE
from ...services.context import WebContext
from ..deps import get_ctx, get_current_user

import hashlib
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _static_version() -> str:
    """静态资源指纹：任一文件变更即变，彻底避免浏览器缓存旧 JS/CSS。"""
    parts = []
    for f in sorted(STATIC_DIR.glob("*")):
        if f.is_file():
            parts.append(f"{f.name}:{f.stat().st_mtime_ns}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:10]


templates = None


def _tpl():
    global templates
    if templates is None:
        from fastapi.templating import Jinja2Templates

        templates = Jinja2Templates(
            directory=str(Path(__file__).resolve().parent.parent / "templates"))
        # 显式开启自动转义（防 XSS）；静态资源带版本指纹防缓存
        templates.env.autoescape = True
        templates.env.globals["static_v"] = _static_version
    return templates


def register_page_routes(app) -> None:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request, ctx: WebContext = Depends(get_ctx)):
        if ctx.settings.auth_mode == "password":
            user = ctx.access.session_user(request.cookies.get(SESSION_COOKIE))
            if user is None:
                return _tpl().TemplateResponse(request, "login.html", {"title": "登录 · Nero PPT"})
        return RedirectResponse("/new")

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return _tpl().TemplateResponse(request, "login.html", {"title": "登录 · Nero PPT"})

    @router.get("/new", response_class=HTMLResponse)
    def new_job(request: Request, user=Depends(get_current_user),
                ctx: WebContext = Depends(get_ctx)):
        return _tpl().TemplateResponse(request, "new_job.html",
                                       {"title": "新建任务 · Nero PPT",
                                        "auth_demo": ctx.settings.auth_mode == "demo",
                                        "max_upload_mb": ctx.settings.max_upload_mb})

    @router.get("/jobs/{job_id}", response_class=HTMLResponse)
    def workbench(job_id: str, request: Request, user=Depends(get_current_user),
                  ctx: WebContext = Depends(get_ctx)):
        return _tpl().TemplateResponse(request, "workbench.html",
                                       {"title": "工作台 · Nero PPT", "job_id": job_id,
                                        "auth_demo": ctx.settings.auth_mode == "demo"})

    app.include_router(router)
