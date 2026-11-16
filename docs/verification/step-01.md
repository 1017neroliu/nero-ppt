# S01 工程骨架、配置、协议与测试入口

阶段：S01
状态：验收通过
本阶段目标：pyproject、包结构、设置校验、CLI、健康接口、日志 request_id、协议实现与测试。

## 实际修改文件

- pyproject.toml / .gitignore / .env.example：工程声明与排除规则
- src/nero_ppt/settings.py：环境变量加载与校验（auth_mode 白名单、限额为正）
- src/nero_ppt/contracts/{common,sources,outline,slides,template,render,qa,release}.py：完整协议层
- src/nero_ppt/app.py：create_app + RequestContextMiddleware（request_id 注入与访问日志）
- src/nero_ppt/cli.py：doctor / serve 子命令
- src/nero_ppt/web/routes/{__init__,pages,api}.py + templates/index.html：路由骨架
- tests/unit/test_contracts.py：11 个协议测试

## 运行命令及退出结果

- `pip3 install -e . --no-deps`：安装成功（依赖本机已满足）
- `python3 -m pytest tests/unit/test_contracts.py -q`：11 passed
- `python3 -m nero_ppt.cli doctor`：输出真实环境能力 JSON；模型未配置密钥 → ok=false 且 note 说明"模型功能显式不可用"；exit=1（有不可用组件时正确非零）
- TestClient 实测：GET /healthz=200、GET /=200（页面渲染）、GET /api/scenarios=200、响应含 X-Request-ID

## 协议验收点

- extra="forbid"：未知字段拒绝（test_unknown_field_rejected）
- 坐标字段 x/y/w/h/width/height 均被拒绝（test_coordinate_fields_rejected）
- body_blocks 有标签联合：bullets/table/chart/image/process 可解析，free_canvas 被拒
- bounds_emu 仅整数 EMU，浮点被拒
- QAReport 严格区分 pass/fail/not_checked，非法状态被拒

## 偏离默认设计

- Python 锁定 3.10（本机唯一版本），见 docs/decisions/0001-mvp-stack.md
- pydantic-settings 未引入（本机无此包），改为手写 os.environ 读取，行为等价且少一个依赖

## 下一步

S02 测试素材、内置模板与模板检查器。
