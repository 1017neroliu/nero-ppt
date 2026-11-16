# S15 资源控制、启动脚本与运行文档

阶段：S15
状态：验收通过
本阶段目标：磁盘阈值、临时清理、启动脚本、运维文档。

## 实际修改文件

- src/nero_ppt/services/resources.py：check_disk（低于阈值暂停上传/渲染）、cleanup_temp（旧 tmp 目录/.part/stage1 清理，dry-run 支持，不触碰用户原文件与最终产物）
- src/nero_ppt/services/{jobs_service,render_service,release_service}.py：上传/渲染前阈值检查（上传 507、渲染步骤显式失败）
- src/nero_ppt/cli.py：cleanup 子命令（--dry-run/--max-age-hours）
- scripts/{start.sh,stop.sh}：Web+Worker 双进程启停（pid 文件 + 兜底清理）
- tests/integration/test_resources.py：4 个测试
- README.md：重写（快速开始/能力边界/运维）

## 检查结果（4 passed）

- 磁盘低于阈值：新上传 507（错误说明已有任务状态保留）✓；渲染步骤显式失败不产半成品 ✓
- 清理：48h 前 tmp 目录与 .part 删除、新目录保留、dry-run 不删除、用户目录不受影响 ✓
- CLI cleanup --dry-run 输出 JSON ✓
- 默认限额写入配置说明：单文件 30MB、每任务 100MB、60 页、磁盘 2048MB（环境变量可调）✓

## 局限

- 历史产物清理（旧 revision）暂未提供范围选择，仅有临时文件清理；用户原文件与最终产物不自动删除

## 下一步

S16 端到端验收与最终交接。
