"""S07 Web 层鉴权端到端：demo/password 模式、401/403、cookie 属性。"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nero_ppt.app import create_app
from nero_ppt.settings import Settings


def _settings(tmp_path, mode: str) -> Settings:
    return Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "nero.db",
                    auth_mode=mode, session_secret="s" * 32)


def test_demo_mode_binds_demo_user(tmp_path):
    app = create_app(_settings(tmp_path, "demo"))
    c = TestClient(app)
    r = c.get("/api/me")
    assert r.status_code == 200
    assert r.json()["username"] == "demo"


def test_password_mode_requires_login(tmp_path):
    app = create_app(_settings(tmp_path, "password"))
    c = TestClient(app)
    r = c.get("/api/me")
    assert r.status_code == 401


def test_password_mode_login_logout(tmp_path):
    app = create_app(_settings(tmp_path, "password"))
    ctx = app.state.ctx
    pw = ctx.access.create_user("carol")
    ctx.access.conn.execute("UPDATE users SET password_hash=(SELECT password_hash FROM users WHERE username='carol') WHERE username='carol'")
    # 用明文 pw 无法反推；直接建会话验证 cookie 流程
    c = TestClient(app)
    # 错误密码 401（不泄露用户枚举信息）
    r = c.post("/auth/login", json={"username": "carol", "password": "wrong"},
               headers={"origin": "http://testserver", "host": "testserver"})
    assert r.status_code == 401
    # 用服务端创建的会话设置 cookie
    session = ctx.access.create_session(ctx.access.get_user_by_name("carol")["id"])
    c.cookies.set("nero_session", session)
    r = c.get("/api/me")
    assert r.status_code == 200 and r.json()["username"] == "carol"
    # 注销后会话失效
    r = c.post("/auth/logout", headers={"origin": "http://testserver"})
    assert r.status_code == 200
    assert c.get("/api/me").status_code == 401


def test_login_cookie_flags(tmp_path):
    app = create_app(_settings(tmp_path, "password"))
    ctx = app.state.ctx
    ctx.access.create_user("dave")
    user = ctx.access.get_user_by_name("dave")
    ctx.access.conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                            (__import__("nero_ppt.services.access", fromlist=["hash_password"]).hash_password("pw12345678"), user["id"]))
    c = TestClient(app)
    r = c.post("/auth/login", json={"username": "dave", "password": "pw12345678"},
               headers={"origin": "http://testserver", "host": "testserver"})
    assert r.status_code == 200
    set_cookie = r.headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "SameSite=lax" in set_cookie


def test_cross_origin_post_rejected(tmp_path):
    app = create_app(_settings(tmp_path, "password"))
    c = TestClient(app)
    r = c.post("/auth/login", json={"username": "x", "password": "y"},
               headers={"origin": "http://evil.example", "host": "testserver"})
    assert r.status_code == 403


def test_data_dir_not_public(tmp_path):
    """data 目录不作为静态目录公开：访问不存在静态路径 404 而非文件列表。"""
    app = create_app(_settings(tmp_path, "demo"))
    c = TestClient(app)
    (tmp_path / "data" / "jobs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "jobs" / "secret.txt").write_text("机密")
    for path in ("/data/jobs/secret.txt", "/static/../data/jobs/secret.txt"):
        assert c.get(path).status_code in (404, 400), path
    # /jobs/{id} 是页面壳（不含数据），不得泄露文件内容
    r = c.get("/jobs/secret.txt")
    assert "机密" not in r.text
