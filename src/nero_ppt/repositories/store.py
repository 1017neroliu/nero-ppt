"""持久化仓储：查询与原子操作，不含业务状态转换逻辑。

约定：
- job_revision 是全局乐观锁；UPDATE ... WHERE job_revision=? 成功才原子 +1
- 步骤领取使用单条 UPDATE 原子操作（pending → running + 租约）
- 文件使用随机 ID 存储，显示名称与路径分离
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from pathlib import Path

from ..contracts import ReleaseManifest


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_hex(8)}"


# ---------------------------------------------------------------- jobs


class ConflictError(Exception):
    """乐观锁冲突或唯一约束冲突。"""

    def __init__(self, message: str, current_revision: int | None = None):
        super().__init__(message)
        self.current_revision = current_revision


class JobRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, owner_id: str, title: str, scene_id: str, scene_version: int,
               brief: dict | None = None, model_profile_id: str | None = None) -> str:
        job_id = new_id("j")
        t = now_iso()
        self.conn.execute(
            "INSERT INTO jobs (id, owner_id, title, scene_id, scene_version, brief_json,"
            " model_profile_id, phase, status,"
            " job_revision, created_at, updated_at) VALUES (?,?,?,?,?,?,?, 'intake', 'ready', 1, ?, ?)",
            (job_id, owner_id, title, scene_id, scene_version,
             json.dumps(brief, ensure_ascii=False) if brief else None,
             model_profile_id, t, t))
        return job_id

    def get(self, job_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def get_owned(self, job_id: str, owner_id: str) -> sqlite3.Row | None:
        """授权检查：owner 不匹配返回 None（上层转 404）。"""
        return self.conn.execute(
            "SELECT * FROM jobs WHERE id=? AND owner_id=?", (job_id, owner_id)).fetchone()

    def list_for_owner(self, owner_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE owner_id=? ORDER BY created_at DESC", (owner_id,)).fetchall()

    def update_state(self, job_id: str, expected_revision: int, *, phase: str | None = None,
                     status: str | None = None, brief: dict | None = None,
                     batch_index: int | None = None, error: str | None = None,
                     template_profile_id: str | None = None,
                     model_profile_id: str | None = None,
                     clear_model: bool = False) -> int:
        """乐观锁状态更新：成功返回新 revision；版本不匹配抛 ConflictError。"""
        sets = ["job_revision=job_revision+1", "updated_at=?"]
        args: list = [now_iso()]
        if phase is not None:
            sets.append("phase=?")
            args.append(phase)
        if status is not None:
            sets.append("status=?")
            args.append(status)
        if brief is not None:
            sets.append("brief_json=?")
            args.append(json.dumps(brief, ensure_ascii=False))
        if batch_index is not None:
            sets.append("batch_index=?")
            args.append(batch_index)
        if error is not None:
            sets.append("error=?")
            args.append(error)
        if template_profile_id is not None:
            sets.append("template_profile_id=?")
            args.append(template_profile_id)
        if model_profile_id is not None:
            sets.append("model_profile_id=?")
            args.append(model_profile_id)
        elif clear_model:
            sets.append("model_profile_id=NULL")
        args.append(job_id)
        args.append(expected_revision)
        cur = self.conn.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE id=? AND job_revision=?", args)
        if cur.rowcount == 0:
            row = self.get(job_id)
            current = row["job_revision"] if row else None
            raise ConflictError(f"job_revision 冲突（期望 {expected_revision}）", current)
        row = self.get(job_id)
        return row["job_revision"]

    def claim_next_step_atomic(self, worker_id: str, lease_seconds: int = 600) -> sqlite3.Row | None:
        """Worker 原子领取：pending 或租约过期的 running 步骤 → running。

        单条 UPDATE 完成状态与租约写入；渲染/模型调用期间不持有写事务。
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT s.id FROM steps s JOIN jobs j ON s.job_id = j.id "
                "WHERE (s.status='pending' OR (s.status='running' AND (s.lease_expires_at IS NULL OR s.lease_expires_at < ?))) "
                "AND j.status NOT IN ('cancelled','completed') "
                "ORDER BY s.created_at LIMIT 1", (now.isoformat(timespec="seconds"),)).fetchone()
            if row is None:
                self.conn.execute("COMMIT")
                return None
            cur = self.conn.execute(
                "UPDATE steps SET status='running', lease_owner=?, lease_expires_at=?, updated_at=? "
                "WHERE id=? AND status IN ('pending','running')", (worker_id, expires, now_iso(), row["id"]))
            if cur.rowcount != 1:
                self.conn.execute("COMMIT")
                return None
            self.conn.execute("COMMIT")
            return self.conn.execute("SELECT * FROM steps WHERE id=?", (row["id"],)).fetchone()
        except Exception:
            self.conn.execute("ROLLBACK")
            raise


# ---------------------------------------------------------------- steps


class StepRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def enqueue(self, job_id: str, kind: str, input_revision: int,
                scope_key: str = "", payload: dict | None = None) -> str:
        """插入唯一待执行步骤；重复(相同键)返回已有 ID（幂等）。"""
        existing = self.conn.execute(
            "SELECT id FROM steps WHERE job_id=? AND kind=? AND input_revision=? AND scope_key=?",
            (job_id, kind, input_revision, scope_key)).fetchone()
        if existing:
            return existing["id"]
        step_id = new_id("s")
        t = now_iso()
        try:
            self.conn.execute(
                "INSERT INTO steps (id, job_id, kind, status, input_revision, scope_key, payload_json,"
                " created_at, updated_at) VALUES (?,?,?,'pending',?,?,?,?,?)",
                (step_id, job_id, kind, input_revision, scope_key,
                 json.dumps(payload, ensure_ascii=False) if payload else None, t, t))
        except sqlite3.IntegrityError:
            existing = self.conn.execute(
                "SELECT id FROM steps WHERE job_id=? AND kind=? AND input_revision=? AND scope_key=?",
                (job_id, kind, input_revision, scope_key)).fetchone()
            return existing["id"]
        return step_id

    def get(self, step_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM steps WHERE id=?", (step_id,)).fetchone()

    def finish(self, step_id: str, result: dict | None = None) -> None:
        self.conn.execute(
            "UPDATE steps SET status='done', result_json=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False) if result else None, now_iso(), step_id))

    def fail(self, step_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE steps SET status='failed', error=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
            (error, now_iso(), step_id))

    def requeue(self, step_id: str, clear_lease: bool = True) -> None:
        """重试：running/failed/interrupted → pending（重试不重复发布由业务层保证）。"""
        self.conn.execute(
            "UPDATE steps SET status='pending', error=NULL, updated_at=? WHERE id=?",
            (now_iso(), step_id))

    def list_for_job(self, job_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM steps WHERE job_id=? ORDER BY created_at", (job_id,)).fetchall()

    def mark_interrupted_running(self, exclude_owner: str | None = None) -> int:
        """Worker 启动时：把残留 running 且租约已过期/无租约的步骤标记 interrupted。

        租约未过期的步骤不重置（持有者可能仍在执行），避免第二个 Worker
        启动就重置正在执行的步骤。
        """
        now = now_iso()
        if exclude_owner:
            cur = self.conn.execute(
                "UPDATE steps SET status='interrupted', updated_at=? WHERE status='running'"
                " AND (lease_expires_at IS NULL OR lease_expires_at < ?)"
                " AND (lease_owner IS NULL OR lease_owner != ?)",
                (now, now, exclude_owner))
        else:
            cur = self.conn.execute(
                "UPDATE steps SET status='interrupted', updated_at=? WHERE status='running'"
                " AND (lease_expires_at IS NULL OR lease_expires_at < ?)", (now, now))
        return cur.rowcount


# ---------------------------------------------------------------- revisions / artifacts / documents


class RecordRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def next_revision(self, job_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(revision),0)+1 AS n FROM revisions WHERE job_id=?", (job_id,)).fetchone()
        return row["n"]

    def save_revision(self, job_id: str, kind: str, data: dict, input_revision: int | None = None) -> tuple[int, str]:
        """不可变快照；同 revision 重存为幂等（返回既有）。"""
        existing = self.conn.execute(
            "SELECT id FROM revisions WHERE job_id=? AND revision=? AND kind=?",
            (job_id, data.get("revision") or self.next_revision(job_id), kind)).fetchone()
        rev = data.get("revision") or self.next_revision(job_id)
        if existing:
            return rev, existing["id"]
        rev_id = new_id("r")
        self.conn.execute(
            "INSERT INTO revisions (id, job_id, revision, kind, data_json, input_revision, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (rev_id, job_id, rev, kind, json.dumps(data, ensure_ascii=False), input_revision, now_iso()))
        return rev, rev_id

    def get_revision(self, job_id: str, revision: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM revisions WHERE job_id=? AND revision=?", (job_id, revision)).fetchone()

    def add_document(self, job_id: str, display_name: str, kind: str, file_ext: str,
                     file_hash: str, byte_size: int, stored_path: str) -> str:
        doc_id = new_id("d")
        self.conn.execute(
            "INSERT INTO documents (id, job_id, display_name, kind, file_ext, file_hash, byte_size,"
            " parse_status, stored_path, uploaded_at) VALUES (?,?,?,?,?,?,?,'pending',?,?)",
            (doc_id, job_id, display_name, kind, file_ext, file_hash, byte_size, stored_path, now_iso()))
        return doc_id

    def documents_for_job(self, job_id: str, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return self.conn.execute(
                "SELECT * FROM documents WHERE job_id=? AND kind=?", (job_id, kind)).fetchall()
        return self.conn.execute("SELECT * FROM documents WHERE job_id=?", (job_id,)).fetchall()

    def set_document_parse_status(self, doc_id: str, status: str) -> None:
        self.conn.execute("UPDATE documents SET parse_status=? WHERE id=?", (status, doc_id))

    def add_artifact(self, job_id: str, kind: str, revision: int, relative_path: str,
                     file_hash: str, byte_size: int, slide_id: str | None = None) -> str:
        existing = self.conn.execute(
            "SELECT id FROM artifacts WHERE job_id=? AND revision=? AND kind=? AND IFNULL(slide_id,'')=? AND relative_path=?",
            (job_id, revision, kind, slide_id or "", relative_path)).fetchone()
        if existing:
            return existing["id"]
        artifact_id = new_id("a")
        self.conn.execute(
            "INSERT INTO artifacts (id, job_id, kind, revision, slide_id, relative_path, file_hash,"
            " byte_size, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (artifact_id, job_id, kind, revision, slide_id, relative_path, file_hash, byte_size, now_iso()))
        return artifact_id

    def artifacts_for_revision(self, job_id: str, revision: int, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return self.conn.execute(
                "SELECT * FROM artifacts WHERE job_id=? AND revision=? AND kind=? ORDER BY created_at",
                (job_id, revision, kind)).fetchall()
        return self.conn.execute(
            "SELECT * FROM artifacts WHERE job_id=? AND revision=? ORDER BY created_at",
            (job_id, revision)).fetchall()

    def save_profile(self, template_id: str, version: int, file_hash: str,
                     profile: dict, job_id: str | None = None) -> str:
        profile_id = new_id("tp")
        self.conn.execute(
            "INSERT INTO template_profiles (id, job_id, template_id, version, file_hash, profile_json, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (profile_id, job_id, template_id, version, file_hash, json.dumps(profile, ensure_ascii=False), now_iso()))
        return profile_id

    def save_review(self, job_id: str, target_kind: str, target_id: str, target_revision: int,
                    reviewer_id: str, decision: str, comment: str = "") -> str | None:
        """唯一约束保证同一对象同决定不重复记录；重复返回 None。"""
        review_id = new_id("rv")
        try:
            self.conn.execute(
                "INSERT INTO reviews (id, job_id, target_kind, target_id, target_revision, reviewer_id,"
                " decision, comment, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (review_id, job_id, target_kind, target_id, target_revision, reviewer_id,
                 decision, comment, now_iso()))
        except sqlite3.IntegrityError:
            return None
        return review_id

    def has_review(self, job_id: str, target_kind: str, target_id: str,
                   target_revision: int, decision: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM reviews WHERE job_id=? AND target_kind=? AND target_id=? AND target_revision=? AND decision=?",
            (job_id, target_kind, target_id, target_revision, decision)).fetchone() is not None

    def save_release(self, job_id: str, job_revision: int, manifest: ReleaseManifest) -> str:
        release_id = manifest.release_id
        self.conn.execute(
            "INSERT OR REPLACE INTO releases (id, job_id, job_revision, manifest_json, created_at) VALUES (?,?,?,?,?)",
            (release_id, job_id, job_revision, manifest.model_dump_json(), now_iso()))
        return release_id

    def save_change_request(self, job_id: str, plan: dict) -> str:
        cr_id = new_id("cr")
        self.conn.execute(
            "INSERT INTO change_requests (id, job_id, plan_json, status, created_at) VALUES (?,?,'proposed',?,?)",
            (cr_id, job_id, json.dumps(plan, ensure_ascii=False), now_iso()))
        return cr_id


# ---------------------------------------------------------------- 文件存储


class FileStore:
    """私有文件目录：随机 ID 文件名，显示名称分离；写入原子（tmp→rename）。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def job_dir(self, job_id: str) -> Path:
        return self.data_dir / "jobs" / job_id

    def write_stream(self, job_id: str, kind: str, filename: str, chunks) -> tuple[Path, str, int]:
        """流式写入：限制在 job 目录内；返回 (path, sha256, size)。"""
        import hashlib

        directory = self.job_dir(job_id) / kind
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename).name or "upload.bin"
        final = directory / f"{secrets.token_hex(8)}-{safe_name}"
        tmp = final.with_suffix(final.suffix + ".part")
        h = hashlib.sha256()
        size = 0
        with tmp.open("wb") as f:
            for chunk in chunks:
                if not chunk:
                    continue
                h.update(chunk)
                size += len(chunk)
                f.write(chunk)
        if size == 0:
            tmp.unlink(missing_ok=True)
            raise ValueError("上传内容为空")
        tmp.replace(final)  # 同目录原子 rename
        return final, h.hexdigest(), size

    def write_bytes_atomic(self, relative_path: str, data: bytes) -> Path:
        """相对 data 目录写文件；tmp → rename，不产生数据库可见前的半成品。"""
        final = self.data_dir / relative_path
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = final.with_suffix(final.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(final)
        return final

    def resolve(self, relative_path: str) -> Path:
        """解析相对路径，拒绝逃逸 data 目录。"""
        p = (self.data_dir / relative_path).resolve()
        if not str(p).startswith(str(self.data_dir.resolve())):
            raise ValueError("路径逃逸")
        return p

    def free_disk_mb(self) -> int:
        import shutil

        return shutil.disk_usage(self.data_dir).free // (1024 * 1024)
