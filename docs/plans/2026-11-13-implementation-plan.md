# Nero PPT 详细开发步骤与模型交接手册

日期：2026-11-13。状态：开发任务书；所有步骤尚未实施。

本文件是接手模型的执行入口。当前仓库仅有设计文档与三个场景 JSON 草案，没有 Web 应用、数据库、Worker、模板引擎或已验收模板。本次编写任务书不代表授权立即编码；后续由使用者把此文件交给实现模型执行。

## 0. 阅读顺序与决策优先级

1. 用户后续明确指令。
2. 本文件：首版默认工程选择、任务顺序、验收和边界。
3. [功能设计](2026-11-13-nero-ppt-design.md)。
4. [架构设计](2026-11-13-architecture.md)。
5. [DeepSeek 建议评审](2026-11-13-deepseek-review.md)。
6. [现有场景配置](../../config/scenarios/README.md)。

遇到旧文档不够具体时，按本文件默认方案推进；遇到真实需求冲突时记录决定，不静默改变用户要求。只在 nero-ppt 内修改项目文件，不操作上级仓库的其他项目。遵守实际环境中的 AGENTS.md 和权限要求；禁止默认提交或发布用户未授权的外部操作。

## 1. 产品目标和不做事项

完成一个小型 Web 工具：上传资料 → 选择场景与模板 → 编辑并批准大纲 → 审核样张 → 逐批生成与审核 → 导出 PPTX、PDF、Markdown 讲稿。PPTX 必须带逐页 Notes；页面、PDF 和讲稿来自同一版本。支持刷新继续、局部重做、失败后恢复。

首版：中文优先，文本型 PDF、DOCX、TXT、Markdown、PPTX 资料；PPTX/POTX 模板；工作汇报、客户方案、技术方案和通用场景。内置模板先完成一套并通过端到端验收，第二套在末期补充，模板数量不能替代外部模板验收。

不做：企业工作空间、角色体系、计费、公开自助注册、实时协作、在线自由拖拽编辑器、多个 Agent、向量数据库、Redis/Celery、通用布局 DSL、本地大模型、自动互联网研究、默认 OCR 和任意模板无损承诺。

简单在线访问仍需独立用户凭据和任务归属检查。开发演示可以使用仅绑定 localhost 的显式 demo 模式；不能把此模式当作线上认证。

## 2. 工程默认值

这是为了减少接手模型反复决策而固定的首版实现基线，不表示相关依赖已经安装或验证。

| 项目 | 默认选择 |
| --- | --- |
| 语言 | Python 3.11+，选本机支持版本并锁定 |
| Web | FastAPI + Jinja2 + 原生 JavaScript/CSS |
| 协议 | Pydantic 严格模型，生成 JSON Schema |
| 持久化 | SQLite + 标准库 sqlite3；版本化 SQL 迁移 |
| 后台 | 同代码库的独立串行 Worker，数据库领取步骤 |
| 文档 | python-pptx、python-docx、pypdf；Poppler 检查与缩略图 |
| PDF 转换 | LibreOffice 独立子进程 |
| 模型 | 一个供应商适配器 + 显式测试用假适配器 |
| 测试 | pytest；有浏览器工具时补关键用户链路自动化 |
| 部署 | 单机直接运行；先不引入 Docker 镜像 |
| 文件 | 本地私有 data 目录，SQLite 存元数据 |
| 进度 | 轮询，建议 2 秒并支持失败退避 |

不要凭记忆使用“最新版”：实现时核对官方 API、可用版本与许可证，再锁定依赖。不要安装候选解析器或完整研究项目来“以防万一”。已有文档运行环境可先探测复用，正式应用的依赖仍需声明，不能依赖某位开发者机器上的隐式路径。

环境示例字段：NERO_DATA_DIR、NERO_DB_PATH、NERO_AUTH_MODE、NERO_SESSION_SECRET、NERO_MODEL_PROVIDER、NERO_MODEL_BASE_URL、NERO_MODEL_NAME、NERO_MODEL_API_KEY、NERO_MAX_UPLOAD_MB、NERO_MIN_FREE_DISK_MB、NERO_MAX_REPAIR_ROUNDS。密钥不写进仓库、前端、模型提示或日志。

