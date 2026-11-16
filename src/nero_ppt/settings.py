"""设置加载：环境变量优先，.env 辅助。所有字段带校验。"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from pydantic import model_validator

from .contracts import NeroModel


class Settings(NeroModel):
    data_dir: Path
    db_path: Path
    auth_mode: str = "demo"  # demo | password
    session_secret: str = ""
    model_provider: str = "none"  # none | openai
    model_base_url: str = ""
    model_name: str = ""
    model_api_key: str = ""
    model_timeout_seconds: int = 120
    max_upload_mb: int = 30
    max_job_input_mb: int = 100
    max_output_slides: int = 60
    min_free_disk_mb: int = 2048
    max_repair_rounds: int = 2

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        if self.auth_mode not in ("demo", "password"):
            raise ValueError("auth_mode 必须是 demo 或 password")
        if self.max_upload_mb <= 0 or self.max_output_slides <= 0:
            raise ValueError("限额必须为正")
        if self.min_free_disk_mb <= 0:
            raise ValueError("磁盘阈值必须为正")
        return self

    @property
    def model_available(self) -> bool:
        """真实模型功能是否可用：无密钥时显式不可用，不伪造成功。"""
        return bool(self.model_api_key) and bool(self.model_name)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        for sub in ("jobs", "tmp", "templates"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


def load_settings(env_file: str | None = None) -> Settings:
    """从环境变量读取；可选 .env 文件补充（不覆盖已存在的环境变量）。"""
    if env_file and Path(env_file).exists():
        from dotenv import load_dotenv as _ld

        _ld(env_file, override=False)
    data_dir = Path(os.environ.get("NERO_DATA_DIR", "./data")).resolve()
    db_path = Path(os.environ.get("NERO_DB_PATH", str(data_dir / "nero.db"))).resolve()

    def _get(key: str, default: str = "") -> str:
        return os.environ.get(key, default)

    def _int(key: str, default: int) -> int:
        raw = os.environ.get(key)
        try:
            return int(raw) if raw else default
        except ValueError:
            return default

    session_secret = _get("NERO_SESSION_SECRET")
    if not session_secret:
        session_secret = secrets.token_hex(32)

    return Settings(
        data_dir=data_dir,
        db_path=db_path,
        auth_mode=_get("NERO_AUTH_MODE", "demo"),
        session_secret=session_secret,
        model_provider=_get("NERO_MODEL_PROVIDER", "none"),
        model_base_url=_get("NERO_MODEL_BASE_URL"),
        model_name=_get("NERO_MODEL_NAME"),
        model_api_key=_get("NERO_MODEL_API_KEY"),
        model_timeout_seconds=_int("NERO_MODEL_TIMEOUT_SECONDS", 120),
        max_upload_mb=_int("NERO_MAX_UPLOAD_MB", 30),
        max_job_input_mb=_int("NERO_MAX_JOB_INPUT_MB", 100),
        max_output_slides=_int("NERO_MAX_OUTPUT_SLIDES", 60),
        min_free_disk_mb=_int("NERO_MIN_FREE_DISK_MB", 2048),
        max_repair_rounds=_int("NERO_MAX_REPAIR_ROUNDS", 2),
    )
