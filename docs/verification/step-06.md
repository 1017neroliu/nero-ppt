# S06 SQLite、私有存储和版本快照

阶段：S06
状态：验收通过
本阶段目标：数据结构、迁移、事务仓储、私有文件存储。

## 实际修改文件

- src/nero_ppt/db/connection.py：连接（foreign_keys/busy_timeout/WAL/synchronous=NORMAL）与幂等迁移（complete_statement 逐语句解析，显式事务）
- src/nero_ppt/db/migrations/001_initial.sql：users/sessions/jobs/documents/template_profiles/revisions/steps/artifacts/reviews/change_requests/releases/schema_migrations；唯一约束含 revision(job_id,revision)、step(job_id,input_revision,kind,scope_key)、review 五元组
- src/nero_ppt/repositories/store.py：JobRepo（乐观锁 update_state、claim_next_step_atomic 单条 UPDATE 领取+租约）、StepRepo（幂等入队/完成/失败/重试/中断标记）、RecordRepo（不可变快照幂等、artifacts 唯一、review 唯一）、FileStore（随机 ID 流式写入 tmp→rename、路径逃逸拒绝）
- tests/integration/test_persistence.py：12 个测试

## 检查结果（12 passed）

- 空库迁移成功；重复启动返回空列表（不重复迁移）✓
- 两个连接基于同 revision 更新：一个成功一个 ConflictError(current=2) ✓
- 步骤幂等入队（同键同 ID；不同 scope_key 不同步骤）✓
- 4 个线程并发领取同一步骤：仅 1 个成功（原子 UPDATE + BEGIN IMMEDIATE）✓
- 租约过期可被重新领取 ✓；启动时残留 running → interrupted ✓
- 快照同 revision 幂等；同库新快照自动递增 revision ✓
- artifacts/review 唯一约束幂等 ✓；事务回滚无残留 ✓；外键强制（无主 users 插入 jobs 失败）✓
- FileStore 流式写入哈希/大小正确；../ 与绝对路径逃逸拒绝；原子写入无 .part 残留 ✓

## 修复记录

- sqlite3.executescript 会隐式提交，破坏迁移原子性 → 改为 complete_statement 逐语句执行
- documents INSERT 列数/占位符不匹配 → 修正

## 下一步

S07 简单鉴权与受限上传。
