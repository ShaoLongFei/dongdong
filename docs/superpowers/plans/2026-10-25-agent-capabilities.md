# Skills、外部 MCP 与操作审批实施计划

> 使用 `writing-plans` 编排，使用 `subagent-driven-development` 并行执行；用户已明确选择 subagent 并授权继续推进。由主 agent 统一提交、推送和集成，不让 worker 竞争共享文件。

**Goal:** 内置 agent 可按需使用本地技能、调用外部 MCP 工具，并在配置要求时等待用户批准有副作用的操作。

**Architecture:** 保留一个 Runtime、一个活动任务与 SQLite 事件流。独立能力模块提供函数/具体资源对象，主 agent 集中接入 Runtime、FastAPI 和侧栏；外部 MCP 使用官方 Python SDK，连接生命周期限定在一次 run。默认保留当前自动执行体验，用户可在设置切换为逐次确认；审批发生时显示全局待办，不依赖用户恰好选中任务会话。

**Tech Stack:** Python / FastAPI / 官方 MCP SDK / React / 现有 Radix 和 lucide。

**Spec:** `docs/superpowers/specs/2026-10-24-general-agent-design.md` 第 2、4、5、8、10 节；本计划限定其第三阶段的 Skills、MCP、审批，不包含文件浏览/产物预览。

## Global Constraints

- 单文件 ≤300 行；不引入插件系统、DI、事件总线、第二套 agent loop。
- 用户数据仍在 `~/.dongdong/`，不读写用户现有配置来验证功能，不启动实际外部 MCP/麦克风，不控制浏览器。
- 不采用 TDD，不为配置或 UI 胶水新增测试；协议/路径/执行/审批边界断言仅放 `tests/smoke.py`，必要时简化其冗长用例但不能删覆盖；总运行 <10 秒。
- 三个 worker 共享隔离工作区，严格遵守文件归属，不修改他人文件，不自行提交/推送；主 agent 每取得可验证进展后 check、commit、pull --rebase、push。
- 新依赖必须列入 pyproject；只安装预编译包、不 sudo、不编译 C；超过20MB先说明并征得同意。

## Review Focus

1. 技能名称/符号链接越界不得读取任意本机文件；禁用技能不能再被模型调用。
2. MCP 同名工具不冲突；服务失败/超时/取消不能卡住所有后续任务或泄露子进程。
3. MCP URL/headers/env 等配置不在列表里泄露明文凭据；仅在用户配置后连接，不加载不可信示例服务。
4. 拒绝或停止待审批操作后不得执行；批准仅适用于准确的 run+tool_call 一次；新 run 不复用批准。
5. 刷新/换会话后仍可看到待审批；CLI 无交互审批能力时拒绝而不是无期限等待。

## 并行与集成顺序

| 任务 | 所有权 | 交付接口 | 依赖 |
| --- | --- | --- | --- |
| 1 Skills | skills.py、Skills.jsx | list_skills/read_skill、router(cfg) | 无 |
| 2 MCP | mcp_client.py、mcp_routes.py、MCP.jsx | MCPTools、router(cfg) | 官方 SDK |
| 3 审批 | approvals.py、Approvals.jsx | Approvals、router(manager) | 无 |
| 4 集成 | Runtime、WebState、CLI、配置、pyproject、侧栏、CSS、smoke、文档 | 完整垂直切片 | 1/2/3 |

三个 worker 可同时实现模块和独立组件；主 agent 串行修改共同调用方和唯一 smoke 文件。worker 完成后逐项审查，再做整体集成审查。

### Task 1: 本地 Skills 发现、读取与管理

**Files:** Create `apps/device-mac/dongdong/skills.py`, `apps/web/src/Skills.jsx`。仅这两处。

**Interfaces:**
- `list_skills(cfg: dict) -> list[dict]`：扫描 `config.HOME / "skills" / <name> / SKILL.md`，返回 name/description/enabled；目录名是稳定 ID，不做安装、编辑或下载。
- `read_skill(name: str, cfg: dict) -> str`：只允许 `[A-Za-z0-9_-]+` 目录，resolve 后确认仍在 skills 根内；拒绝路径穿越、根外符号链接、禁用项；正文上限 32KB，超过明确报错，不静默执行。
- `router(cfg: dict) -> APIRouter`（prefix `/api/skills`）：GET 空路径返回 `{skills:[...]}`；GET `/{name}` 返回 `{name,content}`；PUT `/{name}` 接受严格 bool `{enabled}`，写 `cfg['disabled_skills']` 并 config.save；任务运行中拒绝变更409。错误400/404需人可读。列表不因一项损坏全失败。
- React 默认导出 `Skills({report})`，使用既有 `api`，展示目录提示、列表、启停、正文详情、刷新。`description` 取 SKILL.md 的 frontmatter description（常规简单字符串）或第一段正文；可用成熟 PyYAML，主 agent 负责依赖，不自己造 YAML 解析器。

