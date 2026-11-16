"""Web 路由注册入口。

S01 阶段只有健康接口；页面与业务 API 在 S12/S13 逐步接入后在此注册。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_routes(app: "FastAPI") -> None:
    from .api import register_api_routes
    from .pages import register_page_routes

    register_page_routes(app)
    register_api_routes(app)
