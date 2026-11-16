# S09 任务状态机、Worker 与恢复【门槛 C】

阶段：S09
状态：验收通过
本阶段目标：两层状态机、步骤处理器、单 Worker 串行执行与恢复。

## 实际修改文件

- src/nero_ppt/workflow/states.py：phase×status 转换表（任务书第 5 节逐行）+ 前置校验
- src/nero_ppt/workflow/handlers.py：parse/outline/render_batch/assemble 处理器 + StepContext（检查点、基于当前 revision 的乐观锁更新入口）
- src/nero_ppt/workflow/runner.py：Worker（原子领取、取消检查、失败转存、租约恢复）
- src/nero_ppt/services/render_service.py：确定性渲染步骤（builder→PDF→QA 修复→artifacts 登记→状态推进）
- src/nero_ppt/repositories/store.py：mark_interrupted_running 仅重置租约过期步骤
- src/nero_ppt/cli.py：worker / recover 子命令
- tests/integration/test_worker.py：8 个测试；test_persistence.py 调整 1 处语义

## 检查结果（test_worker 8 passed）

- 转换表：11 条正常路径 + cancel/fail/retry 异常路径 + 非法转换拒绝 ✓
- 完整流程：parse → outline/running（事实/解析产物已登记）→ outline 步骤因模型未配置显式失败（不伪造成功）→ retry 路径可重新领取 ✓
- 幂等：重复点击只入队一次（同键同 ID）✓
- T09：取消后任务不再被领取；已领取后取消在执行前拦截，在途文件不发布 ✓
- Worker 被杀（模拟过期租约）→ recover 标记 interrupted → requeue → 新 Worker 完成同一 step ✓
- 第二个 Worker 启动不重置租约未过期的 running 步骤 ✓
- 确定性渲染端到端（无模型）：手工 SlideSpec → render_batch → PPTX+Notes+PDF+缩略图 → sample/awaiting_review；PDF 页数 3；Notes 可读回；重跑同输入 artifacts 幂等不重复发布 ✓
- 扫描件 → intake/needs_input，不生成空内容 ✓

## 修复记录

- 步骤入队 input_revision 错用 parse 输入版本 → 用完成后的当前 revision
- 步骤内状态更新误用步骤 input_revision 做乐观锁 → StepContext.update_job_state 基于当前 revision
- mark_interrupted_running 误重置活跃租约 → 仅重置过期租约（符合任务书"不能第二个 Worker 启动就重置正在执行的步骤"）

## 下一步

S10 模型适配器与场景/大纲生成。
