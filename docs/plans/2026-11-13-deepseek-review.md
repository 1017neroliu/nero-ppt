# DeepSeek 建议评审与整合方案

日期：2026-11-13。范围：公开项目说明与现有设计的静态评审；未安装或运行被评审项目，未取得用户过去失败的 PPT/日志。因此不能把候选原因当作已验证根因，也不能宣称已有最佳实测方案。

## 1. 结论

采用内容生成与确定性布局分离、结构化页面协议、来源追溯、实际回渲和分层 QA。调整通用布局引擎方案为“模板槽位优先，内置版式受限组合”。不直接采用多 Agent、通用 Flexbox DSL、HTML 作为严格模板主路径或无限自动修复。轻量 Web 工具形态保持不变。

## 2. 逐项评审

| 建议 | 结论 | 整合方式 |
| --- | --- | --- |
| LLM 不输出坐标或任意渲染代码 | 采用 | 模型只提交结构化内容、视觉意图和允许的版式候选 |
| 空间计算是失败根因 | 仅作假设 | 检查输入遗漏、结构取舍、模板破坏、字体与渲染差异等其他原因 |
| 结构化资料与来源锚点 | 采用 | 保存原文、位置、单位、时间、抽取方式与冲突；摘要不是事实唯一来源 |
| sayou-document 作为默认解析器 | 候选 | 通过 Parser 接口隔离，使用真实文档比较；不由 README 判断保真程度 |
| Research/Outline/Style 独立 Agent | 暂不采用 | 同一模型分阶段调用、按需上下文与共享事实表即可 |
| SlideSpec JSON | 采用并扩展 | 增加来源、必须保留的事实、版式绑定、修订和备注 |
| compono / 通用约束布局引擎 | 部分采用 | 借鉴 validate/render 协议，作为内置自由版式候选，不作为用户模板无损保证 |
| 几百行通用布局 DSL | 不作为工期或质量承诺 | 首版实现有限版式、内容容量和适配规则，不造通用排版引擎 |
| python-pptx Builder | 采用并限制边界 | 已适配对象的确定性编辑；资源引用和文本样式保留单独验证 |
| 母版与占位符优先 | 扩展后采用 | 同时识别普通文本框、组合对象和已有页面的固定装饰 |
| 视觉 QA 回路 | 采用 | 机械检查、实际渲染、多模态建议、用户确认四层职责 |
| DOM/CSS 重构 | 不作为主路径 | 只适用于 HTML 实现路线，不能直接修复 OOXML 模板 |
| CLI 验证后接 UI | 采用验证顺序 | CLI 是内部验证工具，最终用户入口仍是已确定的网页 |

## 3. 项目核验与使用决策

- compono：PyPI 项目说明提供结构化原语、validate/render 和设计检查，并声明生成可编辑 OOXML。可作为候选适配器。已核对的 0.1.3 文档把 template 定义为配置文件；不能把这种模板机制直接视为任意上传 PPTX 的对象保留能力。当前版本是否满足中文与自有模板需求必须另测；旧版字体限制不能直接外推为当前缺陷。
- sayou-document：官方资料说明统一 DOM 及 Office/PDF 解析。可比较，但尚未实测备注、复杂表格和来源定位的完整性。
- Docling：官方格式列表和技术报告支持统一结构化文档路线，建议作为复杂资料解析的首个比较候选。与源 PPTX 模板编辑是两个独立职责。
- MinerU：作为扫描件与复杂 PDF 的按需候选；锁定实际采用版本并核对该版本许可和运行资源，不与其他解析器同时默认装入。
- PPTAgent / DeepPresenter：论文和仓库支持参考演示稿分析、编辑式生成与基于渲染观察的修订。借鉴方法，作为对照样例，不整体引入完整研究/沙箱/工具系统。
- PPT Master：仓库声明原生对象、模板及备注相关能力，适合作为可编辑性与模板输出的对照，不以 stars 或仓库自述代替本项目验收。
- duduppt：仓库有证据驱动叙事、阶段确认与 QA 流程，借鉴检查表。SCR 可用于客户方案，不能强制所有技术/培训场景使用。
- pptx-creator：搜索发现同名技能，未能核实所述 text-to-html/html-to-pptx 与 presentation-package.json 对应的确切原仓库，暂不列为确定依赖。
- WPS“20 余脚本”：相关描述出现在社区用户文章；官方使用文档能确认 python-pptx 等工具环境，但不足以独立证明内部完整架构，不能作为选型依据。

上述属于公开资料核对，未完成源码全面审计、依赖安装、许可适配审查或样例运行。

## 4. 调整后的数据链路

原始资料 → SourceBlock / Fact → 场景大纲 → 语义 SlideSpec → TemplateProfile 与版式绑定 → 内容适配检查 → RenderPlan → PPTX + Notes → PDF → QAReport → 审核/有限修复 → 发布。

