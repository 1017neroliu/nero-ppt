# S03 确定性 PPTX Builder【门槛 A】

阶段：S03
状态：验收通过（LibreOffice 替代验证；PowerPoint/WPS 人工验收待完成）
本阶段目标：SlideSpec → RenderPlan → PPTX/Notes，不接模型。

## 实际修改文件

- src/nero_ppt/templates_engine/ooxml.py：页面克隆（共享图片/版式，克隆图表+内嵌 xlsx）、保留格式文本/要点/表格编辑、图片替换、POTX 规范化
- src/nero_ppt/templates_engine/binding.py：visual_intent → 版式选择、槽位绑定、预算预筛
- src/nero_ppt/rendering/{builder,notes}.py：两阶段构建（包构建→内容编辑）、讲稿写入/Markdown 导出
- tests/integration/test_template_build.py：14 个测试
- tests/fixtures/handmade-spec.json：人工 SlideSpec（7 页）
- 内置模板补充 image_text 版式（含真实图片资源）

## 关键实现决策

- 克隆策略：slide XML deepcopy；RT.NOTES_SLIDE 关系丢弃（新页重写讲稿）；图片/版式/母版共享；chart part + 内嵌 xlsx 每页克隆（T02 防污染）
- python-pptx 保存时自动丢弃不可达孤儿 part，输出包内仅保留新页面
- 富文本：以第一个带 run 的段落为样式原型复制段落；不使用 shape.text 整框替换
- 表格列数必须匹配，行数自动增删（复制末行样式）；流程步骤超出槽位显式 BindingError

## 检查结果（tests/integration/test_template_build.py，14 passed）

- 可编辑文本/图片/原生表格/原生柱状图 ✓（读回验证内容与数据）
- 固定对象几何保留（sidebar 0,0,180000,6858000）✓
- 关系无悬空（逐 slide 解析 rels 目标存在）✓
- T02：两页图表数据互不污染 ✓（chart part 与 xlsx 独立克隆）
- Notes 可读回（script/转场/时长结构）✓
- 流程页 4 步写入 ✓；6 步超 4 槽位 → BindingError ✓
- 图片槽位替换 ✓；POTX 规范化后 python-pptx 可打开 ✓
- 真实渲染验证：LibreOffice 26.2 转换 7 页 PDF（960x540pt 16:9），pdftoppm 出图后 OCR 抽查：封面标题、各页标题、图表轴刻度与图例、双栏/总结内容均正确呈现；branding 蓝色像素占比 6.7%（侧条+色条保留）✓

## 兼容性声明

- 本机无 PowerPoint/WPS：目标软件打开验证标记为人工验收待完成（不宣称已通过）
- 后续 S04 将 PDF 转换固化为产物链路

## 下一步

S04 PDF 转换、缩略图和版本产物。
