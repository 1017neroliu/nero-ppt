# Nero PPT 首版端到端验收报告

日期：2026-11-16。范围：S00–S16 全部阶段。

## 1. 总体结论

首版交付完成：模板驱动的确定性 PPTX/Notes/PDF 链路、四阶段审核流（大纲/样张/批次/整稿）、双进程（Web + 单 Worker）、SQLite 持久化、简单鉴权与受限上传、发布事务。全部自动化测试 **152 项通过**；五个门槛（A/B/C/D/E）均达成自动化验收标准。真实模型调用与目标演示软件打开验证因外部依赖缺失显式标记为**待验收**，未打全通过标记。

## 2. 交付物清单

| 交付物 | 位置 |
| --- | --- |
| 应用代码 | `src/nero_ppt/`（contracts/db/parsers/templates_engine/rendering/qa/services/web/workflow/providers/planning，约 30 个模块） |
| 内置模板 | `assets/templates/builtin-classic.pptx`、`builtin-minimal.pptx`（脚本可重建） |
| 场景配置 | `config/scenarios/`（business-review/customer-proposal/technical-solution + 新增 general） |
| 测试 | `tests/unit/`（协议 11 项）+ `tests/integration/`（141 项） |
| 脚本 | `scripts/build_builtin_templates.py`、`build_fixtures.py`、`start.sh`、`stop.sh` |
| 文档 | `README.md`、`docs/decisions/0001-mvp-stack.md`、`docs/verification/step-00~16.md` |
| 工程配置 | `pyproject.toml`（requires-python>=3.10,<3.12）、`.env.example`、`.gitignore` |

## 3. 验收矩阵覆盖情况

| ID | 场景 | 结果 | 验证位置 |
| --- | --- | --- | --- |
| T01 | 长中文标题/混合字体 | ✅ 容量预筛 warning + 渲染验证 | test_qa.py |
| T02 | 多页同图表不污染 | ✅ chart+xlsx 每页克隆 | test_template_build.py |
| T03 | 普通文本框适配 | ✅ 外部模板启发式槽位 | test_template_inspect.py |
| T04 | 跨母版/POTX | ✅ 包类型规范化 | test_template_build.py |
| T05 | 缺字库 | ⚠️ doctor 报告字体；LibreOffice 渲染正常（目标软件待验） | cli doctor |
| T06 | 扫描 PDF | ✅ needs_input 不生成空内容 | test_parsers/test_worker |
| T07 | 数字口径冲突 | ✅ 事实并存不合并 | test_parsers.py |
| T08 | 旧审核/同时批准 | ✅ 409+current_revision；request_id 幂等；唯一约束 | test_api/test_persistence |
| T09 | Worker 中断/取消 | ✅ 租约恢复、取消拦截、孤儿产物不发布 | test_worker |
| T10 | 两用户隔离 | ✅ 列表/状态/下载/修改全隔离 | test_api |
| T11 | 恶意 ZIP | ✅ 条目数/解压量/路径逃逸拒绝 | test_access_upload |
| T12 | 单页修改 | ✅ 无关页逐字节不变；slide_id 稳定 | test_slides |
| T13 | 两轮修复仍失败 | ✅ 停止待用户，无循环 | test_qa |
| T14 | 最终三份产物一致 | ✅ 页数/顺序/Notes 哈希校验 | test_release |
| T15 | 渲染失败/磁盘不足 | ✅ 无完成状态、无半发布；507/显式失败 | test_release/test_resources |
| T16 | 资料恶意指令 | ✅ 提示词边界 + 无审批状态输出 | test_planning |
| T17 | 缺失必需检查 | ✅ not_checked/warning 语义，不假通过 | test_qa |

## 4. 真实验证记录

- **渲染链路实测**：内置模板 × 2、外部风格测试稿、手工规格 7 页全链路；LibreOffice 26.2.6.3 转 PDF（7 页 960×540pt），pdftoppm 出图后 OCR 抽查内容正确、品牌装饰保留（蓝色像素 6.7%）
- **Worker 实测**：4 线程并发领取唯一成功；kill 模拟恢复；取消后不发布；重复执行不重复登记产物
- **发布事务实测**：产物删除/篡改 → 拒绝发布且回退 awaiting_review，releases 表无半发布记录
- **Web 冒烟**：uvicorn 真实启动（healthz 200 / 页面 307→/new / static 200 / request_id 中间件）

## 5. 待验收项与剩余风险（明确不打全通过）

| 项 | 阻塞原因 | 建议动作 |
| --- | --- | --- |
| 真实模型调用 | 未提供 NERO_MODEL_API_KEY | 配置密钥后跑一个真实任务；链路已按 OpenAI 兼容协议就绪 |
| PowerPoint/WPS 打开兼容性 | 本机仅 LibreOffice | 在目标软件打开 `data/tmp-e2e/*-e2e.pptx` 与发布产物人工核验 |
| 真实客户模板 | 无外部客户文件 | 用客户模板走一次样张验收流程 |
| 浏览器人工验收 | 无浏览器自动化工具 | 人工按 test_web_ui.py 流程走查界面 |

## 6. 已知局限

1. 未提交 git（共享仓库含其他项目未提交修改，按 AGENTS.md 不主动提交）。
2. 历史旧 revision 产物清理未提供范围选择（仅临时文件清理）。
3. 批次切分固定每批 5 页（工程默认，未做配置项）。
4. HTTPS Secure cookie 由反代部署决定，应用内未强制。
5. FakeProvider 输出不得作为真实模型能力证据。

## 7. 复现命令

```bash
cd nero-ppt
python3 -m pytest tests/ -q -p no:warnings      # 152 passed
python3 scripts/build_builtin_templates.py assets/templates/builtin-classic.pptx classic
python3 scripts/build_builtin_templates.py assets/templates/builtin-minimal.pptx minimal
python3 -m nero_ppt.cli doctor
scripts/start.sh && curl http://127.0.0.1:8630/healthz && scripts/stop.sh
```