### Fact

保存 fact_id、source_id、定位信息、原始片段、值/单位/时间、抽取方式、verification_status。OCR 置信度仅表示识别质量；模型自报 confidence 不是事实正确概率。来源存在也不等于结论得到支持，重要结论需检查对应证据。

### TemplateProfile

保存固定对象、可编辑槽位、槽位对象 ID、版式用途、允许内容类型、字号下限、行数/段数预算、图片裁切规则、字体资源、模板哈希。预算是预筛规则，最终效果以真实回渲检查为准。

### SlideSpec

purpose、headline、body_blocks、visual_intent、density、source_refs、must_keep_fact_ids、speaker_notes、target_seconds。visual_intent 从有限枚举选取。密度是意图，不替代容量检查。模型不提供 x/y/w/h。

### RenderPlan

程序生成 template_slide_ref、slot_bindings、最终文字、对象编辑动作、坐标、资源和样式引用。坐标来源于原模板或经过验收的内置布局规则。与 SlideSpec 分开存储，便于定位是内容问题还是渲染问题。

### QAReport

保存 slide_id、revision、object_id/slot_id、check_id、severity、observed、expected、suggested_action，以及执行状态 pass/fail/not_checked。检查器缺字体或不可用时为 not_checked，不能假装通过。

## 5. 两类布局策略，尽量共用渲染实现

用户上传模板：复制并编辑已适配原页面，保持固定对象和版式约束；内容不合适时调整内容或选其他模板页，不让通用 resolver 重排整页。

内置模板：预先设计有限版式和槽位，可在指定区域内做表格列宽、图表尺寸、图文分栏等受限计算。只有明确需要自由组合时，才评估 compono 适配器；首版无需两套渲染引擎。

选择模板并完成能力分析应先于最终分页。大纲可以先粗规划，但逐页内容生成要拿到候选槽位预算。不能先写满文字，再把溢出问题完全留给 QA。

文本编辑保留段落/Run 级格式与主题继承；python-pptx 文档表明直接赋值 shape.text 等价于清空后放入新 Run，因此不能用于所有严格模板替换。

## 6. 有界修复策略

建议每页最多 2 轮自动修复，属于初始工程预算，后续按实测调整。

1. 程序修复可确定的问题：允许区域内的尺寸、图片裁切和绑定错误。
2. 同一模板中选择容量合适的已验收版式。
3. 要求模型精简表达，保留 must_keep_fact_ids、数字、单位和限定条件。
4. 拆页或移入备注/附录；影响已批准大纲、页数或讲述时长时提交变更说明，等待批准。
5. 超过预算、重复无改善或需要修改固定样式时停止，显示问题并要求用户处理。

不存在“让模型随意重写直到看着好看”的默认动作。任何正文变化同步更新讲稿。视觉 QA 只能提出候选问题，不能单独判定数字正确或代替用户批准。

重叠检查区分有意包含/叠放（例如文字放在底色卡片上）与有害遮挡；模板保真检查排除预期内容变化，比较固定对象属性与受保护区域，不能把整页像素差都当错误。

## 7. 验证最佳方案的方法

固定同一份人工审核过的 SlideSpec，先隔离模型差异：

- A：模板槽位与原页面保留（推荐基线）。
- B：有限布局规则或 compono 候选（主要验证内置模板）。
- C：选取 PPTAgent 或 PPT Master 作为端到端输出对照，不要求首版集成。

用 2–3 份真实困难文档，覆盖中文长标题、密集表格、多母版、图文混排、单位/时间限定和来源冲突。记录模板固定元素漂移、严重排版缺陷、证据遗漏、编辑性、Notes 一致性、人工修订时间、生成耗时和模型费用。

候选库未达到目标时保持可替换适配器，不能为了采用项目而降低严格模板要求。通过确定性链路后再接入模型，比较自由生成与受约束内容生成的质量。最后接审核网页。

## 8. 官方/原作者资料索引

核对日期为 2026-11-13；在线内容可能变化，正式采用时需固定版本或提交。

- compono：`https://pypi.org/project/compono/`；模板语义示例：`https://pypi.org/project/compono/0.1.3/`
- sayou-document：`https://hub.sayouzone.com/libraries/sayou-document`
- Docling：`https://github.com/docling-project/docling/blob/main/docs/usage/supported_formats.md`
- MinerU：`https://github.com/opendatalab/MinerU`
- PPTAgent / DeepPresenter：`https://github.com/icip-cas/PPTAgent`；`https://arxiv.org/abs/2501.03936`；`https://arxiv.org/abs/2602.22839`
- PPT Master：`https://github.com/hugohe3/ppt-master`
- duduppt：`https://github.com/Shine8592/duduppt`
- python-pptx 文本语义：`https://python-pptx.readthedocs.io/en/latest/user/text.html`
- WPS 官方使用文档：`https://www.lingxi.cn/docs`