- [x] 阅读调用约定，用最少代码实现发现和路径校验；不引入持久索引。
- [x] 实现 routes 与页面；中文文案，明确仅本地目录，启停下一任务生效。
- [x] 临时目录命令行验证普通技能、禁用、越界/根外 symlink、超长文件；临时验证脚本放 `~/tmp/dongdong-capabilities/`。
- [x] ruff 与 JSX 构建级验证；报告产出和限制。不要修改 Runtime/主页面/CSS/配置/测试文件。

### Task 2: 官方 SDK 外部 MCP 客户端与管理页

**Files:** Create `apps/device-mac/dongdong/mcp_client.py`, `apps/device-mac/dongdong/mcp_routes.py`, `apps/web/src/MCP.jsx`。仅这三处。

**Interfaces:**
- 配置 `cfg['mcp_servers']` 是 `{name: {transport:'stdio'|'streamable_http'|'sse', enabled:bool, command?:str, args?:list[str], env?:dict[str,str], url?:str, headers?:dict[str,str]}}`。本地单用户由用户显式配置；名字只允许 `[A-Za-z0-9_-]+`，≤32字符。
- `MCPTools(servers: dict)` async context manager；连接/initialize/list_tools 全部由官方 mcp SDK；不手写 JSON-RPC/SSE/WebSocket。只连接 enabled 服务。`.specs` 是 OpenAI function schemas 列表；`.errors` 是不含敏感配置的服务错误字符串列表；`await .call(model_tool_name: str,args:dict)->str` 返回工具文本或 `执行失败: ...`；不存在名称返回 `未知工具`。模型工具名 `mcp_<server>_<index>`（索引按 list_tools 顺序，映射真实 name，不受真实名称的特殊字符/长度影响）。description 含服务和真实工具名。
- 每个 run 在同一 asyncio task 中进入/退出 AsyncExitStack，正确关闭 stdio/HTTP/SSE。连接/发现与调用都有有限超时（默认15s / 60s）；取消应传播。单服务失败不妨碍内置工具。`isError` 必须变成失败前缀。
- `router(cfg:dict)->APIRouter` prefix `/api/mcp`：GET 返回 `{servers:[{name,transport,enabled,command,args,url,env_keys,header_keys}]}`，不返回 env/header 值；PUT `/{name}` 完整保存配置，未提交 env/headers 保留已有值，`{}` 可清空；DELETE 删除；POST `/{name}/test` 明确连接一次、发现工具后关闭，返回 `{tools:[{name,description}],error?:str}`。变更/测试时用 work_gate 防止与运行/设置并发。禁止 name 冲突覆盖内置工具。配置错误400，运行冲突409。
- React 默认导出 `MCP({report})`：列表、增加/编辑、测试工具、删除，支持三种 transport；敏感字段使用 JSON 文本，读取列表只显示 key 提示，留空保留，不显示既有密钥；用户手动提交才连接。

- [x] 查已安装官方 SDK 真实 API，验证 stdio、streamable_http、sse 的 async context manager 用法，不猜。
- [x] 实现三个文件并只使用 SDK 传输。
- [x] 临时验证可用官方 SDK 的 FastMCP 本地服务（真实协议，不 mock），验证 tool 列表、执行、isError、取消清理；纯本地临时进程，不连用户服务器。若依赖尚未安装，继续写模块，告知主 agent。
- [x] 报告 SDK 生命周期/超时注意事项，不改 Runtime/pyproject/配置/测试文件。

### Task 3: 操作审批管理与全局组件

**Files:** Create `apps/device-mac/dongdong/approvals.py`, `apps/web/src/Approvals.jsx`。仅这两处。

**Interfaces:**
- `Approvals()` 持有当前进程 pending futures；`await request(run_id:str,call_id:str,name:str,args:dict,on_event:Callable)->bool` 产生 `approval` 事件 `{id:call_id,name,arguments:args,status:'pending'}` 后等待用户决定；结束产生 `approval_result` `{id,status:'approved'|'rejected'|'cancelled'}`。同一 call ID 必须与 run 组合匹配。取消时移除 pending 并传播 CancelledError，不执行后续操作。
- `.pending() -> list[dict]` 返回 `{run_id,id,name,arguments}`；`.resolve(run_id:str,call_id:str,approved:bool)->bool` 只处理尚在等待的这一次调用，重复或未知返回 False。不要持久化授权，不实现 always allow。
- `router(manager:Approvals)->APIRouter` prefix `/api/approvals`：GET `{approvals:[...]}`，POST `/{run_id}/{call_id}` 严格 bool `{approved}`，不存在/已处理409。
- React 默认导出 `Approvals({report,onResolved})`，自主轮询 pending（1s），位于主页面全局区域而非所选会话内；显示工具、参数、允许一次/拒绝按钮，处理请求期间禁用；错误调用 report，成功回调 onResolved；切会话不消失，支持停止任务移除请求。用普通卡片，不新增弹窗依赖。
- 主 agent 将新增 cfg `tool_policy:'auto'|'ask'` 默认 auto；ask 在 shell/write_file/edit_file/全部外部 MCP 操作前调用 request；只读工具和 read_skill/update_plan 不审批。无回调的 CLI ask 返回明确拒绝结果，不挂起。实际工具调用由 Runtime 继续执行，审批模块不执行命令。

