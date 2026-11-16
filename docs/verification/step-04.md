# S04 PDF 转换、缩略图和版本产物

阶段：S04
状态：验收通过
本阶段目标：LibreOffice 子进程封装、Poppler 缩略图、产物校验与原子移动。

## 实际修改文件

- src/nero_ppt/rendering/libreoffice.py：参数数组调用、独立临时目录与用户配置（-env:UserInstallation）、超时 killpg 终止进程组、退出码/文件存在/非空/页数校验、out_path 原子移动（tmp→os.replace）+ sha256
- src/nero_ppt/rendering/preview.py：pdftoppm 缩略图
- tests/integration/test_render.py：7 个测试

## 检查结果（7 passed）

- PPTX 与 PDF 页数对应（7 页，960x540pt）✓
- 中文渲染无缺字：pdftotext 抽取"核心经营指标/营业收入/四季度交付流程" ✓
- 缩略图来自同一 PDF，页数一致 ✓
- 输入缺失/为空拒绝 ✓
- 超时（fake soffice sleep 300）：3s 抛"超时"且无 PDF 残留、进程组被杀 ✓
- 独立 profile 目录存在 ✓
- 产物哈希记录 ✓

## 下一步

S05 规则 QA 与有界修复（门槛 B）。