## 3. 建议目录

下列路径是待创建的目标，不表示当前存在。

```text
nero-ppt/
  pyproject.toml
  <所选工具生成的依赖锁文件>
  .env.example
  .gitignore
  README.md
  src/nero_ppt/
    app.py                     # create_app 和依赖装配
    settings.py
    cli.py                     # doctor / 用户管理 / 离线生成 / worker
    contracts/                 # 通用、来源、模板、页面、QA 协议
    db/connection.py
    db/migrations/001_initial.sql
    repositories/              # 持久化查询，不放业务状态转换
    services/                  # jobs / reviews / artifacts / access
    workflow/states.py
    workflow/runner.py
    workflow/handlers.py
    parsers/                   # base / pdf / docx / text / pptx
    planning/                  # scenarios / facts / outline / slides
    providers/                 # base / 一个真实适配器 / fake
    templates_engine/          # inspect / profile / binding / ooxml
    rendering/                 # builder / notes / libreoffice / preview
    qa/                        # structural / geometry / content / repair
    web/routes/                # pages / api / downloads / auth
    web/templates/             # login / new_job / workbench
    web/static/                # app.js / styles.css
  config/scenarios/
  assets/templates/            # 自有、可分发的内置模板与档案
  tests/unit/
  tests/integration/
  tests/fixtures/
  scripts/                    # 本地启动、环境检查、验收辅助
  docs/plans/
  docs/decisions/
  docs/verification/
  data/                       # gitignore；禁止作为静态目录公开
```

保持模块边界但不机械创建大量空文件。名称为建议，调整后更新本任务书中的完成记录。

## 4. 必须先固定的协议

所有持久化 JSON 顶层带 schema_version；未知字段默认拒绝。用户输入和模型输出都校验，不能只校验模型的第一轮输出。坐标单位统一整数 EMU；单位转换仅发生在程序边界。

### 4.1 资料与内容

- SourceDocument：source_id、文件哈希、格式、解析状态、显示名称。
- SourceBlock：block_id、source_id、locator、类型、原文/表格、抽取方式。locator 采用格式对应结构：PDF 页码、DOCX 段落/表格索引、PPTX slide_id/对象 ID、TXT 行范围；不能制造 DOCX 页码。
- Fact：fact_id、source_block_ids、原始证据、归一化值、单位、时间、verification_status。事实与模型推断分开，推断记录支持事实和说明。
- Brief：主题、受众、目的、场景版本、语言、页数范围、时长、必含项、禁止项、资料缺口。
- Outline：outline_revision、按序页面 ID、每页目的/结论/证据/候选版式、附录标记、预计时长。
- SlideSpec：slide_id、revision、purpose、headline、body_blocks、visual_intent、density、source_refs、must_keep_fact_ids、speaker_notes、target_seconds。body_blocks 使用有标签的联合类型：文本要点、表格、数据序列、图片引用。禁止 x/y/w/h 和可执行代码。
- speaker_notes：script、transition、source_explanations；target_seconds 放在页面一级，避免两个字段冲突。

### 4.2 模板、渲染、质量

- TemplateProfile：template_id/version/hash、页面尺寸、页面原件引用、字体情况、layouts、兼容性报告。
- Layout：layout_id、用途、源页面引用、固定对象 ID、允许编辑槽位、所支持 visual_intent。
- Slot：slot_id、对象 ID、类型、bounds_emu、文字/行数预算、字号下限、裁切策略、样式策略、是否必需。预算是预筛，不能代替渲染。
- RenderPlan：slide_id/revision、template_version、layout_id、slot_bindings、受控编辑动作、资源引用。只由程序生成；模型不调用底层 XML 操作。
- QAReport：slide_id/revision、checks。每个 check 有 check_id、object/slot、status(pass/fail/not_checked)、severity、observed、expected、suggested_action。失败和未检查严格区分。
- ReleaseManifest：release_id、job_revision、template_version、按顺序的页面修订、文件哈希和文件类型。最终文件只从该清单解析。

