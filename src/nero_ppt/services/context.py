"""应用上下文：数据库、服务与仓储装配（挂在 app.state）。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..db import connection as dbconn
from ..repositories import FileStore, JobRepo, RecordRepo, StepRepo
from ..services.access import SESSION_COOKIE, AccessService
from ..settings import Settings


@dataclass
class WebContext:
    settings: Settings
    conn: sqlite3.Connection
    access: AccessService
    jobs: JobRepo
    steps: StepRepo
    records: RecordRepo
    files: FileStore


def _deploy_builtin_templates(data_dir) -> None:
    """内置模板自动部署到 data/templates（哈希校验，缺失或损坏时重新复制）。"""
    import hashlib
    import shutil
    from pathlib import Path

    assets = Path(__file__).resolve().parents[2] / "assets" / "templates"
    target = Path(data_dir) / "templates"
    target.mkdir(parents=True, exist_ok=True)
    if not assets.exists():
        return
    for src in sorted(assets.glob("*.pptx")):
        dst = target / src.name
        need = True
        if dst.exists():
            h = hashlib.sha256(dst.read_bytes()).hexdigest()
            need = h != hashlib.sha256(src.read_bytes()).hexdigest()
        if need:
            shutil.copyfile(src, dst)


def build_context(settings: Settings) -> WebContext:
    settings.ensure_dirs()
    _deploy_builtin_templates(settings.data_dir)
    conn = dbconn.connect(settings.db_path)
    dbconn.migrate(conn)
    access = AccessService(conn, secure_cookies=False)  # HTTPS 部署时由部署层设置反代头并开启
    ctx = WebContext(
        settings=settings,
        conn=conn,
        access=access,
        jobs=JobRepo(conn),
        steps=StepRepo(conn),
        records=RecordRepo(conn),
        files=FileStore(settings.data_dir),
    )
    if settings.auth_mode == "demo":
        _ensure_demo_user(ctx)
    return ctx


def _ensure_demo_user(ctx: WebContext) -> None:
    """demo 模式：确保 demo 用户存在（仅回环部署使用）。"""
    if ctx.access.get_user_by_name("demo") is None:
        ctx.access.create_user("demo")


__all__ = ["SESSION_COOKIE", "WebContext", "build_context"]
