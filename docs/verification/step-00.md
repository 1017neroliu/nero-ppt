# S00 环境检查与范围锁定

阶段：S00
状态：验收通过
本阶段目标：环境检查、范围锁定、技术栈决定。

## 执行命令及结果

- `python3 --version` → Python 3.10.12（唯一版本）
- `soffice --version` → LibreOffice 26.2.6.3
- `pdftoppm -v` → 22.02.0
- `fc-list :lang=zh` → Noto Sans/Serif CJK SC 等中文字体可用
- `df -h /` → 剩余 19G
- pip list 确认：fastapi 0.111.0 / uvicorn 0.30.1 / pydantic 2.10.4 / python-pptx 0.6.23 / python-docx / pypdf 4.2.0 / openai 2.33.0 / pytest 9.0.3 / httpx 0.27.0 / python-multipart / aiofiles / pillow 均已安装
- `git status --short`：共享仓库存在其他项目未提交修改（mp-demo 等），本次只改 nero-ppt/ 内文件，不触碰其他项目

## 决定

见 docs/decisions/0001-mvp-stack.md：锁定 Python 3.10（偏离任务书 3.11+ 默认，理由：本机唯一版本）、venv + pyproject 声明依赖、内置模板程序化生成、OpenAI 兼容 provider + FakeProvider。

## 可复用组件

LibreOffice（PDF 转换）、Poppler（缩略图）、全部 Python 依赖已就绪，无需额外安装。

## 缺失组件与风险

- 无真实模型密钥（S10 用 FakeProvider，真实调用标待验收）
- 无 PowerPoint/WPS 目标软件（兼容性验收以 LibreOffice 实测为准）
- 磁盘 92% 已用（19G），渲染前实施磁盘阈值检查

## 下一步

S01 工程骨架、配置、协议与测试入口。
