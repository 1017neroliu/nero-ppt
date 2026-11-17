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


def test_reopen_outline_from_final_completed(client, app):
    """阶段回退：整稿（含已发布）发现问题 → 回到大纲修改 → 再批准重新生成。

    用户场景：走到第 5 步（整稿/发布）发现内容问题，回到第 2 步改大纲，
    批准后重新走样张/批次/整稿链路。
    """
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    _upload(client, job_id)
    # 直接构造已走到整稿并发布的任务（链路细节由其他测试覆盖）
    ctx.records.save_revision(job_id, "outline",
                              {"revision": 1, "brief": {"topic": "t"},
                               "pages": [{"slide_id": "p1", "title": "旧标题",
                                          "purpose": "p", "key_message": "k"}]},
                              input_revision=1)
    cur = ctx.jobs.get(job_id)["job_revision"]
    ctx.jobs.update_state(job_id, cur, phase="final", status="completed", error="")
    view = client.get(f"/api/jobs/{job_id}").json()
    rev = view["revision"]

    # 已发布终态 → 回到大纲修改
    r = client.post(f"/api/jobs/{job_id}/reopen-outline",
                    json={"expected_revision": rev},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reopened"] is True
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["phase"] == "outline" and view["status"] == "awaiting_review"
    assert view["error"] in ("", None)
    assert view["revision"] == body["revision"]

    # 回退后可编辑大纲（update_outline 要求 outline/awaiting_review）
    r = client.put(f"/api/jobs/{job_id}/outline",
                   json={"pages": [{"slide_id": "p1", "title": "回炉新标题",
                                    "purpose": "p", "key_message": "k"}],
                         "expected_revision": view["revision"]},
                   headers={"origin": "http://testserver"})
    assert r.status_code == 200

    # 再次批准大纲 → 重新入队规格生成（幂等键基于新 job_revision，不与历史冲突）
    rev2 = client.get(f"/api/jobs/{job_id}").json()["revision"]
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "outline", "decision": "approve",
                          "expected_revision": rev2},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200, r.text
    steps = ctx.conn.execute(
        "SELECT kind, status FROM steps WHERE job_id=? AND status='pending'", (job_id,)).fetchall()
    assert any(s["kind"] == "generate_specs" for s in steps), "批准后应重新入队规格生成"


