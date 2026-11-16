"""S12 集成测试：业务 API 与审核事务（T08/T10/T17 核心）。"""
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
                        auth_mode="password", session_secret="s" * 32,
                        model_api_key="", model_name="")
    application = create_app(settings)
    ctx = application.state.ctx
    ctx.conn.execute("INSERT INTO users (id, username, password_hash, created_at)"
                     " VALUES ('u1','alice','x','now'), ('u2','bob','x','now')")
    # 内置模板就位
    (ctx.settings.data_dir / "templates").mkdir(exist_ok=True)
    import shutil

    shutil.copyfile(TEMPLATE, ctx.settings.data_dir / "templates" / "builtin-classic.pptx")
    return application


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def alice(client):
    """通过改会话绑定 alice 用户。"""
    return client


def _bind_user(client, ctx, user_id: str):
    session = ctx.access.create_session(user_id)
    client.cookies.set("nero_session", session)


def _create_job(client, title="测试任务", scene="general"):
    r = client.post("/api/jobs", json={"title": title, "scene_id": scene}, headers={"origin": "http://testserver"})
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def _upload(client, job_id: str):
    data = (FIX / "sample-notes.txt").read_bytes()
    r = client.post(f"/api/jobs/{job_id}/documents",
                    files={"file": ("sample-notes.txt", io.BytesIO(data), "text/plain")},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 201, r.text


def test_job_lifecycle_to_outline_review(client, app):
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    _upload(client, job_id)
    job = client.get(f"/api/jobs/{job_id}").json()
    rev = job["revision"]
    r = client.post(f"/api/jobs/{job_id}/start", json={"expected_revision": rev},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200
    # Worker 跑 parse（模拟：直接调用 handler）
    from nero_ppt.workflow.runner import Worker

    w = Worker(ctx)
    assert w.run_once() is True
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["phase"] == "outline" and job["status"] == "running"
    # outline 步骤失败（模型未配置）→ failed 状态
    w.run_once()
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed" and "模型未配置" in job["error"]
    # retry 幂等与状态
    r = client.post(f"/api/jobs/{job_id}/retry", json={"expected_revision": job["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200


def test_t10_user_isolation(client, app):
    """两个用户：列表、状态、下载、修改均隔离。"""
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client, "alice 的任务")
    _upload(client, job_id)
    # bob 登录
    client.cookies.clear()
    _bind_user(client, ctx, "u2")
    assert all(j["id"] != job_id for j in client.get("/api/jobs").json()["items"])
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 404  # 归属不匹配统一 404，不泄露存在性
    r = client.post(f"/api/jobs/{job_id}/start", json={"expected_revision": 1},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 404
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "outline", "decision": "approve", "expected_revision": 1},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 404
    r = client.get(f"/api/jobs/{job_id}/artifacts/nonexistent")
    assert r.status_code == 404


def test_t08_old_revision_and_concurrent_approve(client, app):
    """旧 revision 审核返回 409 并带当前 revision；重复批准幂等。"""
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    # 手工把任务推进到 outline/awaiting_review 并写 outline 修订
    rev = ctx.jobs.update_state(job_id, 1, phase="outline", status="awaiting_review")
    ctx.records.save_revision(job_id, "outline",
                              {"revision": 1, "brief": {"topic": "t"},
                               "pages": [{"slide_id": "p1", "title": "页一"}]}, input_revision=1)
    # 用旧 revision 批准 → 409
    client.cookies.clear()
    _bind_user(client, ctx, "u1")
    ctx.jobs.update_state(job_id, rev, batch_index=0)  # 推进 revision 模拟并发变更
    current = ctx.jobs.get(job_id)["job_revision"]
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "outline", "decision": "approve",
                          "expected_revision": current - 1},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409
    body = r.json()["detail"]["error"]
    assert body["code"] == "revision_conflict"
    assert body["details"]["current_revision"] == current
    # 正确 revision 批准（无模型 → approve_outline 后 sample 渲染步骤入队即可）
    r1 = client.post(f"/api/jobs/{job_id}/reviews",
                     json={"target_kind": "outline", "decision": "approve",
                           "expected_revision": current, "request_id": "req-1"},
                     headers={"origin": "http://testserver"})
    assert r1.status_code == 200
    new_rev = r1.json()["revision"]
    assert new_rev == current + 1
    # 相同 request_id 重复请求 → 幂等缓存
    r2 = client.post(f"/api/jobs/{job_id}/reviews",
                     json={"target_kind": "outline", "decision": "approve",
                           "expected_revision": current, "request_id": "req-1"},
                     headers={"origin": "http://testserver"})
    assert r2.status_code == 200 and r2.json() == r1.json()
    # 再次批准同对象（不同 request_id）→ 幂等返回不重复推进
    r3 = client.post(f"/api/jobs/{job_id}/reviews",
                     json={"target_kind": "outline", "decision": "approve",
                           "expected_revision": new_rev},
                     headers={"origin": "http://testserver"})
    assert r3.status_code == 409  # 状态已不是 outline/awaiting_review


def test_review_requires_awaiting_review_state(client, app):
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "sample", "decision": "approve", "expected_revision": 1},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "state"


def test_upload_restricted_phases_and_types(client, app):
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    # 拒绝类型
    r = client.post(f"/api/jobs/{job_id}/documents",
                    files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 422
    # 启动后不能再上传（intake/running）
    _upload(client, job_id)
    rev = client.post(f"/api/jobs/{job_id}/start", json={"expected_revision": 1},
                      headers={"origin": "http://testserver"}).json()["revision"]
    r = client.post(f"/api/jobs/{job_id}/documents",
                    files={"file": ("late.txt", io.BytesIO(b"x"), "text/plain")},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409


def test_download_auth_and_404(client, app):
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    r = client.get(f"/api/jobs/{job_id}/artifacts/ghost")
    assert r.status_code == 404


def test_cancel_and_events(client, app):
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    _upload(client, job_id)
    rev = client.post(f"/api/jobs/{job_id}/start", json={"expected_revision": 1},
                      headers={"origin": "http://testserver"}).json()["revision"]
    r = client.post(f"/api/jobs/{job_id}/cancel", json={"expected_revision": rev},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "cancelled"
    # 事件轮询
    ev = client.get(f"/api/jobs/{job_id}/events").json()
    assert ev["status"] == "cancelled" and len(ev["events"]) >= 1
    ev2 = client.get(f"/api/jobs/{job_id}/events?after=1").json()
    assert all(e["seq"] > 1 for e in ev2["events"])


def test_scenarios_and_templates_endpoints(client, app):
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()["items"]}
    assert "general" in ids and "business-review" in ids
    r = client.get("/api/templates")
    assert any(t["id"] == "builtin-classic" for t in r.json()["items"])


def test_upload_template_returns_latest_revision_and_start_succeeds(client, app):
    """回归：模板上传推进修订；前端用响应中的 revision 调 start 不再 409。"""
    import io as _io

    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    _upload(client, job_id)
    # 上传模板（builtin-classic.pptx 作为用户模板）
    tpl = TEMPLATE.read_bytes()
    r = client.post(f"/api/jobs/{job_id}/template",
                    files={"file": ("my.pptx", _io.BytesIO(tpl), "application/octet-stream")},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 201, r.text
    latest = r.json()["revision"]
    assert latest == 2  # 模板建档推进修订
    # 用响应中的 revision 启动 → 成功
    r = client.post(f"/api/jobs/{job_id}/start", json={"expected_revision": latest},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == latest + 1


def test_steps_history_and_step_retry(client, app):
    """进度可视化与分步重试：job_view 返回步骤历史；按 step_id 重试。"""
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    _upload(client, job_id)
    rev = client.post(f"/api/jobs/{job_id}/start", json={"expected_revision": 1},
                      headers={"origin": "http://testserver"}).json()["revision"]
    from nero_ppt.workflow.runner import Worker

    w = Worker(ctx)
    w.run_once()  # parse 完成 → outline 入队
    w.run_once()  # outline 失败（模型未配置）
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["status"] == "failed"
    # 步骤历史：含 parse(done) 与 outline(failed+错误信息)
    kinds = {s["kind"]: s for s in view["steps"]}
    assert kinds["parse"]["status"] == "done"
    assert kinds["outline"]["status"] == "failed"
    assert "模型未配置" in kinds["outline"]["error"]
    # 分步重试 outline 步骤 → 任务回 running，步骤重新排队
    step_id = kinds["outline"]["id"]
    r = client.post(f"/api/jobs/{job_id}/steps/{step_id}/retry",
                    json={"expected_revision": view["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200
    assert ctx.steps.get(step_id)["status"] == "pending"
    assert ctx.jobs.get(job_id)["status"] == "running"
    # 非失败步骤不可重试
    r = client.post(f"/api/jobs/{job_id}/steps/{kinds['parse']['id']}/retry",
                    json={"expected_revision": r.json()["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409
    # 他人任务不可重试（404 隔离）
    client.cookies.clear()
    _bind_user(client, ctx, "u2")
    r = client.post(f"/api/jobs/{job_id}/steps/{step_id}/retry",
                    json={"expected_revision": 1},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 404


def test_job_view_returns_outline_at_any_revision(client, app):
    """回归：大纲生成成功推进修订后，job_view 仍能取到大纲（页面不再空白）。"""
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    # 模拟：大纲生成成功（保存 outline 修订）→ 修订号推进
    ctx.records.save_revision(job_id, "outline",
                              {"revision": 1, "brief": {"topic": "t"},
                               "pages": [{"slide_id": "p1", "title": "页一"},
                                         {"slide_id": "p2", "title": "页二"}]},
                              input_revision=1)
    ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"],
                          phase="outline", status="awaiting_review")
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["outline"] is not None
    assert len(view["outline"]["pages"]) == 2
    assert view["outline"]["pages"][0]["title"] == "页一"


def test_update_outline_persists_full_fields(client, app):
    """大页编辑保存链路：PUT outline 携带标题/目的/关键结论，job_view 反映新值。"""
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    ctx.records.save_revision(job_id, "outline",
                              {"revision": 1, "brief": {"topic": "t"},
                               "pages": [{"slide_id": "p1", "title": "旧标题",
                                          "purpose": "旧目的", "key_message": "旧结论"}]},
                              input_revision=1)
    ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"],
                          phase="outline", status="awaiting_review")
    rev = client.get(f"/api/jobs/{job_id}").json()["revision"]
    r = client.put(f"/api/jobs/{job_id}/outline",
                   json={"pages": [{"slide_id": "p1", "title": "新标题",
                                    "purpose": "新目的", "key_message": "新结论"}],
                         "expected_revision": rev},
                   headers={"origin": "http://testserver"})
    assert r.status_code == 200
    view = client.get(f"/api/jobs/{job_id}").json()
    page = view["outline"]["pages"][0]
    assert page["title"] == "新标题" and page["purpose"] == "新目的" and page["key_message"] == "新结论"