### 4.3 身份、版本、幂等

所有任务、步骤、产物属于 job；job 属于 owner。请求路径中的 ID 只用于定位，不能作为授权证据。

job_revision 是全局乐观锁；slide_id 是稳定逻辑身份，页码另存；slide_revision 是内容修订；input_revision 标识步骤输入。审核和修改携带 expected_revision。不要把这些字段混成一个计数器。

变更成功时原子增加 job_revision。即使只修改一页，新的快照也保存完整有序页面引用，可以复用未修改页的旧 slide_revision 和文件；旧批准只能通过精确对象版本继续有效，不能用于修改后的页面。

客户端生成 request_id，服务端按 owner/job/action/request_id 去重。相同 ID 配不同载荷返回冲突；不要把合法的两次不同修改误判为重复。

## 5. 数据库与状态约定

建议表：users、sessions、jobs、documents、template_profiles、revisions、steps、artifacts、reviews、change_requests、releases、schema_migrations。

主要唯一约束：revision(job_id, revision)、step(job_id, input_revision, kind, scope_key)、review(job_id, target_kind, target_id, target_revision, reviewer_id, decision)、请求幂等键。批准已批准对象可幂等返回，但不创建第二个后续步骤。

SQLite 启用外键、短事务、busy timeout，评估 WAL；Worker 领取任务使用原子事务，渲染或模型调用期间不能持有数据库写锁。应用测试必须包含两个连接竞争场景。

状态拆成两层，避免用一个字段同时表示阶段和异常：

- phase：intake / outline / sample / batch / final。
- status：ready / running / awaiting_review / needs_input / failed / interrupted / cancelled / completed。

它们是旧文档复合状态名的具体实现：outline + awaiting_review 对应 outline_review。返回 API 时保留这两个字段，前端按映射展示中文。

| 当前状态 | 触发 | 目标 | 条件 |
| --- | --- | --- | --- |
| intake/ready | start | intake/running | 文件与模板已选择 |
| intake/running | 解析与模板检查完成 | outline/running | 无必须解决的输入问题 |
| intake/running | 不支持模板/扫描资料等 | intake/needs_input | 显示问题与补救方式 |
| outline/running | 成功 | outline/awaiting_review | 快照校验通过 |
| outline/awaiting_review | approve | sample/running | expected_revision 匹配 |
| sample/running | 渲染和检查完成 | sample/awaiting_review | 有可查看真实产物 |
| sample/awaiting_review | approve | batch/running | 已批准代表性样张 |
| batch/running | 批次完成 | batch/awaiting_review | 未完成批次不得批准 |
| batch/awaiting_review | approve | batch/running 或 final/running | 取决于是否仍有页面 |
| final/running | 整稿检查成功 | final/awaiting_review | 三类文件与发布清单匹配 |
| final/awaiting_review | approve | final/completed | 发布事务成功 |
| 非终止状态 | cancel | 原 phase/cancelled | 使在途结果失去发布资格 |
| running | 失败 | 原 phase/failed | 记录可操作错误 |
| failed/interrupted | retry | 原 phase/running | 使用最近成功检查点 |

全局模板或大纲变更回到对应阶段；局部变更保留不相关批准页。跨页拆分、改顺序或改时长先给变更计划，获得确认才执行。不得将 pending 的用户批准视为超时自动通过。

## 6. API 契约

统一错误结构 error.code/message/details/request_id；错误信息不能泄露其他用户文件路径。读取无权访问资源统一 404，登录失败 401，校验 422，版本冲突 409，异步接受 202。

