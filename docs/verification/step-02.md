# S02 测试素材、内置模板与模板检查器

阶段：S02
状态：验收通过
本阶段目标：自有内置模板、示例素材、检查器与 TemplateProfile。

## 实际修改文件

- scripts/build_builtin_templates.py：程序化生成内置模板（可重建、来源明确）
- assets/templates/builtin-classic.pptx：9 版式（cover/section/bullets/two_column/table/chart/process/summary/image_text），nero.* 命名约定 + notes 元数据
- scripts/build_fixtures.py + tests/fixtures/*：DOCX/PDF/TXT/MD/PPTX 资料、外部风格测试稿、手工 SlideSpec
- src/nero_ppt/templates_engine/{inspect,profile}.py：检查器与档案构建
- tests/integration/test_template_inspect.py：9 个测试

## 检查结果

- 每个示例对象可定位（shape_id 页内唯一）✓
- 表格/图表/图片对象识别 ✓；图表页 chart part 类型与数据可读 ✓
- 档案回指模板 sha256 ✓；固定对象（sidebar/band/divider/面板/箭头）识别为 protected ✓
- 外部风格测试稿：启发式用途建议 + compatibility 显式标注"启发式识别需确认" + 超链接报告 ✓
- 不支持扩展名（.ppt）拒绝 ✓
- 版式槽位预算按字号估算（text_budget_chars/line_budget），标注为预筛

## 局限

- 尚无真实客户模板：外部验收用 external-style.pptx（独立构造）代替，标记待补充
- SmartArt/OLE 等不支持对象在实际客户文件中的表现待外部验收

## 下一步

S03 确定性 Builder（门槛 A）。
