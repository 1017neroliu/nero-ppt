"""S06 集成测试：SQLite、私有存储和版本快照。"""
import sqlite3
import threading
from pathlib import Path

import pytest

from nero_ppt.db import connection as dbconn
from nero_ppt.repositories import (
    ConflictError,
    FileStore,
    JobRepo,
    RecordRepo,
    StepRepo,
)


@pytest.fixture()
def db(tmp_path):
    conn = dbconn.connect(tmp_path / "test.db")
    dbconn.migrate(conn)
    conn.execute("INSERT INTO users (id, username, password_hash, created_at)"
                 " VALUES ('u1','alice','x','now'), ('u2','bob','x','now')")
    yield conn
    conn.close()


@pytest.fixture()
def repos(db):
    return JobRepo(db), StepRepo(db), RecordRepo(db), FileStore(Path(db.execute("PRAGMA database_list").fetchone()[2]).parent)


def test_migrate_idempotent(tmp_path):
    """空库可迁移；重复启动不重复迁移。"""
    conn = dbconn.connect(tmp_path / "a.db")
    ran1 = dbconn.migrate(conn)
    ran2 = dbconn.migrate(conn)
    assert ran1 == [1, 2, 3] and ran2 == []
    conn.close()


def test_two_connections_revision_conflict(tmp_path):
    """两个连接更新同 revision 只有一个成功。"""
    db_path = tmp_path / "b.db"
    c1 = dbconn.connect(db_path)
    c2 = dbconn.connect(db_path)
    dbconn.migrate(c1)
    c1.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1','alice','x','now')")
    jobs1, jobs2 = JobRepo(c1), JobRepo(c2)
    job_id = jobs1.create("u1", "t", "general", 1)
    # 两边基于同一 revision 1 更新
    jobs1.update_state(job_id, 1, status="running")
    with pytest.raises(ConflictError) as e:
        jobs2.update_state(job_id, 1, status="failed")
    assert e.value.current_revision == 2
    c1.close()
    c2.close()


def test_step_enqueue_idempotent(db):
    steps = StepRepo(db)
    jobs = JobRepo(db)
    job_id = jobs.create("u1", "t", "general", 1)
    s1 = steps.enqueue(job_id, "outline", input_revision=1)
    s2 = steps.enqueue(job_id, "outline", input_revision=1)
    assert s1 == s2
    # 不同 scope_key 是不同步骤
    s3 = steps.enqueue(job_id, "batch", input_revision=1, scope_key="batch-0")
    assert s3 != s1


def test_worker_claim_atomic_two_workers(db):
    """两个 Worker 并发领取：同一时刻只有一个领取到同一任务。"""
    jobs = JobRepo(db)
    steps = StepRepo(db)
    job_id = jobs.create("u1", "t", "general", 1)
    step_id = steps.enqueue(job_id, "parse", input_revision=1)
    claimed = []
    lock = threading.Lock()
    db_path = db_path_of(db)  # 主线程预先解析，子线程不得复用连接

    def worker(name):
        c = dbconn.connect(db_path)
        j = JobRepo(c)
        row = j.claim_next_step_atomic(name)
        with lock:
            if row:
                claimed.append((name, row["id"]))
        c.close()
    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == 1
    assert claimed[0][1] == step_id