| 方法与路径 | 用途 |
| --- | --- |
| POST /auth/login；POST /auth/logout | 独立凭据换取会话 / 注销 |
| GET /api/scenarios；GET /api/templates | 可选场景、内置模板 |
| POST /api/jobs；GET /api/jobs | 创建 / 列出本人任务 |
| GET /api/jobs/{id} | 状态、版本、页面索引、待审核项 |
| POST /api/jobs/{id}/documents | 流式上传资料 |
| POST /api/jobs/{id}/template | 上传用户模板 |
| POST /api/jobs/{id}/start | 提交解析与规划步骤 |
| PUT /api/jobs/{id}/outline | 修改大纲并产生新版本 |
| POST /api/jobs/{id}/reviews | 批准/退回精确版本 |
| POST /api/jobs/{id}/changes/plan | 解释修改范围，返回变更计划 |
| POST /api/jobs/{id}/changes/{change_id}/apply | 批准执行变更计划 |
| POST /api/jobs/{id}/retry；POST /api/jobs/{id}/cancel | 恢复 / 取消 |
| GET /api/jobs/{id}/artifacts/{artifact_id} | 鉴权下载或预览 |
| GET /api/jobs/{id}/events?after={seq} | 可选的轻量事件轮询，无需 SSE |

改变状态的 JSON 请求携带 expected_revision/request_id；上传只能发生于允许的阶段，否则要求明确创建新修订。同步成功返回新 revision，异步请求返回 step_id。预览 URL 只能引用已记录产物，不接受客户端传来的任意本地路径。

## 7. 按顺序实施的步骤

每个步骤完成后写 docs/verification/step-NN.md：变更路径、执行命令、实际结果、产物位置、局限与下一步。禁止用“代码看起来正确”替代必要验证。阶段门槛未通过时先修复，不用更多功能掩盖基础缺陷。

### S00：环境检查和范围锁定

动作：

1. 读取本任务书和现有 JSON，记录当前 git 状态，不覆盖其他项目修改。
2. 检查 Python、LibreOffice、Poppler、中文字体、可用磁盘与已有文档环境。
3. 记录各工具实际版本；依赖缺失时按环境权限安装，网络受限不得反复绕过。
4. 确定包管理工具和锁文件；检查拟采用依赖许可，写简短决定。
5. 写 docs/decisions/0001-mvp-stack.md 与环境检查清单。

验收：列清可复用组件、缺失组件和磁盘预算。约束为新增占用初期尽量控制在 5–8 GB；19 GB 空间是此前观测值，不能当作当前保证。

### S01：工程骨架、配置、协议与测试入口

动作：建立 pyproject、包结构、设置校验、CLI、健康接口、日志 request_id；声明开发/测试依赖并锁定；实现第 4 节协议和 fixture 示例；gitignore 排除 data、密钥、虚拟环境、缓存。模型密钥缺失时相关功能显式不可用，不伪造成功。

目标路径：src/nero_ppt/{app.py,settings.py,cli.py,contracts/}、tests/unit/test_contracts.py、.env.example。

验收：应用可启动；协议拒绝坐标字段、未知版式/资源引用由业务校验拒绝；doctor 显示真实环境能力。通过实际启动和必要协议测试，README 提供准确命令。

### S02：测试素材、内置模板与模板检查器

动作：制作有权使用的自有模板，包含封面、章节、单栏、双栏、图表/表格、总结页。建立中文长标题、混合格式文本、图片、原生表格/图表、多母版和故意不支持对象的样例。制作来源明确的示例资料和手工 SlideSpec。

检查器提取页面尺寸、形状 ID/类型、文本 Run、主题/母版/版式、字体、图片、图表与外部关系。识别每个槽位和固定对象，输出 TemplateProfile、缩略图与支持矩阵。自动识别只给建议；第一版允许手动档案或界面选择对象确认。

目标路径：assets/templates/、tests/fixtures/、templates_engine/inspect.py、profile.py。

验收：每个示例对象可定位；未支持对象列出处理方式；档案能回指具体模板哈希。尚无客户模板时标记外部验收待补充，用独立构造的外部风格测试稿先测，不冒充真实客户测试。

### S03：确定性 PPTX Builder，先不接模型【门槛 A】

