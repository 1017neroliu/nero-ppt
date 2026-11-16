# S05 规则 QA 与有界修复【门槛 B】

阶段：S05
状态：验收通过
本阶段目标：结构/几何/内容 QA 与有界修复循环。

## 实际修改文件

- src/nero_ppt/qa/engine.py：check_structure（必需槽位空/缺失、内置占位符残留词表）、check_geometry（越界+固定对象缺失）、check_content（来源存在、必须事实）、check_overflow（容量预筛 warning）、check_notes、check_image_fit（纵横比）
- src/nero_ppt/qa/repair.py：repair_slide（槽位内字号下限修复）、run_repair_loop（最多 N 轮；无可修复动作或失败签名不变即停止；修复历史跨轮保留）
- tests/integration/test_qa.py：9 个测试

## 检查结果（9 passed）

- 正常构建通过 QA（无阻塞失败）✓
- 来源不存在 → error ✓；必须事实缺值 → not_checked 语义的 warning 提示、有值未体现 → warning ✓
- 故意残留"要点页标题"被 placeholder_leftover 捕获 ✓
- 超长标题触发 overflow_risk（warning 预筛，不宣称等同 Office 排版）✓
- Notes 缺失 → error ✓
- T13：不可自动修复问题 2 轮后停止（round_index ≤2，结果 stopped/no_change）✓
- QAReport.artifact_hash 与产物文件 sha256 匹配 ✓
- two_column 底色卡片不误判越界 ✓

## 设计要点

- 模板允许的背景包含关系（面板在画布内、文字在面板上）通过"仅检查越界与固定对象缺失"实现白名单，不把所有相交判错
- 自动修复第一版仅限槽位内字号调整；内容级问题（残留/超容量/来源）一律报告交人工或模型精简，不静默删减
- 视觉模型检查为可选适配器，未实现时 visual_checked=False（not_checked 语义保留）

## 下一步

S06 SQLite、私有存储和版本快照。