- [x] 实现一处等待/一次批准与取消清理，无状态机/数据表。
- [x] 实现 routes、全局组件，使用现有 api。
- [x] 临时 async 验证拒绝、一次批准、重复/错误 ID、取消不留 pending；不改 Runtime/主页面/CSS/测试文件。
- [x] ruff 验证与报告。

### Task 4: 集成、边界验证、文档与交付

**Files:** 主 agent 独占 Runtime、web_runtime、web_server、chat_cli、agent_service、config、pyproject、main.jsx、Settings.jsx、Conversation.jsx、style.css、tests/smoke.py 及公共文档。

- [x] 安装官方 MCP SDK 和必要小依赖，记录在 pyproject；保持预编译安装且总下载<20MB。
- [x] Runtime 加 cfg/approve 可选边界参数；一轮 run 的资源 context 用 MCPTools；系统提示只列启用技能及 description，提供 read_skill 工具按需加载；读取失败可继续模型回合，不影响内置工具。
- [x] 每次 run 只创建自己的一组外部连接，错误写可见 `mcp_error` 事件；外部工具、read_skill 的调用与结果沿用原 SQLite/SSE。增加审批前 await，拒绝形成工具结果，用户停止取消资源和 pending；配置在下一 run 生效。必要时把现有进度 schema/helper 移入小文件以保证 Runtime≤300行，不写通用工具框架。
- [x] WebState 持有 Approvals；on_event 转存审批事件；FastAPI include routers 在静态 mount 前。CLI/独立 service 传 cfg，无 UI 的 ask 直接拒绝。安全设置公开并校验 tool_policy。主页面加入技能/MCP tabs、全局 Approvals 与 CSS；Conversation 显示审批等待/拒绝状态。
- [x] 将 tests/smoke.py 冗长的协议测试简化以保留全部覆盖并容纳新增核心断言：技能路径/禁用、MCP协议转换、批准/拒绝/取消/一次性边界。全套仍≤300行且<10秒；真实 SDK subprocess/HTTP传输集成脚本放 ~/tmp，避免协议 mock 和新增测试文件。
- [x] 命令行隔离验证完整 Runtime→技能→MCP→待批准→拒绝/允许→最终交付，HTTP管理/API严格布尔/同源保护/秘密隐藏，SSE回放，重复任务取消后可继续。前端构建与必要 SSR 校验，不控制用户浏览器。
- [x] 完成三项 scoped review、整体 review；修复实质问题，./scripts/check.sh、npm run build、git diff --check。更新本计划勾选与 README/architecture/decisions，check 后提交并立即推送 codex/agent-capabilities；不合并主分支，不重启用户服务。

## 执行证据（2026-10-25）

- Task 1 / 2 / 3 由独立 subagent 同时实现，主 agent 完成公共接入；隔离分支 `codex/agent-capabilities`，基线 `2cefc5e`。
- 官方 SDK 1.30.0 真实本地 FastMCP：stdio、Streamable HTTP、SSE，发现/调用/关闭、isError、连接与调用超时、取消、多服务同时连接验证通过；未启动用户的服务或麦克风。
- `~/tmp/dongdong-capabilities/integration.py`：Runtime 技能读取→真实 MCP→批准→写文件→最终结果，REST 机密隐藏/参数校验/互斥、SSE 历史、拒绝/取消/无 UI 不执行通过。
- `~/tmp/dongdong-capabilities/render.mjs`：审批等待状态、能力页面 SSR 通过；`npm run build` 通过。未操作浏览器，实际页面视觉和用户自有服务仍需体验。
- `./scripts/check.sh` 通过；唯一新增边界断言位于 smoke.py（≤300行）。
- 独立审查修复：循环符号链接导致技能列表失败；非法 IPv6 URL 导致 MCP 配置500。审批与整体集成未发现其它实质问题。
- 交付边界：Skills 仅本地发现/启停，MCP 不代用户安装服务，审批默认 auto、ask 审批一次工具调用；文件预览、投屏、主动语音回报仍为后续。