动作：从人工 SlideSpec 生成 RenderPlan；根据槽位预算绑定内容；以原始包为基础编辑并按顺序复制/选用页面。复制时处理 slides/layouts/masters/notes/charts/media/relationships/content types；共享不可变资源、克隆会变化的资源，避免一页图表数据修改影响另一页。

实现文字、图片、原生表格、至少一种原生柱状图；保持富文本格式策略，禁止全局 shape.text 粗暴替换。Notes 写入逐页讲稿。POTX 正确规范化包类型。背景和固定装饰不得因输出新内容而重建走样。不支持的复杂对象明确阻止相应编辑。

目标路径：templates_engine/{binding.py,ooxml.py}、rendering/{builder.py,notes.py}、tests/integration/test_template_build.py。

验收：生成可编辑文本/图片/表格/图表；固定对象结构与几何属性保留；关系无悬空；多页同图表样例互不污染；备注可读回。具备目标演示软件时实际打开，无条件时标记兼容性未验收，不能仅凭库打开宣称通过。

失败处理：不要转为整页截图来通过“可编辑”要求；不要以自由重画替代严格模板。

### S04：PDF 转换、缩略图和版本产物

动作：封装 LibreOffice subprocess，使用参数数组，独立临时目录和用户配置，限制超时并终止进程组；生成 PDF 后用 Poppler 输出缩略图。验证退出码、文件存在、非空、页数。临时路径不直接作为正式产物发布，成功后原子移动并记录哈希。

目标路径：rendering/{libreoffice.py,preview.py}、tests/integration/test_render.py。

验收：中文样例真实渲染无缺字；PPTX 与 PDF 页数对应；超时有错误且不留下假成功 PDF；从同一版本生成 Notes、PDF 和缩略图。输出页面拼图并实际查看全部示例页。

### S05：规则 QA 与有界修复【门槛 B】

动作：实现结构、占位符残留、来源存在、必须事实、对象越界、尺寸异常、图片裁切和固定对象差异检查。把模板允许的背景包含关系加入白名单，不把所有相交都判错。文字容量预检只能给风险提示，不能宣称字形度量等于 Office 排版。

生成 QAReport；视觉模型为可选适配器，不可用时明确 not_checked。初始规则修复只允许已验收槽位内调整；最多 2 轮，重复相同问题或无改善时停止。模型精简在 S11 接入，但接口先确定。拆页/变更页序生成 ChangePlan，等待批准。

验收：故意溢出/残留/缺来源样例被捕获；正常底色卡片不误判；缺字体、缺渲染器显示未检查或阻塞；2 轮后停止；检查记录对应实际产物哈希。

### S06：SQLite、私有存储和版本快照

动作：实现第 5 节数据结构、迁移、事务仓储；文件使用随机 ID，显示名称与存储路径分离。每次变更生成不可变快照；SQL 和文件发布使用“临时文件 → 完整产物 → 数据库可见”的顺序，崩溃后允许清理孤立文件，不能出现数据库指向半成品。

验收：空库可迁移，重复启动不重复迁移；两个连接更新同 revision 只有一个成功；job 快照引用正确文件；异常事务回滚；不同 slide_revision 可在同一新快照组合。

### S07：简单鉴权与受限上传

动作：CLI 创建用户和高熵访问凭据，凭据仅展示一次并存不可逆验证值；登录建立服务端可撤销会话，cookie HttpOnly、SameSite，HTTPS 部署启用 Secure。状态修改检查 CSRF/Origin，限制登录尝试；不创建注册或组织体系。

上传采用流式大小限制、类型和文件签名校验、压缩包条目数/解压大小限制、路径检查。宏不执行，外部链接不自动访问。资料和模板对象均归属本人任务；所有列表、下载、状态、修改接口使用同一授权服务。

验收：两位测试用户互相访问任务、预览、模板、修改、下载均失败；伪造 ID 或路径不能逃逸；过大/伪装/恶意 ZIP 被拒绝；无会话不能通过静态目录获得产物。demo 模式不可绑定公开地址而跳过检查。

### S08：资料解析与可追溯素材

