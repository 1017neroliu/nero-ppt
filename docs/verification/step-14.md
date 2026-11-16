# S14 整稿组装与最终交付【门槛 E】

阶段：S14
状态：验收通过（LibreOffice 替代验证；目标演示软件人工验收待完成）
本阶段目标：发布清单、整稿组装、Markdown 讲稿、发布事务。

## 实际修改文件

- src/nero_ppt/services/release_service.py：assemble_impl（完整 PPTX→PDF→讲稿→全稿 QA→页数三方校验→产物登记→release_draft 快照→final/awaiting_review）、publish（发布事务：manifest 文件存在+哈希+大小校验 → releases 登记 + completed 同事务）
- src/nero_ppt/services/jobs_service.py：final 批准接入发布事务；批次进度修正（batch_index 递进、每批 5 页）
- src/nero_ppt/services/render_service.py：render_batch 保存渲染修订快照（占住 revision 号，修复产物撞号）
- src/nero_ppt/rendering/libreoffice.py：pdf_page_count 支持内存流
- tests/integration/test_release.py：3 个测试

## 检查结果（3 passed；全套 142 passed）

- T14：4 页整稿 PPTX+PDF+Markdown 讲稿同版本同序（PDF 页数=4，讲稿"## 第 "计数=4，PPTX Notes 可读回封面讲稿）✓
- 发布事务：最终批准 → releases 表 manifest（slides 4 项 + 三份核心产物哈希）+ completed 原子完成 ✓
- T15：产物删除后批准 → 409 publish_failed，状态回 awaiting_review，releases 表无记录（无半发布）✓
- 产物篡改（哈希不匹配）→ 拒绝发布 ✓
- 修复记录：render_batch 未保存修订快照导致 revision 撞号 → 补 save_revision(kind='render')；batch_index 递进逻辑修正

## 兼容性声明

- 整稿从同一模板包与受控 RenderPlan 构建（非 ZIP 拼接）；目标软件打开验证待人工补充

## 下一步

S15 资源控制、启动脚本与运行文档。
