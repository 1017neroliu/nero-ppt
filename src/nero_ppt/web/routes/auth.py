"""鉴权路由：登录 / 注销 / 当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ...services.access import SESSION_COOKIE
from ...services.context import WebContext
from ..deps import get_ctx, get_current_user, require_same_origin


class LoginRequest(BaseModel):
    username: str
    password: str


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "-"


def register_auth_routes(app) -> None:
    router = APIRouter()

    @router.post("/auth/login")
    def login(body: LoginRequest, request: Request, response: Response,
              ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        if ctx.settings.auth_mode == "demo":
            raise HTTPException(status_code=400, detail="demo 模式无需登录")
        session_id = ctx.access.login(body.username, body.password, ip=client_ip(request))
        if session_id is None:
            # 统一 401，不区分"用户不存在/密码错误/限流"，避免用户枚举
            raise HTTPException(status_code=401, detail="登录失败")
        response.set_cookie(
            SESSION_COOKIE, session_id,
            httponly=True, samesite="lax",
            secure=ctx.settings.auth_mode == "password" and request.url.scheme == "https",
            max_age=12 * 3600, path="/",
        )
        user = ctx.access.session_user(session_id)
        return {"user": {"id": user["id"], "username": user["username"]}}

    @router.post("/auth/logout")
    def logout(request: Request, response: Response,
               ctx: WebContext = Depends(get_ctx)):
        require_same_origin(request)
        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id:
            ctx.access.logout(session_id)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @router.get("/api/me")
    def me(user=Depends(get_current_user)):
        return {"id": user["id"], "username": user["username"]}

    app.include_router(router)