动作：实现统一 Parser 接口与 PDF/DOCX/TXT/MD/PPTX 适配器，产出 SourceBlock。PDF 保留页码；DOCX 提取段落与表格；PPTX 提取页内文本、表格和备注；编码问题显式报告。扫描 PDF 无可用文字则 needs_input，不返回空内容继续生成。

按章节/页分块，保留原文，不先压缩丢失全部上下文；建立确定性数字/单位/日期提取及待核验 Fact。复杂解析候选仅在基线失败且样例证明有收益后接入；不要在此阶段安装三个解析框架。

验收：固定样例中的数字、单位、时间和来源定位可追溯；输入丢页或不支持格式被识别；冲突信息同时保留而不自动选一个；总字数超限进入分块路径而非直接截断。

### S09：任务状态机、Worker 与恢复【门槛 C】

动作：实现第 5 节转换表和步骤处理器；Web 事务插入唯一待执行步骤，Worker 原子领取并释放 DB 锁后执行；保持一个活跃 Worker，若有残留 running 步骤，确认旧进程已退出或租约过期后标记 interrupted。不能第二个 Worker 启动就重置正在执行的步骤。

支持取消、重试、过期 input_revision 阻止发布；浏览器连接断开不影响任务；等待审核时不占 Worker；多个用户任务按提交顺序串行，界面明确排队。模型调用和文档转换失败分别保存错误。

验收：重复点击只入队一次；取消后在途文件不能成为当前产物；Worker 被终止后从最近检查点恢复；步骤重试不重复发布；用数据库状态而不是内存字典恢复任务。

### S10：模型适配器与场景/大纲生成

动作：实现 provider 接口、显式 FakeProvider 和一个真实供应商适配器；如用户未给密钥，使用测试 fixture 验证并将真实调用列为待验收，不能假称已完成。支持超时、有限重试、调用用量、结构化输出校验，供应商无 schema 保证时应用端仍验证。不得让模型执行源文档中的指令。

接入现有三个场景配置，新增 general 通用配置；字段缺失先尝试资料提取再交用户确认。模板能力分析早于最终分页；给模型事实表、目标、版式用途和容量摘要，产出 Brief 和 Outline。长资料先分块抽取再汇总，保留原文索引和事实 ID。

验收：场景显式选择优先；通用路径可工作；输出缺字段、非法枚举、伪造来源会被拒绝；同一事实冲突提示用户；无外部检索和编造案例；模型不可输出授权、审核或发布状态。

### S11：逐页内容、讲稿与局部修改

动作：使用批准大纲和候选模板槽位预算生成 SlideSpec。每页传入相关事实和章节摘要，避免传全量无关上下文。备注包含口述正文和转场；模型只写语义规格，程序绑定版式并编译 RenderPlan。

自然语言修改先解释为 ChangePlan：作用域、页面 ID、动作、影响大纲/模板/时间与否。执行前确认跨页或全局影响；本页修改也校验 must_keep_fact_ids。接入 S05 的精简修复，逐轮重新渲染检查，不静默删掉数字和限定条件。

验收：单页修改只产生相应新页面/讲稿修订，无关已批准页哈希保持；改动后旧批准不适用；全局模板切换触发样张重新审核；页序变化更新目录、页码和转场；2 轮后不会循环调用模型。

### S12：业务 API 与审核事务

动作：按第 6 节提供接口，连接上传、状态机、模型步骤、版本和产物。reviews 在同一事务验证归属、expected_revision、可审核状态与 QA 条件，写审核记录并创建后续步骤。机械结构错误不能通过批准变成合法产物；视觉风险若允许人工接受需写明理由。

验收：GET 不触发昂贵生成；异步请求及时返回；两个同时批准请求只有一个推动流程；旧 revision 返回 409 并包含当前 revision；未完成的批次不可批准；错误码稳定可供前端展示。

### S13：新建任务页与审核工作台【门槛 D】

