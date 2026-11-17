# Nero PPT

轻量 Web PPT 生成工具：上传资料与要求、选择场景和模板、分阶段审核，生成可编辑 PPTX、PDF 预览与逐页讲稿备注。

## 当前状态

已实现模板驱动的 PPTX/Notes/PDF 生成链路、任务状态机与 Worker 恢复、鉴权与受限上传、审核事务和整稿发布，并配有单元与集成测试。最近更新包括四步工作台、阶段切换与重跑、样张/整稿版本历史、任务内模型配置、多文件上传，以及标题和表格溢出自适应。设计文档见 [docs/plans/](docs/plans/)，历史验收记录见 [docs/verification/](docs/verification/)。

## 快速开始

```bash
# 0) 安装（Python 3.10 或 3.11；系统需安装 LibreOffice、Poppler、中文字体）
python3 -m pip install -e ".[dev]"
python3 -m nero_ppt.cli doctor

# 1) 生成内置模板（两套）
python3 scripts/build_builtin_templates.py assets/templates/builtin-classic.pptx classic
python3 scripts/build_builtin_templates.py assets/templates/builtin-minimal.pptx minimal

# 2) 生成示例资料（可选）
python3 scripts/build_fixtures.py

# 3) 启动（Web + Worker 两个进程）
scripts/start.sh          # Web: http://127.0.0.1:8630；停止 scripts/stop.sh
# 或手动：
python3 -m nero_ppt.cli serve --host 127.0.0.1 --port 8630
python3 -m nero_ppt.cli worker

# 4) 创建用户（password 模式；demo 模式自动绑定 demo 用户）
python3 -m nero_ppt.cli user-create <用户名>   # 凭据仅显示一次

# 5) 运行测试
python3 -m pytest tests/ -q
```

demo 模式只允许绑定 127.0.0.1，不作为线上认证；线上部署设 `NERO_AUTH_MODE=password` 并在反代启用 HTTPS。

## 环境变量

见 [.env.example](.env.example)：`NERO_DATA_DIR`、`NERO_DB_PATH`、`NERO_AUTH_MODE`（demo/password）、`NERO_SESSION_SECRET`、`NERO_MODEL_*`（OpenAI 兼容协议，未配置密钥时模型功能显式不可用）、`NERO_MAX_UPLOAD_MB`（默认 30）、`NERO_MAX_JOB_INPUT_MB`（100）、`NERO_MAX_OUTPUT_SLIDES`（60）、`NERO_MIN_FREE_DISK_MB`（2048）、`NERO_MAX_REPAIR_ROUNDS`（2）。

## 模型选择与可用性测试（按任务）

系统出厂**不预置任何模型**。在新建任务页点「＋ 配置新模型」，填写显示名称、API 地址、模型名与 API Key，支持本地 Ollama 和远程 OpenAI 兼容接口；保存后即可在下拉列表选择，也可从列表删除。配置保存在服务端 `data/config/models.json`（密钥不下发浏览器），改动热生效无需重启。管理员也可手工预置该文件（支持 `api_key_env` 引用环境变量）；未建目录时回退到 `NERO_MODEL_*` 环境变量提供的默认档。

**已有任务切换模型**：在工作台顶栏打开「⚙ 配置」，选择可用的目录模型并保存；任务未绑定模型时会高亮提示。可勾选「保存后重试失败步骤」，修正模型配置后继续处理失败任务。

**推理模型（GLM/DeepSeek-R1 类思考模型）**：思考过程计入 `max_tokens`——若任务报"模型未返回任何正文内容"，在「＋ 配置新模型」的"最大输出 tokens"填入更大值（建议 8000–16000，系统在正文为空时也会自动以 4 倍预算重试一次）；也可在 `models.json` 为该条目手工加 `"extra_body": {"thinking": {"type": "disabled"}}` 直接关闭思考（GLM 系支持）。

**可用性测试**：新建任务页每个模型配"测试可用性"按钮——发起一次最小真实调用（约 10 tokens），验证网络可达、密钥有效、模型名存在、JSON 协议兼容四项，并按错误分类提示（认证失败/无法连接/超时/限流/模型不存在）。目录配置了模型时，**必须测试通过才能创建任务**；启动接口同时做结构校验（密钥缺失 409），Worker 执行时仍有显式失败兜底。

