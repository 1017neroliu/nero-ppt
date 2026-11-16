"""S15 集成测试：磁盘阈值、临时清理、启动脚本约定。"""
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nero_ppt.app import create_app
from nero_ppt.services.resources import ResourceLimitError, check_disk, cleanup_temp
from nero_ppt.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "assets" / "templates" / "builtin-classic.pptx"


@pytest.fixture()
def app(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "nero.db",
                        auth_mode="demo", session_secret="s" * 32,
                        min_free_disk_mb=1024 * 1024,  # 极大阈值，制造磁盘不足
                        model_api_key="", model_name="")
    application = create_app(settings)
    return application


def test_disk_threshold_blocks_upload(app, tmp_path):
    """磁盘低于阈值 → 新上传 507，错误信息说明已有任务状态。"""
    client = TestClient(app)
    r = client.post("/api/jobs", json={"title": "t", "scene_id": "general"},
                    headers={"origin": "http://testserver"})
    job_id = r.json()["job_id"]
    r = client.post(f"/api/jobs/{job_id}/documents",
                    files={"file": ("a.txt", io.BytesIO(b"data"), "text/plain")},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 507
    assert "低于阈值" in r.json()["detail"]["error"]["message"]


import io  # noqa: E402


def test_disk_threshold_blocks_render(tmp_path):
    """渲染前磁盘检查：低于阈值时渲染步骤显式失败（不产生半成品）。"""
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "nero.db",
                        auth_mode="demo", session_secret="s" * 32,
                        min_free_disk_mb=1024 * 1024, model_api_key="", model_name="")
    from nero_ppt.services.context import build_context
    from nero_ppt.workflow.handlers import StepContext

    ctx = build_context(settings)
    ctx.conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1','o','x','now')")
    job_id = ctx.jobs.create("u1", "t", "general", 1)
    step_id = ctx.steps.enqueue(job_id, "render_batch", input_revision=1)
    row = ctx.steps.get(step_id)
    step = StepContext(ctx=ctx, job_id=job_id, step_id=step_id, input_revision=1, payload={})
    from nero_ppt.services.render_service import render_batch_impl

    with pytest.raises(RuntimeError) as e:
        render_batch_impl(step)
    assert "低于阈值" in str(e.value)


def test_cleanup_removes_stale_tmp_and_parts(tmp_path):
    """清理：旧 tmp 目录与 .part 删除；新文件保留；dry-run 不删除。"""
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "nero.db",
                        auth_mode="demo", session_secret="s" * 32)
    from nero_ppt.services.context import build_context

    ctx = build_context(settings)
    stale = ctx.settings.data_dir / "tmp" / "render-old"
    stale.mkdir(parents=True)
    (stale / "work.pdf").write_bytes(b"x" * 100)
    old_ts = time.time() - 48 * 3600
    os.utime(stale, (old_ts, old_ts))
    fresh = ctx.settings.data_dir / "tmp" / "render-new"
    fresh.mkdir(parents=True)
    part = ctx.settings.data_dir / "jobs" / "j1" / "inputs" / "f.pdf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"p")
    # dry-run：不删除
    report = cleanup_temp(ctx, dry_run=True)
    assert report["dry_run"] and stale.exists() and part.exists()
    # 实际清理
    report = cleanup_temp(ctx)
    assert not stale.exists() and not part.exists()
    assert fresh.exists()  # 新目录保留
    assert report["freed_bytes"] >= 100
    # 用户产物目录不受影响
    assert (ctx.settings.data_dir / "jobs" / "j1" / "inputs").exists()


def test_cli_cleanup_command(tmp_path, capsys):
    import subprocess
    import sys

    env = dict(os.environ, NERO_DATA_DIR=str(tmp_path / "data"), NERO_DB_PATH=str(tmp_path / "data" / "nero.db"))
    r = subprocess.run([sys.executable, "-m", "nero_ppt.cli", "cleanup", "--dry-run"],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["dry_run"] is True