def db_path_of(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA database_list").fetchone()[2]


def test_lease_expiry_reclaim(db):
    """租约过期的 running 步骤可被重新领取。"""
    jobs = JobRepo(db)
    steps = StepRepo(db)
    job_id = jobs.create("u1", "t", "general", 1)
    steps.enqueue(job_id, "parse", input_revision=1)
    row = jobs.claim_next_step_atomic("w1", lease_seconds=600)
    assert row is not None
    # 模拟旧租约过期：直接改 lease 时间
    db.execute("UPDATE steps SET lease_expires_at='2000-01-01T00:00:00+00:00'")
    row2 = jobs.claim_next_step_atomic("w2", lease_seconds=600)
    assert row2 is not None and row2["id"] == row["id"]


def test_interrupted_running_on_restart(db):
    """启动时把租约过期的残留 running 标记 interrupted。"""
    jobs = JobRepo(db)
    steps = StepRepo(db)
    job_id = jobs.create("u1", "t", "general", 1)
    steps.enqueue(job_id, "parse", input_revision=1)
    jobs.claim_next_step_atomic("dead-worker")
    # 模拟旧进程死亡：租约过期
    db.execute("UPDATE steps SET lease_expires_at='2000-01-01T00:00:00+00:00'")
    n = steps.mark_interrupted_running(exclude_owner="new-worker")
    assert n == 1
    row = steps.get(steps.list_for_job(job_id)[0]["id"])
    assert row["status"] == "interrupted"


def test_revision_snapshot_immutable(db):
    """快照保存与不可变：同 revision 幂等返回。"""
    records = RecordRepo(db)
    jobs = JobRepo(db)
    job_id = jobs.create("u1", "t", "general", 1)
    rev, rid = records.save_revision(job_id, "outline", {"revision": 1, "pages": ["a"]}, input_revision=1)
    rev2, rid2 = records.save_revision(job_id, "outline", {"revision": 1, "pages": ["a"]}, input_revision=1)
    assert rev == rev2 == 1 and rid == rid2
    rev3, _ = records.save_revision(job_id, "outline", {"pages": ["b"]})  # 自动分配 2
    assert rev3 == 2


def test_artifact_unique_and_listing(db):
    records = RecordRepo(db)
    jobs = JobRepo(db)
    job_id = jobs.create("u1", "t", "general", 1)
    a1 = records.add_artifact(job_id, "pdf", 1, "revisions/1/out.pdf", "h" * 64, 123)
    a2 = records.add_artifact(job_id, "pdf", 1, "revisions/1/out.pdf", "h" * 64, 123)
    assert a1 == a2
    rows = records.artifacts_for_revision(job_id, 1)
    assert len(rows) == 1


def test_file_store_stream_and_escape(tmp_path):
    fs = FileStore(tmp_path)
    path, sha, size = fs.write_stream("j1", "inputs", "资料.docx", iter([b"hello ", b"world"]))
    assert path.exists() and size == 11 and len(sha) == 64
    assert "资料.docx" in path.name
    # 路径逃逸拒绝
    with pytest.raises(ValueError):
        fs.resolve("../outside.bin")
    with pytest.raises(ValueError):
        fs.resolve("/etc/passwd")


def test_file_store_atomic_write(tmp_path):
    fs = FileStore(tmp_path)
    fs.write_bytes_atomic("jobs/j1/exports/out.txt", b"v1")
    assert (tmp_path / "jobs/j1/exports/out.txt").read_bytes() == b"v1"
    assert not list(tmp_path.rglob("*.part"))


def test_transaction_rollback(db):
    """异常事务回滚：文档插入失败不留残留。"""
    records = RecordRepo(db)
    jobs = JobRepo(db)
    job_id = jobs.create("u1", "t", "general", 1)
    db.execute("BEGIN IMMEDIATE")
    try:
        records.add_document(job_id, "a.txt", "material", ".txt", "h", 1, "jobs/x/inputs/a.txt")
        raise RuntimeError("模拟失败")
    except RuntimeError:
        db.execute("ROLLBACK")
    assert records.documents_for_job(job_id) == []


def test_review_unique(db):
    records = RecordRepo(db)
    jobs = JobRepo(db)
    job_id = jobs.create("u1", "t", "general", 1)
    r1 = records.save_review(job_id, "outline", "rev-1", 1, "u1", "approve")
    r2 = records.save_review(job_id, "outline", "rev-1", 1, "u1", "approve")
    assert r1 is not None and r2 is None
    assert records.has_review(job_id, "outline", "rev-1", 1, "approve")


def test_foreign_keys_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO jobs (id, owner_id, title, scene_id, scene_version, created_at, updated_at)"
                   " VALUES ('x', 'no-user', 't', 'general', 1, 'now', 'now')")