```json
{
  "default": "openai-gpt4o-mini",
  "models": [
    {"id": "openai-gpt4o-mini", "name": "GPT-4o mini（OpenAI）",
     "base_url": "https://api.openai.com/v1", "model_name": "gpt-4o-mini",
     "api_key_env": "NERO_MODEL_API_KEY"}
  ]
}
```

## 使用流程

1. 解析资料：新建任务，上传文本型 PDF/DOCX/TXT/MD/PPTX，填写需求并选择场景、模板与模型（内置两套模板或上传 PPTX/POTX）。支持拖拽、多选上传及逐文件进度反馈；第 1 步可补充资料后重跑解析。
2. 生成大纲：模型基于资料事实生成大纲，可编辑标题、调整顺序、增删页面，审核通过后继续。
3. 样张审核：一次生成全稿规格并渲染预览，查看各页内容、讲稿与来源；单页修改先通过 ChangePlan 解释范围，再确认执行。批准后直接进入整稿组装。
4. 整稿交付：生成同版本的可编辑 PPTX（含逐页 Notes）、PDF 与 Markdown 讲稿，最终批准后发布。

### 工作台导航与历史版本

- 点击顶部步骤或「上一步 / 下一步」可查看已到达阶段的产物，浏览不会改变任务状态；运行中也可回看历史步骤。
- 需要编辑或重新执行时，使用步骤内的显式切换/重跑按钮；切换工作阶段要求任务未在运行，且目标阶段已有相应产物。
- 样张支持「重新渲染样张」（沿用现有规格，不调用模型，应用当前排版逻辑）和「重新生成规格与样张」（按最新大纲重做内容）；整稿可重新组装。
- 样张与整稿各有 V1、V2… 版本历史，点击版本可切换预览，并按版本下载 PPTX、PDF 与讲稿。
- 右栏「页面内容 / 大纲」标签可切换逐页详情和全篇结构，大纲以卡片完整展示。

当前界面采用以上四步流程，正文批次阶段仅在状态机中保留兼容，不再作为独立审核步骤显示。

## 能力边界

**已支持**（有自动化验收）：内置两套模板 9 种版式（封面/章节/要点/双栏/表格/图表/流程/总结/图文）；文本/原生表格/柱状图/图片受控编辑且保留模板格式；多页同图表数据隔离；标题按单行宽度自动缩字、表格按可用高度压缩行高与字号，版式回退按槽位物理面积排序；LibreOffice PDF 渲染与缩略图；中文事实提取与来源追溯；扫描 PDF 显式 needs_input；两用户隔离鉴权；版本冲突 409；Worker 中断恢复与取消；QA 规则与 2 轮有界修复；发布清单哈希校验。

**不支持**：SmartArt/OLE 编辑（原样保留并在档案中报告）；任意模板像素级一致承诺；扫描件 OCR；Excel 解析；自动互联网检索；多 Agent；在线拖拽编辑器；实时协作。

**待验证**（记录于 [docs/verification/report.md](docs/verification/report.md)）：真实模型调用（需凭据）；PowerPoint/WPS 打开兼容性（本机仅有 LibreOffice）；真实客户模板验收。

## 运维

- `python3 -m nero_ppt.cli doctor`：环境能力体检
- `python3 -m nero_ppt.cli cleanup [--dry-run]`：清理临时文件（不影响当前发布与审核引用）
- `python3 scripts/refit_job_profile.py <任务ID>`：渲染逻辑更新后，为已完成任务重建模板档案并入队整稿组装；脚本固定使用项目内 `data/nero.db` 与 `data/`，仅适用于该默认存储位置，执行前备份，随后由 Worker 处理
- 备份：停止进程后复制 `data/`（SQLite + 文件同目录）；恢复后直接启动
- 日志：`logs/web.log`、`logs/worker.log`（访问日志含 request_id）

## 目录

```
src/nero_ppt/        # 应用代码（contracts/db/parsers/templates_engine/rendering/qa/services/web/workflow/providers/planning）
config/scenarios/    # 四个场景配置（工作汇报/客户方案/技术方案/通用）
assets/templates/    # 内置模板（脚本生成，可重建）
tests/               # 单元 + 集成测试
docs/verification/   # 各阶段验收记录（step-00 ~ step-16）
data/                # 私有数据（gitignore，绝不公开静态访问）
```
