# S13 新建任务页与审核工作台【门槛 D】

阶段：S13
状态：验收通过（浏览器工具不可用，用 TestClient 模拟完整用户链路；真实浏览器人工验收待补充）
本阶段目标：登录页、新建任务页、工作台、真实状态轮询。

## 实际修改文件

- src/nero_ppt/web/templates/{login,new_job,workbench}.html：三个页面（Jinja2 自动转义开启）
- src/nero_ppt/web/static/{app.js,styles.css}：原生 JS 无框架；textContent 动态渲染；轮询 2s 起步、失败指数退避至 30s、终态/页面卸载停止、页面不可见暂停；409 提示刷新不覆盖输入
- src/nero_ppt/web/routes/pages.py：/、/login、/new、/jobs/{id}
- src/nero_ppt/app.py：/static 挂载（仅 web/static；data 永不公开）
- src/nero_ppt/services/jobs_service.py：job_view 补充 specs_detail 与最新产物修订
- tests/integration/test_web_ui.py：4 个测试

## 检查结果（4 passed；全套 139 passed）

- 页面渲染（登录/新建/工作台壳）与静态资源 200 ✓
- data 目录不可经 /static 穿越；/jobs/{id} 页面壳不含数据内容 ✓
- 完整流程（API 模拟浏览器）：创建→上传→start→parse→手工规格→render_batch→sample/awaiting_review→缩略图 PNG 下载→批准→batch/running；batch/running 状态下批准批次被 409 拒绝（未完成批次不可批准）✓
- 旧 revision 审核返回 409+current_revision（前端提示刷新契约）✓

## 界面能力对照设计

- 新建页：资料/要求/场景/模板齐全；最近任务列表 ✓
- 工作台：左页列表/中预览/右内容+Notes+来源+修改；排队与失败状态显示 ✓
- 大纲编辑：标题修改/排序/删除（outline/awaiting_review 时可用）✓
- 无"后端未支持却可点"的按钮：模型相关按钮在 API 层显式 409 报"模型未配置" ✓

## 局限

- 无浏览器自动化工具：关键用户链路以 API 层集成测试代替，真实浏览器验收人工待补
- sample 与 batch 的 UI 操作相同（批准继续）；批次进度在顶部状态与 events 中体现

## 下一步

S14 整稿组装与最终交付【门槛 E】。
