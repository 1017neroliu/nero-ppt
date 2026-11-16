"""模型目录与按任务选择模型测试。"""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nero_ppt.app import create_app
from nero_ppt.services.model_resolver import resolve_job_provider
from nero_ppt.services.models import load_catalog
from nero_ppt.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "assets" / "templates" / "builtin-classic.pptx"


def _settings(tmp_path, **over) -> Settings:
    base = dict(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "nero.db",
                auth_mode="demo", session_secret="s" * 32, model_api_key="", model_name="")
    base.update(over)
    return Settings(**base)


def _catalog(tmp_path, settings):
    return load_catalog(tmp_path / "data", settings, use_cache=False)


def test_env_default_catalog(tmp_path):
    """环境变量单档默认（id=default）。"""
    s = _settings(tmp_path, model_api_key="sk-test", model_name="gpt-4o-mini")
    cat = _catalog(tmp_path, s)
    assert cat.source == "env" and cat.default_id == "default"
    entry = cat.get(None)
    assert entry.available and entry.model_name == "gpt-4o-mini"


def test_no_config_catalog(tmp_path):
    """无配置：显式 none，不伪造。"""
    cat = _catalog(tmp_path, _settings(tmp_path))
    assert cat.source == "none" and cat.entries == []


def test_models_json_multi_profiles(tmp_path, monkeypatch):
    """models.json 多档：api_key_env 解析；不可用模型带原因；不泄露密钥。"""
    data_dir = tmp_path / "data"
    cfg_dir = data_dir / "config"
    cfg_dir.mkdir(parents=True)
    monkeypatch.setenv("TEST_KEY_A", "sk-aaa")
    (cfg_dir / "models.json").write_text(json.dumps({
        "default": "m1",
        "models": [
            {"id": "m1", "name": "GPT-4o mini", "base_url": "https://api.openai.com/v1",
             "model_name": "gpt-4o-mini", "api_key_env": "TEST_KEY_A"},
            {"id": "m2", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
             "model_name": "deepseek-chat", "api_key_env": "TEST_KEY_MISSING"},
            {"id": "m3", "name": "坏配置", "model_name": ""},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    s = _settings(tmp_path)
    cat = _catalog(tmp_path, s)
    assert cat.source == "file" and cat.default_id == "m1"
    m1, m2, m3 = cat.entries
    assert m1.available and m1.api_key == "sk-aaa"
    assert not m2.available and "TEST_KEY_MISSING" in m2.unavailable_reason
    assert not m3.available and "model_name" in m3.unavailable_reason
    # 公开表示无密钥（unavailable_reason 中的 api_key_env 引用允许存在）
    pub = json.dumps(cat.public_list())
    assert "sk-aaa" not in pub
    assert '"api_key"' not in pub and '"api_key":' not in pub
    # get 语义
    assert cat.get("m2").id == "m2"
    assert cat.get(None).id == "m1"  # 默认档


def test_hot_reload_on_mtime_change(tmp_path):
    data_dir = tmp_path / "data"
    cfg_dir = data_dir / "config"
    cfg_dir.mkdir(parents=True)
    path = cfg_dir / "models.json"
    path.write_text(json.dumps({"models": [{"id": "a", "name": "A", "model_name": "a"}]}), encoding="utf-8")
    s = _settings(tmp_path)
    cat1 = load_catalog(data_dir, s)  # 缓存
    assert cat1.get("a") is not None and cat1.get("b") is None
    import time

    time.sleep(0.02)
    path.write_text(json.dumps({"models": [{"id": "b", "name": "B", "model_name": "b"}]}), encoding="utf-8")
    cat2 = load_catalog(data_dir, s)  # mtime 变化 → 重新加载
    assert cat2.get("b") is not None and cat2.get("a") is None


def test_resolver_fallback_and_explicit_error(tmp_path):
    """任务绑定模型不可用 → 回退默认档；全不可用 → 显式"模型未配置"。"""
    from types import SimpleNamespace

    data_dir = tmp_path / "data"
    cfg_dir = data_dir / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "models.json").write_text(json.dumps({
        "default": "m1",
        "models": [
            {"id": "m1", "name": "可用", "model_name": "gpt", "api_key": "sk-ok"},
            {"id": "m2", "name": "无密钥", "model_name": "x", "api_key_env": "NOPE"},
        ],
    }), encoding="utf-8")
    settings = _settings(tmp_path)
    ctx = SimpleNamespace(settings=settings, data_dir=data_dir)
    # 绑定不可用 m2 → 回退默认 m1
    provider = resolve_job_provider(ctx, "m2")
    assert provider.model_name == "gpt"
    # 绑定不存在的 id → 回退默认
    provider = resolve_job_provider(ctx, "ghost")
    assert provider.model_name == "gpt"
    # 全部不可用 → 显式错误
    (cfg_dir / "models.json").write_text(json.dumps({
        "models": [{"id": "m2", "name": "无密钥", "model_name": "x", "api_key_env": "NOPE"}],
    }), encoding="utf-8")
    with pytest.raises(RuntimeError) as e:
        resolve_job_provider(ctx, "m2")
    assert "模型未配置" in str(e.value)
    # 无任何配置
    (cfg_dir / "models.json").unlink()
    load_catalog.cache_clear = None
    import nero_ppt.services.models as M

    M.load_catalog._cache = None  # 清缓存
    with pytest.raises(RuntimeError) as e:
        resolve_job_provider(ctx, None)
    assert "models.json" in str(e.value) or "NERO_MODEL" in str(e.value)


def test_models_endpoint_and_job_binding(tmp_path):
    """API：/api/models 无密钥；创建任务绑定模型；job_view 返回模型名。"""
    data_dir = tmp_path / "data"
    cfg_dir = data_dir / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "models.json").write_text(json.dumps({
        "default": "m1",
        "models": [{"id": "m1", "name": "测试模型", "model_name": "test-model", "api_key": "sk-secret"}],
    }), encoding="utf-8")
    app = create_app(_settings(tmp_path))
    ctx = app.state.ctx
    ctx.conn.execute("INSERT INTO users (id, username, password_hash, created_at)"
                     " VALUES ('u1','alice','x','now')")
    client = TestClient(app)
    r = client.get("/api/models")
    assert r.status_code == 200
    body = r.json()
    assert body["default"] == "m1"
    item = body["items"][0]
    assert item["available"] and item["name"] == "测试模型"
    assert "sk-secret" not in json.dumps(body)
    # 创建任务绑定模型
    r = client.post("/api/jobs", json={"title": "t", "scene_id": "general", "model_id": "m1"},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 201
    job_id = r.json()["job_id"]
    assert ctx.jobs.get(job_id)["model_profile_id"] == "m1"
    # 不存在的模型 422
    r = client.post("/api/jobs", json={"title": "t2", "scene_id": "general", "model_id": "ghost"},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 422
    # job_view 返回模型名（demo 模式 owner 是 demo 用户）
    from nero_ppt.services.jobs_service import JobsService

    demo_id = ctx.access.get_user_by_name("demo")["id"]
    view = JobsService(ctx).job_view(demo_id, job_id)
    assert view["model"]["name"] == "测试模型"


def test_connectivity_function(tmp_path, monkeypatch):
    """test_model_connectivity：成功/输出不符/错误分类。"""
    from nero_ppt.providers.base import FakeProvider, ProviderError, test_model_connectivity

    ok = FakeProvider(responses={"ok": {"ok": True}})
    r = test_model_connectivity(ok)  # FakeProvider.generate_json 签名兼容
    assert r["ok"] is True and r["error"] is None

    class BadOutputProvider(FakeProvider):
        def generate_json(self, system, user, *, max_tokens=4000, json_mode=True):
            return {"unexpected": 1}, self.usage

    r = test_model_connectivity(BadOutputProvider())
    assert r["ok"] is False and "不符合预期" in r["error"]

    class AuthFailProvider(FakeProvider):
        def generate_json(self, system, user, *, max_tokens=4000, json_mode=True):
            raise ProviderError("Error code: 401 - invalid_api_key")

    r = test_model_connectivity(AuthFailProvider())
    assert r["ok"] is False and "认证失败" in r["error"]

    class ConnFailProvider(FakeProvider):
        def generate_json(self, system, user, *, max_tokens=4000, json_mode=True):
            raise ProviderError("Connection error: name or service not known")

    r = test_model_connectivity(ConnFailProvider())
    assert r["ok"] is False and "无法连接" in r["error"]


def test_models_test_endpoint(tmp_path, monkeypatch):
    """/api/models/{id}/test：不可用配置直接返回原因；成功路径真实调用。"""
    data_dir = tmp_path / "data"
    cfg_dir = data_dir / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "models.json").write_text(json.dumps({
        "default": "m1",
        "models": [
            {"id": "m1", "name": "无密钥", "model_name": "x", "api_key_env": "NOPE"},
            {"id": "m2", "name": "好模型", "model_name": "good", "api_key": "sk-ok"},
        ],
    }), encoding="utf-8")
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    # 不可用配置：不发起调用直接返回原因
    r = client.post("/api/models/m1/test")
    assert r.status_code == 200
    assert r.json()["ok"] is False and "配置不可用" in r.json()["error"]
    # 不存在 → 404
    r = client.post("/api/models/ghost/test")
    assert r.status_code == 404
    # 可用模型：monkeypatch OpenAIProvider.generate_json 模拟成功调用
    from nero_ppt.providers import base as pb

    def fake_generate(self, system, user, *, max_tokens=4000, json_mode=True):
        return {"ok": True}, pb.Usage()

    monkeypatch.setattr(pb.OpenAIProvider, "generate_json", fake_generate)
    r = client.post("/api/models/m2/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["model"] == "好模型" and body["model_id"] == "m2"


def test_start_blocked_when_model_configured_but_unavailable(tmp_path, monkeypatch):
    """start 前置校验：目录有模型但无密钥 → 409；目录空 → 放行（确定性链路）。"""
    import io as _io

    data_dir = tmp_path / "data"
    cfg_dir = data_dir / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "models.json").write_text(json.dumps({
        "models": [{"id": "m1", "name": "无密钥", "model_name": "x", "api_key_env": "NOPE"}],
    }), encoding="utf-8")
    app = create_app(_settings(tmp_path))
    ctx = app.state.ctx
    (ctx.settings.data_dir / "templates").mkdir(exist_ok=True)
    import shutil

    shutil.copyfile(TEMPLATE, ctx.settings.data_dir / "templates" / "builtin-classic.pptx")
    client = TestClient(app)
    r = client.post("/api/jobs", json={"title": "t", "scene_id": "general", "model_id": "m1"},
                    headers={"origin": "http://testserver"})
    job_id = r.json()["job_id"]
    client.post(f"/api/jobs/{job_id}/documents",
                files={"file": ("a.txt", _io.BytesIO(b"data"), "text/plain")},
                headers={"origin": "http://testserver"})
    r = client.post(f"/api/jobs/{job_id}/start", json={"expected_revision": 1},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "model_required"
    assert "可用性测试" in r.json()["detail"]["error"]["message"]
    # 目录清空（未配置模型）→ start 放行
    (cfg_dir / "models.json").unlink()
    import nero_ppt.services.models as M

    M.load_catalog._cache = None
    r = client.post(f"/api/jobs/{job_id}/start", json={"expected_revision": 1},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200


def test_model_crud_api(tmp_path):
    """页面添加/更新/删除模型；密钥永不出现在响应；热生效。"""
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    # 添加
    r = client.post("/api/models",
                    json={"name": "我的模型", "base_url": "https://api.example.com/v1",
                          "model_name": "my-model", "api_key": "sk-user-key"},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 201
    mid = r.json()["model"]["id"]
    assert r.json()["model"]["available"] is True
    assert "sk-user-key" not in json.dumps(r.json())
    # 落盘且目录条目存在
    cfg = json.loads((tmp_path / "data" / "config" / "models.json").read_text(encoding="utf-8"))
    assert any(m["id"] == mid and m["api_key"] == "sk-user-key" for m in cfg["models"])
    assert cfg["default"] == mid  # 第一个添加成为默认
    # 校验：缺模型名 422
    r = client.post("/api/models", json={"name": "x", "model_name": " "},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 422
    # 列表含新模型且无密钥
    r = client.get("/api/models")
    items = {m["id"]: m for m in r.json()["items"]}
    assert items[mid]["name"] == "我的模型"
    assert "sk-user-key" not in json.dumps(r.json())
    # 更新（api_key 留空沿用原值）
    r = client.put(f"/api/models/{mid}",
                   json={"name": "我的模型2", "base_url": "https://api2.example.com/v1",
                         "model_name": "my-model-2", "api_key": ""},
                   headers={"origin": "http://testserver"})
    assert r.status_code == 200
    cfg = json.loads((tmp_path / "data" / "config" / "models.json").read_text(encoding="utf-8"))
    entry = next(m for m in cfg["models"] if m["id"] == mid)
    assert entry["name"] == "我的模型2" and entry["api_key"] == "sk-user-key"
    # 删除后列表为空、default 清空
    r = client.delete(f"/api/models/{mid}", headers={"origin": "http://testserver"})
    assert r.status_code == 200
    r = client.get("/api/models")
    assert r.json()["items"] == []
    cfg = json.loads((tmp_path / "data" / "config" / "models.json").read_text(encoding="utf-8"))
    assert cfg["default"] == ""
    # 删除不存在的 404
    r = client.delete(f"/api/models/{mid}", headers={"origin": "http://testserver"})
    assert r.status_code == 404


def test_provider_streams_long_output(tmp_path, monkeypatch):
    from types import SimpleNamespace

    """流式调用：拼接 delta 内容并提取 usage；兼容测试桩与长输出。"""
    from nero_ppt.providers.base import OpenAIProvider, Usage

    provider = OpenAIProvider.__new__(OpenAIProvider)  # 绕过 __init__ 的 SDK 构造
    provider.model_name = "test"
    provider.timeout = 30
    provider.max_output_tokens = 4000
    provider.extra_body = None

    class FakeStream:
        def __init__(self):
            self._chunks = [
                _mk_chunk({"delta": {"content": '{"brief":'}}),
                _mk_chunk({"delta": {"content": ' {"topic": "t"}}'}}),
                _mk_chunk({"delta": {}, "usage": _mk_usage()}),
                _mk_chunk(None),  # 结束 chunk（choices 为空列表）
            ]
        def __iter__(self):
            return iter(self._chunks)

    def _mk_chunk(body):
        choices = []
        if body and "delta" in body:
            choices.append(SimpleNamespace(delta=SimpleNamespace(**body["delta"])))
        usage = body.get("usage") if body else None
        return SimpleNamespace(choices=choices, usage=usage)

    def _mk_usage():
        return SimpleNamespace(prompt_tokens=10, completion_tokens=5)

    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["stream"] is True
            assert kwargs["stream_options"] == {"include_usage": True}
            return FakeStream()

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    data, usage = provider.generate_json("s", "u")
    assert data == {"brief": {"topic": "t"}}
    assert usage.completion_tokens == 5 and usage.calls == 1
