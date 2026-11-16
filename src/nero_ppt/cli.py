"""CLI：doctor / serve / 用户管理 / 离线生成 / worker。

S01 提供 doctor 与 serve；user/worker/offline 子命令在后续阶段实现后接入。
"""
from __future__ import annotations

import json
import shutil
import socket
import sys
from pathlib import Path

import click


def _check_python() -> dict:
    v = sys.version_info
    ok = v >= (3, 10)
    return {"component": "python", "version": f"{v.major}.{v.minor}.{v.micro}", "ok": ok,
            "note": "" if ok else "需要 Python >= 3.10"}


def _check_soffice() -> dict:
    path = shutil.which("soffice")
    version = ""
    if path:
        import subprocess

        try:
            out = subprocess.run(["soffice", "--version"], capture_output=True, text=True, timeout=30)
            version = (out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else ""
        except Exception as exc:  # noqa: BLE001
            version = f"error: {exc}"
    return {"component": "libreoffice", "version": version, "ok": bool(path),
            "note": "" if path else "未找到 soffice，PDF 转换不可用"}


def _check_poppler() -> dict:
    pp = shutil.which("pdftoppm")
    pi = shutil.which("pdfinfo")
    version = ""
    if pp:
        import subprocess

        try:
            out = subprocess.run([pp, "-v"], capture_output=True, text=True, timeout=10)
            version = (out.stderr or out.stdout).splitlines()[0]
        except Exception:  # noqa: BLE001
            pass
    ok = bool(pp and pi)
    return {"component": "poppler", "version": version, "ok": ok,
            "note": "" if ok else "需要 pdftoppm/pdfinfo 生成缩略图"}


def _check_fonts() -> dict:
    if not shutil.which("fc-list"):
        return {"component": "fonts-zh", "version": "", "ok": False, "note": "fc-list 不可用"}
    import subprocess

    out = subprocess.run(["fc-list", ":lang=zh", "family"], capture_output=True, text=True, timeout=10)
    families = sorted({line.strip() for line in out.stdout.splitlines() if line.strip()})
    ok = len(families) > 0
    return {"component": "fonts-zh", "version": f"{len(families)} families", "ok": ok,
            "note": "" if ok else "未发现中文字体，渲染中文会缺字"}


def _check_python_deps() -> list[dict]:
    results = []
    for mod, min_ver in [("fastapi", "0.111"), ("pydantic", "2.10"), ("pptx", "0.6.23"),
                         ("docx", "0"), ("pypdf", "4.2"), ("openai", "1.40"), ("jinja2", "3.1")]:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "")
            results.append({"component": f"py:{mod}", "version": ver, "ok": True, "note": ""})
        except ImportError as exc:
            results.append({"component": f"py:{mod}", "version": "", "ok": False, "note": str(exc)})
    return results


def _check_model(settings) -> dict:
    if settings.model_available:
        return {"component": "model", "version": f"{settings.model_provider}:{settings.model_name}",
                "ok": True, "note": "已配置 API key（真实可用性需实际调用验证）"}
    return {"component": "model", "version": "", "ok": False,
            "note": "未配置 NERO_MODEL_API_KEY/NERO_MODEL_NAME，模型功能显式不可用（FakeProvider 测试仍可运行）"}


def _check_disk(settings) -> dict:
    from pathlib import Path

    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(settings.data_dir)
    free_mb = usage.free // (1024 * 1024)
    ok = free_mb >= settings.min_free_disk_mb
    return {"component": "disk", "version": f"{free_mb}MB free", "ok": ok,
            "note": "" if ok else f"低于阈值 {settings.min_free_disk_mb}MB，新渲染将被暂停"}


