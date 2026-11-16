"""访问控制：用户凭据、服务端可撤销会话、Origin 校验、登录限流。

- 凭据仅展示一次，存储不可逆验证值（scrypt）
- cookie HttpOnly + SameSite=Lax；HTTPS 部署时设置 Secure（由配置接管）
- demo 模式只在绑定回环地址时使用（cli.serve 强制），自动绑定 demo 用户
- 请求路径中的 ID 只用于定位，不能作为授权证据：一切授权走 user_id 归属检查
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

SESSION_COOKIE = "nero_session"
SESSION_TTL_HOURS = 12

_MAX_ATTEMPTS = 5
_LOCK_SECONDS = 300


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not isinstance(password, str) or not isinstance(stored, str):
        return False
    try:
        algo, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=2 ** 14, r=8, p=1, dklen=32)
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def generate_password() -> str:
    """高熵访问凭据（创建时仅展示一次）。"""
    return secrets.token_urlsafe(15)


class AccessService:
    def __init__(self, conn: sqlite3.Connection, *, secure_cookies: bool = False):
        self.conn = conn
        self.secure_cookies = secure_cookies
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    # ---- 用户 ----
    def create_user(self, username: str) -> str:
        """CLI 创建用户：返回一次性明文凭据。"""
        if not username or len(username) > 64:
            raise ValueError("用户名长度 1-64")
        if self.conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("用户名已存在")
        password = generate_password()
        user_id = secrets.token_hex(8)
        self.conn.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?,?,?,?)",
            (user_id, username, hash_password(password),
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
        return password

    def get_user(self, user_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    def get_user_by_name(self, username: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    # ---- 登录限流 ----
    def _attempts_key(self, username: str, ip: str) -> str:
        return f"{username}|{ip}"

    def _throttled(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            attempts = [t for t in self._attempts.get(key, []) if now - t < _LOCK_SECONDS]
            self._attempts[key] = attempts
            return len(attempts) >= _MAX_ATTEMPTS

    def _record_failure(self, key: str) -> None:
        with self._lock:
            self._attempts.setdefault(key, []).append(time.monotonic())

    # ---- 登录/会话 ----
    def login(self, username: str, password: str, ip: str = "-") -> str | None:
        """成功返回新会话 ID；失败返回 None（含限流拒绝）。"""
        key = self._attempts_key(username, ip)
        if self._throttled(key):
            return None
        user = self.get_user_by_name(username)
        if user is None or not verify_password(password, user["password_hash"]):
            self._record_failure(key)
            return None
        with self._lock:
            self._attempts.pop(key, None)
        return self.create_session(user["id"])

    def create_session(self, user_id: str) -> str:
        session_id = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        self.conn.execute(
            "INSERT INTO sessions (id, user_id, created_at, expires_at, revoked) VALUES (?,?,?,?,0)",
            (session_id, user_id, now.isoformat(timespec="seconds"),
             (now + timedelta(hours=SESSION_TTL_HOURS)).isoformat(timespec="seconds")))
        return session_id

    def session_user(self, session_id: str | None) -> sqlite3.Row | None:
        if not session_id:
            return None
        row = self.conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON s.user_id=u.id "
            "WHERE s.id=? AND s.revoked=0 AND s.expires_at > ?",
            (session_id, datetime.now(timezone.utc).isoformat(timespec="seconds"))).fetchone()
        return row

    def logout(self, session_id: str) -> None:
        """服务端撤销会话。"""
        self.conn.execute("UPDATE sessions SET revoked=1 WHERE id=?", (session_id,))


def check_same_origin(origin: str | None, host: str) -> bool:
    """状态修改请求的 Origin/Host 同源检查；无 Origin 的非浏览器客户端放行由路由层决定。"""
    if not origin:
        return True  # 非浏览器客户端（同机脚本）；浏览器总会带 Origin
    from urllib.parse import urlparse

    parsed = urlparse(origin)
    return parsed.netloc == host
