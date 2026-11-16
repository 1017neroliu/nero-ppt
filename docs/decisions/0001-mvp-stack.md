# 0001：MVP 技术栈与环境决定

日期：2026-11-13。状态：已确认。

## 环境检查结果（实测）

| 组件 | 实测版本 | 状态 |
| --- | --- | --- |
| Python | 3.10.12（/usr/bin/python3，仅此一个版本，无 3.11+） | 可用；任务书要求 3.11+，本机只有 3.10，锁定 3.10，代码不使用 3.11 专属特性（如 typing.Self、except*） |
| LibreOffice | 26.2.6.3（/usr/bin/soffice） | 可用，用于 PPTX→PDF |
| Poppler | pdftoppm/pdfinfo 22.02.0 | 可用，用于缩略图与页数校验 |
| 中文字体 | Noto Sans/Serif CJK SC、AR PL UMing/UKai | 可用，LibreOffice 渲染中文有保障 |
| 磁盘 | / 剩余 19G（92% 已用） | 满足初期 5–8 GB 预算；渲染前需检查阈值 |
| fastapi | 0.111.0 | 已安装 |
| uvicorn | 0.30.1 | 已安装 |
| pydantic | 2.10.4 | 已安装 |
| jinja2 | 3.1.2 | 已安装 |
| python-pptx | 0.6.23 | 已安装 |
| python-docx | 已安装 | 可用 |
| pypdf | 4.2.0 | 已安装 |
| openai SDK | 2.33.0 | 已安装，用作真实模型适配器基础 |
| pytest | 9.0.3 | 已安装 |
| httpx | 0.27.0 | 已安装，API 测试用 |
| python-multipart / aiofiles / pillow | 已安装 | 上传与图片处理用 |

## 决定

1. **Python 版本**：锁定 3.10（`requires-python = ">=3.10,<3.12"`）。偏离任务书默认（3.11+），理由：本机唯一可用版本为 3.10.12；不安装新解释器，避免占用磁盘与引入环境风险。
2. **依赖管理**：使用 pip + venv（`.venv`），依赖在 `pyproject.toml` 声明；本机依赖已满足，暂不生成 lock 文件（pip 无原生 lock；如需可后续用 `pip-tools`）。当前以"已安装版本实测通过"作为事实基线，`pyproject.toml` 中写实测下界。
3. **模型适配器**：OpenAI 兼容协议（openai SDK），`FakeProvider` 用于测试；无 `NERO_MODEL_API_KEY` 时相关功能显式不可用。
4. **包内模板**：内置模板用 python-pptx 程序化生成（`scripts/build_builtin_templates.py`），产物放 `assets/templates/`，保证可分发、可重建、来源明确。
5. **许可**：fastapi(MIT)、uvicorn(BSD)、pydantic(MIT)、jinja2(BSD)、python-pptx(MIT)、python-docx(MIT)、pypdf(BSD-ish)、openai SDK(Apache-2.0)、pytest(MIT)、LibreOffice(MPL-2.0)、Poppler(GPL-2.0，仅子进程调用)——均为宽松许可或独立进程调用，无传染风险。

## 缺失/待验收项

- 真实模型凭据：未配置，S10 用 FakeProvider 验收，真实调用标记待验收。
- 目标演示软件（PowerPoint/WPS）：不可用，兼容性验收以 LibreOffice 打开渲染为准，人工验收项显式标记。
- 真实客户模板：无，按任务书用独立构造的外部风格测试稿代替。
