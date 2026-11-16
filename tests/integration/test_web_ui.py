"""S13 门槛 D 测试：页面渲染、静态资源、完整浏览器流程（API 层模拟）。"""
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nero_ppt.app import create_app
from nero_ppt.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "assets" / "templates" / "builtin-classic.pptx"
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture()
def app(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "nero.db",
                        auth_mode="demo", session_secret="s" * 32, model_api_key="", model_name="")
    application = create_app(settings)
    ctx = application.state.ctx
    (ctx.settings.data_dir / "templates").mkdir(exist_ok=True)
    import shutil

    shutil.copyfile(TEMPLATE, ctx.settings.data_dir / "templates" / "builtin-classic.pptx")
    return application


@pytest.fixture()
def client(app):
    return TestClient(app)


def test_pages_render(client):
    for path, marker in [("/", "Nero PPT"), ("/new", "新建任务"), ("/jobs/x", "工作台")]:
        r = client.get(path, follow_redirects=False)
        if path == "/" :
            assert r.status_code in (200, 307)
        else:
            assert r.status_code == 200, path
            assert marker in r.text


def test_static_served_and_data_isolated(client, app):
    r = client.get("/static/app.js")
    assert r.status_code == 200 and "Nero PPT" in r.text
    r = client.get("/static/styles.css")
    assert r.status_code == 200
    # data 目录不可通过 /static 访问
    secret = app.state.ctx.settings.data_dir / "secret.txt"
    secret.write_text("机密")
    r = client.get("/static/../data/secret.txt")
    assert r.status_code in (404, 400)


def test_browser_flow_manual_specs_to_review(client, app):
    """从新建到样张审核的完整流程（用手工规格快照替代模型生成）。"""
    ctx = app.state.ctx
    client.get("/")  # demo 登录态
    # 创建 + 上传 + start
    r = client.post("/api/jobs", json={"title": "浏览器流程", "scene_id": "general"},
                    headers={"origin": "http://testserver"})
    job_id = r.json()["job_id"]
    client.post(f"/api/jobs/{job_id}/documents",
                files={"file": ("a.txt", io.BytesIO("收入 4,820 万元".encode()), "text/plain")},
                headers={"origin": "http://testserver"})
    rev = client.post(f"/api/jobs/{job_id}/start", json={"expected_revision": 1},
                      headers={"origin": "http://testserver"}).json()["revision"]
    from nero_ppt.workflow.runner import Worker

    w = Worker(ctx)
    w.run_once()  # parse
    # 取消自动入队的 outline 步骤（本测试用手工规格替代模型大纲）
    ctx.conn.execute("UPDATE steps SET status='cancelled' WHERE job_id=? AND kind='outline'", (job_id,))
    # 手工写入 specs 修订（模拟模型产出的批准版本）
    specs = [{
        "slide_id": "cover-1", "headline": "流程验证", "visual_intent": "title",
        "speaker_notes": {"script": "封面讲稿"},
    }, {
        "slide_id": "bullets-1", "headline": "要点页", "visual_intent": "bullets",
        "body_blocks": [{"kind": "bullets", "items": ["要点A", "要点B"]}],
        "speaker_notes": {"script": "要点讲稿"},
        "source_refs": [{"source_id": "x", "quote": "收入 4,820 万元"}],
    }]
    new_rev = ctx.records.next_revision(job_id)
    ctx.records.save_revision(job_id, "specs", {"revision": new_rev, "specs": specs},
                              input_revision=rev)
    ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"],
                          phase="sample", status="running")
    # 渲染批次
    step_id = ctx.steps.enqueue(job_id, "render_batch", input_revision=ctx.jobs.get(job_id)["job_revision"],
                                scope_key="sample", payload={"scope_key": "sample"})
    w.run_once()
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["phase"] == "sample" and view["status"] == "awaiting_review"
    assert len(view["slides"]) == 2 and len(view["specs_detail"]) == 2
    assert len(view["artifacts"]) >= 3  # pptx+pdf+thumbs
    # 下载缩略图
    thumb = next(a for a in view["artifacts"] if a["kind"] == "thumbnail")
    r = client.get(f"/api/jobs/{job_id}/artifacts/{thumb['id']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    # 批准样张 → 直接整稿（final/running + assemble 入队）
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "sample", "decision": "approve",
                          "expected_revision": view["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["phase"] == "final" and view["status"] == "running"
    # 已进入 final 的任务再次批准样张 → 409
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "sample", "decision": "approve",
                          "expected_revision": view["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409


def test_409_flow_on_stale_revision(client, app):
    """前端 409 处理契约：旧 revision 返回当前 revision。"""
    ctx = app.state.ctx
    demo_id = ctx.access.get_user_by_name("demo")["id"]
    job_id = ctx.jobs.create(demo_id, "t", "general", 1)
    ctx.jobs.update_state(job_id, 1, phase="outline", status="awaiting_review")
    ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"], batch_index=1)
    current = ctx.jobs.get(job_id)["job_revision"]
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "outline", "decision": "approve",
                          "expected_revision": current - 1},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["details"]["current_revision"] == current
