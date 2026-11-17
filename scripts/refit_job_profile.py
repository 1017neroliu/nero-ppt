#!/usr/bin/env python3
"""一次性运维脚本：重建任务模板档案（新代码预算）并重渲染整稿。

背景：渲染层标题自适应 + 预算字号修复后，已完成任务的 profile_json（DB 快照）
仍是旧预算（14pt 默认 → 57 字虚高），需重建后重跑 assemble 出新 revision。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nero_ppt.db import connection as dbconn  # noqa: E402
from nero_ppt.repositories import JobRepo, RecordRepo, StepRepo  # noqa: E402
from nero_ppt.settings import Settings  # noqa: E402
from nero_ppt.templates_engine import build_profile, inspect_template  # noqa: E402

JOB_ID = sys.argv[1] if len(sys.argv) > 1 else "j22d583cf7e75f937"

settings = Settings(data_dir=ROOT / "data", db_path=ROOT / "data" / "nero.db")
conn = dbconn.connect(settings.db_path)
jobs_repo, steps_repo, records_repo = JobRepo(conn), StepRepo(conn), RecordRepo(conn)

job = jobs_repo.get(JOB_ID)
assert job is not None, f"任务不存在: {JOB_ID}"
profile_id = job["template_profile_id"]
old_row = conn.execute(
    "SELECT * FROM template_profiles WHERE id=?", (profile_id,)).fetchone()
assert old_row is not None, f"模板档案不存在: {profile_id}"
old_profile = json.loads(old_row["profile_json"])

# 模板文件：kind=template 的文档（按存储路径）
tpl_rows = conn.execute(
    "SELECT * FROM documents WHERE job_id=? AND kind='template'", (JOB_ID,)).fetchall()
assert tpl_rows, "任务无模板文档"
tpl_path = settings.data_dir / tpl_rows[0]["stored_path"]
assert tpl_path.exists(), f"模板文件缺失: {tpl_path}"

# 新代码重建档案：template_id/version 沿用原档案（保持引用一致）
insp = inspect_template(tpl_path)
new_profile = build_profile(insp, old_profile["template_id"], source_file=str(tpl_path))
assert new_profile.file_hash == old_row["file_hash"], "模板文件哈希变化，请人工确认"

conn.execute(
    "UPDATE template_profiles SET profile_json=?, version=version+1, created_at=datetime('now')"
    " WHERE id=?",
    (new_profile.model_dump_json(), profile_id))

# 校验新预算：标题槽位字号/预算
title_budgets = []
for layout in new_profile.layouts:
    for slot in layout.slots:
        if slot.slot_id == "title0":
            title_budgets.append(f"{layout.layout_id}: font={slot.max_font_pt}pt budget={slot.text_budget_chars}")
print("新档案标题预算:", "; ".join(title_budgets))

# 任务状态：completed → final/running（assemble 前置），清残留 error
cur = conn.execute(
    "UPDATE jobs SET status='running', error='', updated_at=datetime('now')"
    " WHERE id=? AND status='completed'", (JOB_ID,))
assert cur.rowcount == 1, f"任务状态不是 completed，当前: {job['status']}"

# 入队 assemble（幂等键：scope_key=refit-{旧 profile version}）
step_id = steps_repo.enqueue(JOB_ID, "assemble",
                             input_revision=job["job_revision"],
                             scope_key=f"refit-v{old_row['version'] + 1}")
print(f"profile 已重建: {profile_id} (version → {old_row['version'] + 1})")
print(f"assemble 步骤已入队: {step_id}，任务 → final/running")
conn.close()
