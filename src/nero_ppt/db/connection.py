"""SQLite 连接与迁移。

约定：启用外键、busy_timeout、WAL；短事务；迁移幂等（schema_migrations 版本表）。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class LockedConnection(sqlite3.Connection):
    """跨线程安全连接：所有语句经进程内锁串行化（配合 SQLite 自身锁与 busy_timeout）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._py_lock = threading.RLock()

    def execute(self, *args, **kwargs):
        with self._py_lock:
            return super().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with self._py_lock:
            return super().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with self._py_lock:
            return super().executescript(*args, **kwargs)


def connect(db_path: str | Path) -> sqlite3.Connection:
    """新建连接：外键开启、busy_timeout、WAL；跨线程可用。"""
    conn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None,
                           check_same_thread=False, factory=LockedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _split_statements(sql: str) -> list[str]:
    """按 sqlite3.complete_statement 拆分 SQL 语句（避免分号内嵌误切）。"""
    statements: list[str] = []
    buf = ""
    for line in sql.splitlines():
        buf += line + "\n"
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            if stmt and not all(l.strip().startswith("--") or not l.strip() for l in stmt.splitlines()):
                statements.append(stmt)
            buf = ""
    if buf.strip():
        statements.append(buf.strip())
    return statements


def migrate(conn: sqlite3.Connection) -> list[int]:
    """按序应用未执行的迁移；返回本次应用的版本列表。幂等。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    ran: list[int] = []
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(sql_file.stem.split("_")[0])
        if version in applied:
            continue
        sql = sql_file.read_text(encoding="utf-8")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _split_statements(sql):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        ran.append(version)
    return ran
