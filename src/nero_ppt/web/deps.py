"""请求级依赖：当前用户（授权证据）与上下文。

请求路径中的 ID 只用于定位；授权一律基于 current_user + 归属检查。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from ..services.access import SESSION_COOKIE
from ..services.context import WebContext


def get_ctx(request: Request) -> WebContext:
    return request.app.state.ctx


def get_current_user(request: Request, ctx: WebContext = Depends(get_ctx)):
    """password 模式：会话 cookie；demo 模式：自动绑定 demo 用户。"""
    if ctx.settings.auth_mode == "demo":
        user = ctx.access.get_user_by_name("demo")
        if user is None:
            raise HTTPException(status_code=500, detail="demo 用户缺失")
        return user
    session_id = request.cookies.get(SESSION_COOKIE)
    user = ctx.access.session_user(session_id)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return user


def require_same_origin(request: Request) -> None:
    """状态修改请求的 CSRF/Origin 检查。"""
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    from ..services.access import check_same_origin

    if not check_same_origin(origin, host):
        raise HTTPException(status_code=403, detail="Origin 校验失败")
