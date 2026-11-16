# S07 简单鉴权与受限上传

阶段：S07
状态：验收通过
本阶段目标：独立凭据、可撤销会话、Origin 校验、登录限流、受限上传。

## 实际修改文件

- src/nero_ppt/services/access.py：scrypt 口令哈希（不可逆）、服务端会话（可撤销/过期）、登录限流（5 次失败按用户+IP 锁 300s）、高熵凭据生成
- src/nero_ppt/services/uploads.py：类型白名单、magic bytes 签名（PDF/OOXML/UTF-8 文本）、ZIP 条目数/解压总量/单文件/路径逃逸检查
- src/nero_ppt/services/context.py：WebContext 装配；demo 模式自动创建 demo 用户
- src/nero_ppt/web/deps.py：get_current_user（demo 绑定 / 会话校验）、require_same_origin
- src/nero_ppt/web/routes/auth.py：/auth/login /auth/logout /api/me；cookie HttpOnly+SameSite=Lax（HTTPS 时 Secure）
- src/nero_ppt/cli.py：user-create 子命令（凭据仅显示一次）
- src/nero_ppt/db/connection.py：LockedConnection（跨线程安全，配合 WAL/busy_timeout）
- tests/integration/{test_access_upload,test_web_auth}.py：17 个测试

## 检查结果（17 passed）

- 口令不可逆且可验证；重复用户名拒绝；凭据 ≥16 字符高熵 ✓
- 登录失败不区分"用户不存在/密码错"，统一 401 ✓
- 5 次失败后同 IP 限流（即使密码正确），其他 IP 不受影响 ✓
- logout 后会话立即失效（服务端撤销）✓
- cookie 含 HttpOnly 与 SameSite=lax ✓
- 跨 Origin POST 403 ✓
- T10 基础：demo/password 模式行为正确；password 未登录 401 ✓
- data 目录非静态公开：/data/**、/static/../ 路径穿越均 404/405 ✓
- 上传：签名伪装（MZ→.pdf、文本→.docx）拒绝；非 UTF-8 文本拒绝；超限/空文件拒绝 ✓
- 恶意 ZIP：路径逃逸、绝对路径、2100 条目均拒绝；正常 OOXML 通过 ✓

## 修复记录

- web 包相对导入层级错误（deps/routes 两三层混用）——已修正
- FastAPI 线程池访问 sqlite 连接报跨线程错误 → LockedConnection（RLock + check_same_thread=False）

## 局限

- HTTPS Secure 标志由部署层（反代）决定，本机 HTTP 测试不启用
- demo 模式仅限回环绑定（cli.serve 已强制），不作为线上认证

## 下一步

S08 资料解析与可追溯素材。