@click.group()
@click.option("--env-file", default=None, help=".env 文件路径")
@click.pass_context
def main(ctx: click.Context, env_file: str | None) -> None:
    """Nero PPT 命令行。"""
    ctx.obj = {"env_file": env_file}


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """环境能力体检：显示真实可用组件与缺失项。"""
    from .settings import load_settings

    settings = load_settings(ctx.obj["env_file"])
    checks = [
        _check_python(),
        _check_soffice(),
        _check_poppler(),
        _check_fonts(),
        *_check_python_deps(),
        _check_model(settings),
        _check_disk(settings),
    ]
    all_ok = all(c["ok"] for c in checks)
    click.echo(json.dumps({"all_ok": all_ok, "checks": checks}, ensure_ascii=False, indent=2))
    sys.exit(0 if all_ok else 1)


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8630, show_default=True, type=int)
@click.pass_context
def serve(ctx: click.Context, host: str, port: int) -> None:
    """启动 Web 应用（开发模式）。demo 鉴权只允许绑定回环地址。"""
    import uvicorn

    from .settings import load_settings

    settings = load_settings(ctx.obj["env_file"])
    if settings.auth_mode == "demo" and host not in ("127.0.0.1", "localhost", "::1"):
        raise click.ClickException("demo 模式只能绑定回环地址；线上部署请使用 password 模式")
    uvicorn.run("nero_ppt.app:create_app", host=host, port=port, factory=True, log_level="info")


@main.command("user-create")
@click.argument("username")
@click.option("--env-file", default=None)
def user_create(env_file: str | None, username: str) -> None:
    """创建用户并打印一次性凭据（仅显示一次）。"""
    from .db import connection as dbconn
    from .settings import load_settings
    from .services.access import AccessService

    settings = load_settings(env_file)
    settings.ensure_dirs()
    conn = dbconn.connect(settings.db_path)
    dbconn.migrate(conn)
    access = AccessService(conn)
    try:
        password = access.create_user(username)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    click.echo(f"用户创建成功：{username}")
    click.echo(f"访问凭据（仅显示一次，请立即保存）：{password}")


@main.command()
@click.option("--poll-interval", default=2.0, show_default=True, type=float)
@click.option("--env-file", default=None)
def worker(poll_interval: float, env_file: str | None) -> None:
    """启动后台 Worker（单实例串行处理）。"""
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from .services.context import build_context
    from .settings import load_settings
    from .workflow import Worker

    ctx = build_context(load_settings(env_file))
    w = Worker(ctx, poll_interval=poll_interval)
    click.echo(f"Worker 启动：{w.worker_id}")
    w.run_forever()


@main.command()
@click.option("--env-file", default=None)
def recover(env_file: str | None) -> None:
    """把残留 running 步骤标记 interrupted（人工运维入口）。"""
    from .services.context import build_context
    from .settings import load_settings

    ctx = build_context(load_settings(env_file))
    from .workflow.runner import Worker

    n = Worker(ctx).recover()
    click.echo(f"已标记 {n} 个 interrupted")


@main.command()
@click.option("--dry-run", is_flag=True, help="仅列出将清理的文件")
@click.option("--max-age-hours", default=24.0, show_default=True, type=float)
@click.option("--env-file", default=None)
def cleanup(dry_run: bool, max_age_hours: float, env_file: str | None) -> None:
    """清理无引用的临时文件（不影响当前发布与审核引用）。"""
    import json as _json

    from .services.context import build_context
    from .services.resources import cleanup_temp
    from .settings import load_settings

    ctx = build_context(load_settings(env_file))
    report = cleanup_temp(ctx, dry_run=dry_run, max_age_hours=max_age_hours)
    click.echo(_json.dumps(report, ensure_ascii=False, indent=2))


@main.command()
@click.option("--env-file", default=None)
def serve_all(ctx_obj_env_file: str | None = None) -> None:
    """开发便利：同进程启动 Web（前台）与提示单独开 Worker。生产建议两个进程。"""
    raise click.ClickException("请分别运行 `serve` 与 `worker` 两个进程（见 README）")


if __name__ == "__main__":
    main()