动作：实现登录、新建任务、本人最近任务列表入口和工作台。新建页包含资料、要求、场景与模板；工作台左侧页列表、中间同版本 PDF/图片、右侧内容/备注/来源/修改；显示排队、进行中、等待审核、失败、已取消等真实状态。

大纲可增删、排序、改标题/核心结论；模板映射页面展示候选源页和槽位，允许确认或报告不支持。样张代表性选择包括封面、密集正文和图表/流程；不能重复计入整稿。每批默认 3–5 页，批准后才继续。

轮询停止于终态/页面卸载，失败退避；文本安全转义，PDF/源文件不放公共静态路径；预览显示版本，发生 409 时提示刷新，不覆盖用户输入。

验收：从新建到大纲到样张到批次在浏览器可完成；刷新可继续；能查看/修改 Notes；没有后端尚未支持却展示为可用的按钮；示例数据明确标注，实际预览来自真实 PPTX 转出的 PDF。

### S14：整稿组装与最终交付【门槛 E】

动作：从批准快照创建发布清单，按页面 ID/修订组装完整 PPTX；样张已经属于最终页面时直接复用，不插入两份。更新页码/目录/转场，改变可见内容时重新校验并呈现最终预览；渲染完整 PDF，导出 Markdown 讲稿，校验页数、顺序和备注。

注意：已有单页 PPTX 拼接同样涉及包资源关系，不可使用不安全的 ZIP 文件覆盖；优先从同一模板包及受控 RenderPlan 构建整稿，复用 S03 资源管理逻辑。

最终用户批准后原子发布完整 manifest；任一产物失败都不能 completed。后续修改创建新版本，旧交付物保留历史标记。

验收：PPTX、PDF、讲稿、页面预览在内容与顺序上相符；文件校验和匹配；真实目标软件打开，备注正常；完整浏览每页并记录问题。目标软件不可用时明确人工验收待完成，不能宣称可上线。

### S15：资源控制、启动脚本与运行文档

动作：提供 Web/Worker 启动与停止命令、环境 doctor、备份恢复说明；每任务独立转换目录、超时、磁盘阈值和文件限制。新增容量默认限额写入配置说明，初始建议单文件 30 MB、每任务 100 MB、最多 60 页输出、最大输入页数可配置，这些是工程默认值，可由用户调整。

磁盘低于配置阈值（建议初始 2 GB）暂停新上传和新渲染，说明已有任务状态；只清理无活跃引用的临时文件，失败时也清理。用户原文件和最终产物不默认自动删除。历史产物清理提供 dry-run 和明确范围，不影响当前发布及审核引用。

验收：磁盘不足或转换超时不产生半成品发布；备份/恢复 SQLite 和 data 后可继续查看任务；文档包含真实命令和已知兼容范围；环境无需本地大模型和大镜像。

### S16：端到端验收和最终交接

动作：执行第 8 节矩阵，补第二套内置模板；记录实际支持矩阵、测试命令和局限。用真实模型及至少一份外部模板完成全流程；缺少凭据或目标软件时记录具体未完成项，不打全通过标记。

交付：可运行代码与锁文件、示例配置、模板与许可说明、迁移、测试、操作文档、最终 verification/report.md。README 区分已支持/不支持/待验证。不得把 FakeProvider 的输出截图当作真实模型能力证据。

## 8. 最小验收矩阵