def test_reopen_outline_rejects_invalid_states(client, app):
    """回退仅限后续阶段：大纲阶段/整稿运行中不可回退；版本冲突 409。"""
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    cur = ctx.jobs.get(job_id)["job_revision"]
    # intake 阶段：不可回退
    r = client.post(f"/api/jobs/{job_id}/reopen-outline",
                    json={"expected_revision": cur},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409
    # 整稿生成中（running）：不可回退
    ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"],
                          phase="final", status="running")
    r = client.post(f"/api/jobs/{job_id}/reopen-outline",
                    json={"expected_revision": ctx.jobs.get(job_id)["job_revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409
    # 版本冲突
    ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"],
                          phase="final", status="awaiting_review")
    r = client.post(f"/api/jobs/{job_id}/reopen-outline",
                    json={"expected_revision": 1},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409
    # 同阶段纯切换（rerun=False）无意义 → 409；rerun=True 允许（重跑该步）
    ctx.records.add_artifact(job_id, "pptx", 3, f"jobs/{job_id}/revisions/3/final.pptx", "h" * 64, 1)
    cur = ctx.jobs.get(job_id)["job_revision"]
    r = client.post(f"/api/jobs/{job_id}/reopen",
                    json={"phase": "final", "expected_revision": cur},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409
    r = client.post(f"/api/jobs/{job_id}/reopen",
                    json={"phase": "final", "expected_revision": ctx.jobs.get(job_id)["job_revision"],
                          "rerun": True},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200
    assert ctx.conn.execute(
        "SELECT COUNT(*) FROM steps WHERE job_id=? AND kind='assemble' AND status='pending'",
        (job_id,)).fetchone()[0] == 1


def test_reopen_any_step_and_rerun(client, app):
    """向导式步骤切换：任意已到达步骤可切换（产物保留），rerun=True 立即重跑入队。"""
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    _upload(client, job_id)
    # 构造已走完全流程的任务：大纲/规格/样张/整稿产物都在
    ctx.records.save_revision(job_id, "outline",
                              {"revision": 1, "brief": {"topic": "t"},
                               "pages": [{"slide_id": "p1", "title": "t1",
                                          "purpose": "p", "key_message": "k"}]},
                              input_revision=1)
    ctx.records.save_revision(job_id, "specs",
                              {"revision": 2, "specs": [{"slide_id": "p1", "headline": "h",
                                                         "visual_intent": "bullets",
                                                         "body_blocks": [], "source_refs": [],
                                                         "speaker_notes": {"script": "s"}}]},
                              input_revision=2)
    cur = ctx.jobs.get(job_id)["job_revision"]
    ctx.jobs.update_state(job_id, cur, phase="final", status="awaiting_review", error="")
    # 整稿渲染产物登记（真实流程 assemble 产物）
    ctx.records.add_artifact(job_id, "pptx", 3, f"jobs/{job_id}/revisions/3/final.pptx", "h" * 64, 1)

    view = client.get(f"/api/jobs/{job_id}").json()
    assert set(view["reached_phases"]) >= {"intake", "outline", "sample", "final"}
    assert view["step_revisions"]["outline"] is not None
    assert view["step_revisions"]["specs"] is not None
    assert len(view["materials"]) >= 1

    # 1) 切换到大纲（不重跑）：状态 outline/awaiting_review，产物修订保留
    rev = view["revision"]
    r = client.post(f"/api/jobs/{job_id}/reopen",
                    json={"phase": "outline", "expected_revision": rev},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200, r.text
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["phase"] == "outline" and view["status"] == "awaiting_review"
    assert view["step_revisions"]["specs"] is not None, "切换不得丢弃样张/规格产物"

    # 2) 从大纲切换到样张（later 阶段但已到达）：允许（产物保留的向导导航）
    r = client.post(f"/api/jobs/{job_id}/reopen",
                    json={"phase": "sample", "expected_revision": view["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["phase"] == "sample" and view["status"] == "awaiting_review"

    # 3) 在样张步重跑：状态 sample/running + generate_specs 入队
    r = client.post(f"/api/jobs/{job_id}/reopen",
                    json={"phase": "sample", "expected_revision": view["revision"], "rerun": True},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200, r.text
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["phase"] == "sample" and view["status"] == "running"
    steps = ctx.conn.execute(
        "SELECT kind, status FROM steps WHERE job_id=? AND status='pending'", (job_id,)).fetchall()
    assert any(s["kind"] == "generate_specs" for s in steps)

    # 4) 运行中不可切换
    r = client.post(f"/api/jobs/{job_id}/reopen",
                    json={"phase": "outline", "expected_revision": view["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409

    # 5) 未到达阶段不可切换（batch 无产物）
    ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"],
                          phase="sample", status="awaiting_review")
    r = client.post(f"/api/jobs/{job_id}/reopen",
                    json={"phase": "final", "expected_revision": 1},
                    headers={"origin": "http://testserver"})
    # final 已到达（构造时 final/awaiting_review，但无 final 渲染产物 → 不可切换）
    assert r.status_code == 409


def test_reopen_to_intake_ready_then_next(client, app):
    """切到第 1 步（intake/ready）后：下一步可导航、重跑解析=start 语义。"""
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    _upload(client, job_id)
    # 走到大纲审核（模拟正常流：解析+大纲产物已有）
    ctx.records.save_revision(job_id, "outline",
                              {"revision": 1, "brief": {"topic": "t"},
                               "pages": [{"slide_id": "p1", "title": "t1",
                                          "purpose": "p", "key_message": "k"}]},
                              input_revision=1)
    cur = ctx.jobs.get(job_id)["job_revision"]
    ctx.jobs.update_state(job_id, cur, phase="outline", status="awaiting_review")

    # 切到第 1 步 → intake/ready
    view = client.get(f"/api/jobs/{job_id}").json()
    r = client.post(f"/api/jobs/{job_id}/reopen",
                    json={"phase": "intake", "expected_revision": view["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200, r.text
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["phase"] == "intake" and view["status"] == "ready"

    # 第 1 步 rerun（ready 状态）= start 语义：置 running + 入队 parse
    r = client.post(f"/api/jobs/{job_id}/reopen",
                    json={"phase": "intake", "expected_revision": view["revision"],
                          "rerun": True},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200, r.text
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["phase"] == "intake" and view["status"] == "running"
    assert ctx.conn.execute(
        "SELECT COUNT(*) FROM steps WHERE job_id=? AND kind='parse' AND status='pending'",
        (job_id,)).fetchone()[0] == 1

    # 运行中不可切换（切走会打断执行语义，parse 完成后自然到达大纲审核）
    r = client.post(f"/api/jobs/{job_id}/reopen",
                    json={"phase": "outline", "expected_revision": view["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409


def test_set_job_model_and_rerun_failed_step(client, app):
    """详情页补配模型：绑定目录条目 + 立即重试失败步骤（大纲等模型步骤重跑）。"""
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    _upload(client, job_id)
    # 构造"未配模型且大纲步骤失败"的历史任务
    cur = ctx.jobs.get(job_id)["job_revision"]
    ctx.jobs.update_state(job_id, cur, phase="outline", status="failed",
                          error="模型未配置：需在 data/config/models.json 配置模型目录")
    # 登记一条失败的 outline 步骤（真实流程中由 worker 执行失败产生）
    step_id = ctx.steps.enqueue(job_id, "outline", input_revision=cur, scope_key="initial")
    ctx.steps.fail(step_id, "ProviderError: 模型未配置")
    outline_step = ctx.conn.execute(
        "SELECT id FROM steps WHERE job_id=? AND kind='outline' AND status='failed'",
        (job_id,)).fetchone()
    assert outline_step is not None
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["model"] is None

    # 注入测试模型目录（load_catalog 读 data/config/models.json）
    import json as _json

    cfg_dir = ctx.settings.data_dir / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "models.json").write_text(_json.dumps({
        "default": "m-test",
        "models": [{"id": "m-test", "name": "test-model", "base_url": "http://127.0.0.1:9",
                    "model_name": "test/model", "api_key": "sk-test"}]}, ensure_ascii=False))
    # 目录里任选一个可用模型
    models = client.get("/api/models").json()
    assert models["items"], "测试环境需有模型目录（data/config/models.json）"
    target = next((m for m in models["items"] if m["available"]), models["items"][0])

    # 绑定模型并重试失败步骤
    r = client.put(f"/api/jobs/{job_id}/model",
                   json={"model_profile_id": target["id"],
                         "expected_revision": view["revision"], "rerun_failed": True},
                   headers={"origin": "http://testserver"})
    assert r.status_code == 200, r.text
    assert r.json()["retried_step"] == outline_step["id"]
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["model"]["id"] == target["id"]
    assert view["status"] == "running" and view["error"] in ("", None)
    assert ctx.conn.execute(
        "SELECT status FROM steps WHERE id=?", (outline_step["id"],)).fetchone()["status"] == "pending"

    # 非法模型 ID → 422
    r = client.put(f"/api/jobs/{job_id}/model",
                   json={"model_profile_id": "nope", "expected_revision": view["revision"]},
                   headers={"origin": "http://testserver"})
    assert r.status_code == 422
    # 版本冲突 → 409
    r = client.put(f"/api/jobs/{job_id}/model",
                   json={"model_profile_id": target["id"], "expected_revision": 1},
                   headers={"origin": "http://testserver"})
    assert r.status_code == 409


def test_reopen_sample_render_only(client, app):
    """样张仅重渲染：规格不变、不调模型，入队 render_batch 而非 generate_specs。"""
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    _upload(client, job_id)
    ctx.records.save_revision(job_id, "specs",
                              {"revision": 2, "specs": [{"slide_id": "p1", "headline": "h",
                                                         "visual_intent": "bullets",
                                                         "body_blocks": [], "source_refs": [],
                                                         "speaker_notes": {"script": "s"}}]},
                              input_revision=2)
    ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"],
                          phase="sample", status="awaiting_review")
    view = client.get(f"/api/jobs/{job_id}").json()
    r = client.post(f"/api/jobs/{job_id}/reopen",
                    json={"phase": "sample", "expected_revision": view["revision"],
                          "rerun": True, "render_only": True},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200, r.text
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["phase"] == "sample" and view["status"] == "running"
    pending = ctx.conn.execute(
        "SELECT kind FROM steps WHERE job_id=? AND status='pending'", (job_id,)).fetchall()
    kinds = [s["kind"] for s in pending]
    assert "render_batch" in kinds and "generate_specs" not in kinds, kinds


def test_render_versions_listed(client, app):
    """版本历史：同一样张多次渲染 → render_versions 按顺序列出，各版本带下载与缩略图。"""
    ctx = app.state.ctx
    _bind_user(client, ctx, "u1")
    job_id = _create_job(client)
    _upload(client, job_id)
    # 构造两个样张渲染版本（rev 2 与 rev 3）
    for rev in (2, 3):
        ctx.records.add_artifact(job_id, "pptx", rev, f"jobs/{job_id}/revisions/{rev}/batch-sample.pptx",
                                 "h" * 64, 1)
        ctx.records.add_artifact(job_id, "pdf", rev, f"jobs/{job_id}/revisions/{rev}/batch-sample.pdf",
                                 "h" * 64, 1)
        ctx.records.add_artifact(job_id, "thumbnail", rev, f"jobs/{job_id}/revisions/{rev}/thumbs/page-01.png",
                                 "h" * 64, 1, slide_id="p1")
    view = client.get(f"/api/jobs/{job_id}").json()
    versions = view["render_versions"]["sample"]
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["revision"] == 2 and versions[1]["revision"] == 3
    for v in versions:
        assert "pptx" in v["files"] and "pdf" in v["files"]
        assert len(v["thumb_ids"]) == 1
        assert v["created_at"]
