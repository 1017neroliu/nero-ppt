# S11 逐页内容、讲稿与局部修改

阶段：S11
状态：验收通过
本阶段目标：批准大纲→逐页规格（带槽位预算）、ChangePlan 解释与校验、单页修订。

## 实际修改文件

- src/nero_ppt/planning/slides.py：generate_specs（版式→槽位预算摘要传模型）、plan_change（自然语言→ChangePlan，只解释不执行）、validate_change_plan（未知页面/全局未声明影响拒绝）、apply_page_change（无关页同一对象、slide_id 身份稳定校验）、specs_revision_payload（整体快照）
- src/nero_ppt/contracts/change.py：ChangePlan/ChangeItem 严格协议
- tests/integration/test_slides.py：7 个测试

## 检查结果（7 passed）

- 规格生成：每页一次调用、预算提示传入、讲稿非空 ✓
- ChangePlan：page 范围解析与校验通过；global 必须声明模板/大纲影响 ✓；未知页面拒绝 ✓
- T12 基础：单页修改后无关页 model_dump_json 逐字节一致；原列表不可变语义；slide_id 漂移拒绝 ✓
- 快照往返一致 ✓

## 说明

- 精简修复已由 S05 run_repair_loop 承担（≤2 轮，不循环调模型）；S11 层复用
- ChangePlan 执行入口在 S12 API（changes/plan → 用户确认 → apply）

## 下一步

S12 业务 API 与审核事务。
