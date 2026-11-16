# S12 业务 API 与审核事务

阶段：S12
状态：验收通过
本阶段目标：第 6 节 API 契约、审核事务、幂等与错误结构。

## 实际修改文件

- src/nero_ppt/services/jobs_service.py：创建/列表/视图/上传/start/outline 编辑/review 事务/cancel/retry/changes plan+apply/产物鉴权下载/幂等键
- src/nero_ppt/web/routes/api.py：全部路由（含 FileResponse 下载、events 轮询、Origin 校验）
- src/nero_ppt/db/migrations/002_idempotency.sql：幂等键表
- src/nero_ppt/workflow/handlers.py：regenerate_pages 处理器（ChangePlan 执行）
- tests/integration/test_api.py：9 个测试

## 检查结果（test_api 9 passed）

- 生命周期：创建→上传→start→parse→outline 失败（模型未配置显式报错）→ retry ✓
- T10：两位用户列表/状态/审核/下载全部隔离（归属不匹配统一 404）✓
- T08：旧 revision 批准 → 409 + current_revision；相同 request_id 幂等返回；同对象重复批准不重复推进 ✓
- 审核要求 awaiting_review 状态（409）✓
- 上传阶段/类型限制（intake/running 后上传 409；exe 422）✓
- 取消 + 事件轮询（after 游标）✓
- scenarios/templates 列表 ✓

## 修复记录

- start 内部模板绑定先于乐观锁检查导致假冲突 → 先锁状态再绑模板，返回最终 revision
- sniff_signature 文本检查误用 16 字节切片截断 UTF-8 多字节字符 → 全量解码检查
- api.py 重写时遗漏 auth 路由注册 → 补回

## 下一步

S13 新建任务页与审核工作台【门槛 D】。
