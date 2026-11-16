# S08 资料解析与可追溯素材

阶段：S08
状态：验收通过
本阶段目标：统一 Parser 接口、四类适配器、确定性事实提取。

## 实际修改文件

- src/nero_ppt/parsers/base.py：接口、超长块按句切分（保留全文）、确定性 Fact 提取（数字+单位 / 日期 / 季度，含主题与时间上下文回溯）、统一入口 parse_document
- src/nero_ppt/parsers/{pdf,docx,text,pptx}.py：PDF 逐页（page locator，扫描件 needs_input）；DOCX 段落/表格索引；TXT/MD 行范围（GBK 降级并显式报告）；PPTX 页内文本/表格/备注（slide_id/shape_id）
- tests/integration/test_parsers.py：12 个测试

## 检查结果（12 passed）

- DOCX：数值事实（4,820 万元）可追溯至段落 locator；表格结构保留 ✓
- PDF：页码 locator；事实单位提取；不制造非 PDF 定位 ✓
- TXT/MD：行范围 locator（end≥start）；12 万/78%、15 万元、4.8 秒事实提取 ✓
- PPTX 资料：slide_id/shape_id 定位；87.5% 提取 ✓
- T06：纯图片 PDF（PIL 生成）→ needs_input 且错误信息含"扫描件" ✓
- T07：冲突口径（100 万元 vs 120 万元）同时保留为两个事实，不合并 ✓
- 编码：非 UTF-8/GBK 拒绝并报告；GBK 自动转码且 parse_error 显式标注 ✓
- .xlsx 不支持显式拒绝；ParsedContent 序列化往返一致 ✓

## 下一步

S09 任务状态机、Worker 与恢复（门槛 C）。