| ID | 场景 | 预期 | 推荐验证层 |
| --- | --- | --- | --- |
| T01 | 长中文标题与混合字体 | 保留样式，溢出有处理记录 | 渲染 + 人工 |
| T02 | 同模板多页引用同一图表 | 改一页数据不污染其他页 | 文件解析 + 渲染 |
| T03 | 普通文本框而非占位符 | 能明确适配或报告不支持 | 模板集成 |
| T04 | 跨母版、POTX | 包结构正确、目标软件可打开 | 集成 + 人工 |
| T05 | 缺字库/字体 | 显示缺失和兼容风险，不假通过 | 集成 |
| T06 | 扫描 PDF | 提示需要可解析资料，不生成空内容 | 解析集成 |
| T07 | 数字口径冲突与单位限定 | 保留来源和冲突，不擅自合并 | 内容测试 |
| T08 | 旧审核/同时批准 | 409 或幂等，不重复推进 | 双连接集成 |
| T09 | Worker 中断、取消 | 可恢复；旧结果不发布 | 进程集成 |
| T10 | 两个不同用户 | 列表、状态、下载、修改均隔离 | API 集成 |
| T11 | 超大/伪装/恶意 ZIP | 有限资源内拒绝 | 上传集成 |
| T12 | 单页修改 | 无关批准页不变，Notes 同步 | 全流程 |
| T13 | 两轮修复仍失败 | 停止并等待用户，无无限重试 | 单元 + 集成 |
| T14 | 最终 PDF/讲稿/PPTX | 顺序、版本、事实和备注一致 | 全流程 + 人工 |
| T15 | 渲染失败或磁盘不足 | 不产生完成状态和半发布 | 故障注入 |
| T16 | 文档中的恶意指令 | 不改变权限、不执行代码或外链 | 模型适配边界 |
| T17 | 任意缺失必需检查 | 标记 not_checked 并阻止误宣称通过 | QA 集成 |

外部候选库对比是可选优化，不是先把所有项目安装一遍的要求。每阶段只运行与变更相关的检查；最终再执行完整矩阵。

## 9. 执行顺序与完成状态

主依赖顺序：S00 → S01 → S02 → S03 → S04 → S05 → S06 → S07 → S08 → S09 → S10 → S11 → S12 → S13 → S14 → S15 → S16。

默认顺序执行。完成状态：S00–S16 已于 2026-11-16 执行完毕，验收记录见 docs/verification/；真实模型调用与目标软件人工验收为外部依赖待办（见 report.md）。即便技术上部分工作可并行，没有用户或适用指令授权时不得自行启动多个模型代理。不要让多个执行者同时修改同一协议和迁移。

- [x] S00 环境与决策
- [x] S01 骨架与协议
- [x] S02 模板与素材
- [x] S03 确定性 Builder（门槛 A）
- [x] S04 PDF 与预览
- [x] S05 QA（门槛 B）
- [x] S06 持久化
- [x] S07 访问与上传
- [x] S08 解析与来源
- [x] S09 Worker 与恢复（门槛 C）
- [x] S10 场景与大纲
- [x] S11 页面与修改
- [x] S12 API 与审核
- [x] S13 工作台（门槛 D）
- [x] S14 发布交付（门槛 E）
- [x] S15 运行与资源
- [x] S16 全链路验收

只有验收记录存在且达到标准才勾选。遇到外部阻塞保留未勾选，记录替代验证及剩余风险；能推进的独立步骤仍可推进，但不能宣称门槛通过。

## 10. 每阶段交接记录模板

```text
阶段：Sxx
状态：未开始 / 进行中 / 验收通过 / 外部阻塞
本阶段目标：
实际修改文件：
运行命令及退出结果：
真实产物位置与版本：
检查结果（含失败和 not_checked）：
偏离默认设计的地方及理由：
未完成项与外部依赖：
下一阶段与起始命令：
```

## 11. 可直接交给实现模型的提示词

> 请在 nero-ppt 目录实现轻量 PPT 生成工具。先阅读 README.md 和 docs/plans/2026-11-13-implementation-plan.md，再按关联设计文档执行。当前只有文档和场景配置，不要假设已有应用。按 S00–S16 顺序推进，每阶段记录真实验证结果；优先完成模板驱动的确定性 PPTX/Notes/PDF 链路，再接模型和界面。不要引入多 Agent、企业平台、通用布局引擎或大型本地模型。模型只能生成受约束内容规格，不能直接生成坐标或服务器执行代码。保护已有文件和其他项目，不伪造兼容性、模型调用或测试结果。缺少模型凭据、真实客户模板或目标演示软件时，明确标记阻塞与未验收项，继续可独立完成的工作。最终交付代码、依赖锁文件、运行说明、验收记录和已知限制；不要仅返回计划或空骨架。
