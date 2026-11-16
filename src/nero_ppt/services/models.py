"""模型目录：服务端持有密钥的多模型配置，按任务选择。

配置来源（优先级）：
1. data/config/models.json（管理员维护；api_key_env 引用环境变量名，或直接 api_key）
2. 环境变量 NERO_MODEL_*（单档默认，id="default"）

安全约定：
- api/models 接口只返回 id/name/model_name/available，绝不返回密钥
- 密钥不写日志、不写前端
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..providers.base import OpenAIProvider, ProviderError


@dataclass
class ModelEntry:
    id: str
    name: str
    model_name: str
    base_url: str = ""
    api_key: str = ""          # 解析后的密钥（运行时持有，不外发）
    max_tokens: int = 4000     # 单次调用输出预算（推理模型建议 8000+）
    extra_body: dict = field(default_factory=dict)  # 透传 API 的额外参数（如关闭思考）
    available: bool = False
    unavailable_reason: str = ""

    def public(self) -> dict:
        """对外表示：不含任何密钥。"""
        return {"id": self.id, "name": self.name, "model_name": self.model_name,
                "max_tokens": self.max_tokens,
                "has_extra_body": bool(self.extra_body),
                "available": self.available,
                "unavailable_reason": self.unavailable_reason}


@dataclass
class ModelCatalog:
    default_id: str = ""
    entries: list[ModelEntry] = field(default_factory=list)
    source: str = ""  # file | env | none

    def get(self, model_id: str | None) -> ModelEntry | None:
        """按 id 取模型；None/空 用默认档。"""
        if not model_id or model_id == "default":
            model_id = self.default_id
        for e in self.entries:
            if e.id == model_id:
                return e
        return None

    def public_list(self) -> list[dict]:
        return [e.public() for e in self.entries]

    def build_provider(self, model_id: str | None, timeout: float | None = None) -> OpenAIProvider:
        entry = self.get(model_id)
        if entry is None:
            raise ProviderError(f"模型配置不存在：{model_id}")
        if not entry.available:
            raise ProviderError(f"模型 {entry.name} 不可用：{entry.unavailable_reason}")
        kwargs = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return OpenAIProvider(entry.base_url, entry.api_key, entry.model_name,
                              max_output_tokens=entry.max_tokens,
                              extra_body=entry.extra_body or None, **kwargs)


def _entry_from_cfg(cfg: dict, idx: int) -> ModelEntry:
    mid = str(cfg.get("id") or f"model-{idx}")
    name = str(cfg.get("name") or mid)
    model_name = str(cfg.get("model_name") or "")
    base_url = str(cfg.get("base_url") or "")
    key = ""
    if cfg.get("api_key_env"):
        key = os.environ.get(str(cfg["api_key_env"]), "")
    elif cfg.get("api_key"):
        key = str(cfg["api_key"])
    entry = ModelEntry(id=mid, name=name, model_name=model_name, base_url=base_url, api_key=key,
                       max_tokens=int(cfg.get("max_tokens") or 4000),
                       extra_body=cfg.get("extra_body") if isinstance(cfg.get("extra_body"), dict) else {})
    if not model_name:
        entry.available = False
        entry.unavailable_reason = "缺少 model_name"
    elif not key:
        entry.available = False
        entry.unavailable_reason = f"密钥未配置（api_key_env={cfg.get('api_key_env', '未设置')}）"
    else:
        entry.available = True
    return entry


def load_catalog(data_dir: Path, settings=None, *, use_cache: bool = True) -> ModelCatalog:
    """加载模型目录；models.json 按 mtime 缓存，支持热更新。"""
    cache = getattr(load_catalog, "_cache", None)
    path = Path(data_dir) / "config" / "models.json"
    if use_cache and cache is not None and cache.get("path") == path:
        mtime = path.stat().st_mtime if path.exists() else -1
        if cache.get("mtime") == mtime:
            return cache["catalog"]
    catalog = ModelCatalog()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = [_entry_from_cfg(c, i) for i, c in enumerate(data.get("models", []))]
            default = str(data.get("default") or (entries[0].id if entries else ""))
            catalog = ModelCatalog(default_id=default, entries=entries, source="file")
        except (json.JSONDecodeError, OSError):
            catalog = ModelCatalog(source="none")  # 配置损坏：显式无模型
    else:
        # 环境变量单档默认
        if settings is None:
            from ..settings import load_settings

            settings = load_settings()
        if settings.model_available or settings.model_name:
            entry = _entry_from_cfg({
                "id": "default", "name": f"默认模型（{settings.model_name or '未命名'}）",
                "model_name": settings.model_name, "base_url": settings.model_base_url,
                "api_key": settings.model_api_key,
            }, 0)
            catalog = ModelCatalog(default_id="default", entries=[entry], source="env")
        else:
            catalog = ModelCatalog(source="none")
    if use_cache:
        mtime = path.stat().st_mtime if path.exists() else -1
        load_catalog._cache = {"path": path, "mtime": mtime, "catalog": catalog}
    return catalog


# ---------------------------------------------------------------- 页面 CRUD


def _cfg_path(data_dir: Path) -> Path:
    return Path(data_dir) / "config" / "models.json"


def _read_cfg(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"models": []}
    return {"models": []}


def _write_cfg(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # 原子替换
    load_catalog._cache = None  # 写后立即生效


def add_model(data_dir: Path, name: str, base_url: str, model_name: str,
              api_key: str, *, api_key_env: str | None = None,
              max_tokens: int | None = None) -> ModelEntry:
    """页面添加模型：api_key 明文写入 data/config/models.json（本地私有目录）。"""
    import secrets

    if not name.strip():
        raise ValueError("名称不能为空")
    if not model_name.strip():
        raise ValueError("模型名不能为空")
    path = _cfg_path(data_dir)
    cfg = _read_cfg(path)
    models_list = cfg.setdefault("models", [])
    model_id = f"u-{secrets.token_hex(4)}"
    while any(m.get("id") == model_id for m in models_list):
        model_id = f"u-{secrets.token_hex(4)}"
    entry_cfg: dict = {"id": model_id, "name": name.strip(),
                       "base_url": (base_url or "").strip(),
                       "model_name": model_name.strip()}
    if max_tokens:
        entry_cfg["max_tokens"] = int(max_tokens)
    if api_key_env:
        entry_cfg["api_key_env"] = api_key_env
    else:
        entry_cfg["api_key"] = api_key or ""
    models_list.append(entry_cfg)
    if not cfg.get("default") or not any(m.get("id") == cfg.get("default") for m in models_list):
        cfg["default"] = model_id
    _write_cfg(path, cfg)
    return _entry_from_cfg(entry_cfg, 0)


def update_model(data_dir: Path, model_id: str, name: str, base_url: str,
                 model_name: str, api_key: str | None = None,
                 max_tokens: int | None = None) -> ModelEntry:
    """更新模型；api_key 为 None/空字符串时保留原值（含原 api_key_env 引用）。"""
    path = _cfg_path(data_dir)
    cfg = _read_cfg(path)
    for m in cfg.get("models", []):
        if m.get("id") == model_id:
            m["name"] = name.strip()
            m["base_url"] = (base_url or "").strip()
            m["model_name"] = model_name.strip()
            if max_tokens:
                m["max_tokens"] = int(max_tokens)
            if api_key:  # 留空 = 沿用原密钥/引用
                m.pop("api_key_env", None)
                m["api_key"] = api_key
            _write_cfg(path, cfg)
            return _entry_from_cfg(m, 0)
    raise ValueError(f"模型不存在：{model_id}")


def delete_model(data_dir: Path, model_id: str) -> bool:
    """删除模型；default 指向被删项时改指剩余第一个。"""
    path = _cfg_path(data_dir)
    cfg = _read_cfg(path)
    models_list = cfg.get("models", [])
    remaining = [m for m in models_list if m.get("id") != model_id]
    if len(remaining) == len(models_list):
        return False
    cfg["models"] = remaining
    if cfg.get("default") == model_id:
        cfg["default"] = remaining[0]["id"] if remaining else ""
    _write_cfg(path, cfg)
    return True
