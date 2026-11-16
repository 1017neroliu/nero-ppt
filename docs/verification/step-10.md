# S10 模型适配器与场景/大纲生成

阶段：S10
状态：验收通过（FakeProvider 链路验收；真实模型调用待凭据，标记待验收）
本阶段目标：provider 接口、Fake/OpenAI 适配器、场景配置、大纲生成。

## 实际修改文件

- src/nero_ppt/providers/base.py：ModelProvider 协议、FakeProvider（显式测试）、OpenAIProvider（chat.completions json_object、超时、有限重试、用量记录）、parse_model_json（剥离围栏）、validate_model_output（应用端结构校验）、SYSTEM_RULES（资料指令非命令/禁坐标/禁编造/禁联网/禁审批状态）
- src/nero_ppt/planning/scenarios.py + config/scenarios/general.json：场景加载、general 通用场景
- src/nero_ppt/planning/outline.py：事实表+原文摘要（保留 fact_id/block_id）→ 大纲；逐页 SlideSpec（只传相关事实与槽位预算）；worker 步骤入口（无 provider 显式失败）
- tests/integration/test_planning.py：10 个测试

## 检查结果（10 passed）

- 四个场景（含 general）加载；缺失场景 KeyError ✓
- FakeProvider 大纲/逐页规格生成，结构校验通过 ✓
- 伪造来源识别（服务层校验函数按已知 source_id/block_id 比对，p2 伪造被报出）✓
- T16：提示词声明资料指令非命令，输出不含审批/发布状态字段 ✓
- 非 JSON 输出/围栏剥离 ✓；OpenAIProvider 无密钥构造即报错 ✓
- 显式场景选择优先于自动推荐 ✓

## 待验收项

- 真实模型调用（需 NERO_MODEL_API_KEY）：完整链路已按 OpenAI 兼容协议实现，凭据就绪后需实测一轮
- FakeProvider 输出不得作为真实模型能力证据

## 下一步

S11 逐页内容、讲稿与局部修改。
