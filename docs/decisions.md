# 决策记录

- 2026-10-26：macOS 独立包采用 Swift AppKit/WKWebView + App 内 arm64 CPython，而不是 Tauri；当前项目已经是 FastAPI 静态 Web，原生壳无需引入 Rust。App 选择空闲 loopback 端口并把运行时数据放到 `~/.dongdong`；当前只交付 Apple Silicon，Intel/通用包需单独构建原生依赖。

- 2026-10-25：项目与会议目录使用 macOS `osascript` 的固定 `choose folder` 系统面板，浏览器目录上传接口不能提供宿主机绝对路径；不上传文件、不引入新依赖。选择器限单窗口并在超时/取消请求时结束子进程，后端仍校验目录与会议工作区边界。

只记「为什么这么选」，不记过程。新决策往下加。

- 2026-10-25：故事导出器只解析 `tools/ffmpeg/<platform>/ffmpeg`，不回退到系统 PATH；随项目分发已核验的 macOS arm64 FFmpeg 6.0 构建（来源、SHA-256、许可证说明见 `tools/ffmpeg/`）。该构建启用了 GPL/version3/nonfree 组件，后续公开再分发前必须复核对应许可义务。PNG/PDF 优先使用 CairoSVG，缺失时尝试 Playwright，再回退到 macOS Quick Look，避免新增大型运行时依赖。

| # | 决策 | 理由 | 代价 |
| --- | --- | --- | --- |
| 1 | Mac 端用 Python，不用 C++ | 和 CLI agent 交互就是子进程；音频/Opus/mDNS 都有现成库 | 不能直接复用到 ESP32，但 ESP32 本来也不需要这些 |
| 2 | 工具实现只有一份（`tools.py`），MCP 和本地 HTTP 共用 | ESP32 阶段只加转发，Mac 侧零改动 | 多一个 HTTP 服务（30 行） |
| 3 | agent 用 CLI 子进程，不自己实现 agent loop | codex / claude 自带工具循环，重写毫无意义 | 受 CLI 能力限制（如不能细粒度控制） |
| 4 | 投屏用 AppleScript 点控制中心 | 系统没有公开 API；私有框架（AirPlaySender）不稳定 | 依赖辅助功能权限，macOS 升级可能失效 → 提供 `mirror_command` 覆盖 |
| 5 | 不自己实现唤醒词 | 平台 `realtime` 模式自带 VAD/打断，够用 | 环境噪音会触发；后续可加本地 KWS |
| 6 | 单文件测试 `tests/smoke.py`（已被 13 取代） | 只保护协议/编解码/路径边界，避免测试拖慢开发 | 没有覆盖 UI 交互，靠手动验 |
| 7 | DeepSeek 走 Claude Code 的兼容端点 | 不用自己写 agent loop，也不用装新 CLI | 需要 `DEEPSEEK_API_KEY`，功能受端点限制 |
| 8 | 面板跟 `run` 同进程（守护线程 HTTP），默认只听 `127.0.0.1` | 直接读取进程内状态，不加 IPC；本机使用默认不暴露到局域网 | 需要远程访问时自行配置监听地址和 token |
| 9 | Mac 端用 Mem0 SDK；Qdrant 和 SQLite 存在仓库 `.memory/`，OpenAI 负责提取与嵌入 | 沿用进程内 SDK 和本地持久化，不引入独立服务；本地数据便于设备重启后继续检索 | 保存和搜索文字会发送给 OpenAI；依赖 OpenAI key 和网络，模型工具调用不保证 |
| 10 | 新 agent runtime 用 SQLite，存 `~/.dongdong/dongdong.db` | 本地单用户，标准库 `sqlite3` 够用，不引入 Postgres/ORM | 多进程/多用户要换 |
| 11 | 模型协议只做 OpenAI 兼容 | 灵矽自定义模型本身就是 OpenAI 兼容；DeepSeek/OpenAI 都通；DeepSeek 特有用 `extra_body`，换端点清空即可 | Anthropic 原生协议以后再加 |
| 12 | 新 runtime 与旧 `agent.py` 并存 | 不打断现有灵矽语音链路，迁移可以分阶段 | 两套入口短期共存 |
| 13 | 测试拆到 `tests/agent_smoke.py` + `tests/agent_transport.py` | agent loop 的边界（路径、超时、历史裁剪）和 HTTP/SSE 传输必须自动测；`smoke.py` 已接近 300 行，单文件不能超 300 | 偏离「唯一测试文件」的约定，由 `check.sh` 统一跑 |
| 14 | shell 超时杀整个进程组 | `start_new_session=True` + `killpg` 才能杀掉管道和后台子进程，只杀顶层命令会留下孤儿 | 组内所有进程一起死，不能只杀其中一条 |
| 15 | shell 是有意保留的逃生口；路径守卫只覆盖文件工具 | shell 本来就要能跑任意命令（只挡少量明显危险命令），工作区白名单只对读写工具有意义 | 本地单用户威胁模型；shell 能碰到工作区外、继承环境变量（含 key），接远端或多用户前必须加沙箱 |
| 16 | Phase 1 关闭 DeepSeek thinking | DeepSeek V4 默认开思考；请求带 `tools` 时必须回传所有历史 `reasoning_content`，否则 400。Phase 1 不存思考内容，用 `extra_body` 关掉最省事 | 复杂任务少一层推理；以后要开思考，得在 messages 里持久化并回传 `reasoning_content` |
| 17 | 灵矽语音走设备 MCP，不做公网穿透；`code_backend=builtin` 时本地 Runtime 跑在专用线程 + 事件循环 | 平台是 MCP client，Mac 主动连出去，不需要入站端口；同步 `tools/call` 用 8 秒等待 + job id，避免语音回合被长任务卡死 | 语音只能轮询结果，不能主动推送；`builtin` 的 shell 能力暴露给语音，需审批/沙箱才适合多人 |
| 18 | 再加一套 `tests/agent_mcp.py` | MCP 的线程/事件循环、取消、默认 CLI 路由和 cwd 边界是会执行命令的边界，值得自动测 | 第四套测试文件，继续偏离「唯一测试文件」；由 `check.sh` 统一跑 |

- 2026-10-25：Skills 只发现本地 SKILL.md，按需读取；外部 MCP 用官方 Python SDK，连接限定一次 run，避免另建插件框架或常驻服务管理器。审批默认 auto 保留体验，ask 只批准一次准确调用，CLI 没有 UI 时拒绝；不把审批当作沙箱。三条能力并行实现，共享 Runtime/配置/页面入口由主 agent 集中接入。
- 2026-10-25：MCP SDK 在初始化超时后关闭流可能抛出含 BrokenResourceError 的 ExceptionGroup；清理阶段隔离普通异常，保留原始取消/任务异常，避免一个服务失败阻断其它工具，或把已完成任务改判失败。
- 2026-10-25：任务调度采用 SQLite WAL + 条件更新 claim/lease，不引入 Redis/RabbitMQ；本地单机已有持久化数据库，足以支持线程池和跨进程恢复，代价是暂不支持多机共享工作区。
- 2026-10-26：长任务完成暂不主动语音播报；现有协议只有云端 TTS → 设备链路，没有已确认的设备主动注入 TTS 消息格式。继续复用 `delivery` 事件和 `self.task.status` 轮询，待平台提供协议后再增加带会话/generation 校验的一次性通知。
- 2026-10-25：Web、灵矽和运行记录共用 Run/Goal/Item/Attempt 投影；工作台 MCP 页面继续管理外部 MCP，内置调度显示在任务记录中，避免混淆两个 MCP 角色。

## 已知的技术债（`shortcut:`）

- 2026-10-25：语音改为界面显式启停，run/web 均默认关闭，不持久化开启状态；关闭停止采音、播放和连接，清空缓冲后才确认。已派发编程任务不受影响。

- 2026-10-25：`run` 统一为 Web + Device 同进程、同事件循环；builtin MCP 用同步适配器投递到 WebState，设置保存与任务启动共用互斥，下一任务即时使用新配置。取代上一条“run 保留旧面板”的过渡方案；CLI 后端继续显式保留。

- 2026-10-25：Web 使用 FastAPI + React/Vite/Tailwind，先独立 `web` 命令，保留 `run` 的语音面板；仅本机访问。取消必须等 Runtime/子进程清理后释放进程内互斥，不能用 concurrent Future 的 cancel 返回值判断清理完成。跨进程互斥、统一入口后续再做。

| 位置 | 限制 | 什么时候该还 |
| --- | --- | --- |
| `screen.py` | AppleScript 选择器随 macOS 版本变，未在 macOS 27 上逐一验证 | 换系统大版本 / 用户反馈点不中 |
| `agent.py` | 一次只跑一个 agent 任务，第二个请求直接拒绝 | 需要并行改多个仓库时 |
| `audio.py` | 用 `echo_guard` 闭麦防回声，代价是不能边说边打断 | 换耳机或加 AEC 时 |
| `linx.py` | 只支持 opus，没做 PCM 兜底 | 平台换音频格式时 |
| `bridge.py` | 裸 HTTP + 可选 token，没有 TLS | 需要跨公网时 |

## 实测踩到的坑（2026-10）

- 2026-10-25：平台会在单轮结束后重连，因此 WS session_id 不能用作本地对话边界；普通语音与任务分别复用各自当前会话，主动“开启新对话”才切分，任务上下文随会话保留。

- 2026-10-25：语音返回到达但无声时，先检查系统静音；实测音量 75% 仍可能 `output muted:true`。普通 STT/TTS 原来只更新顶部状态，现存入独立“语音对话”，不保存音频、不重复记录 TTS start/stop。

**macOS 27 上控制中心没有可脚本化的菜单栏项。**
`process "ControlCenter"` 的 `menu bars` 数量为 0；状态栏项目都在 `MenuBarAgent` 里，
但它的项没有 title/description，`menu bar item i` 索引报 `invalid index`，只能读到 5 个无名的「组」。
结论：AirPlay 屏幕镜像没法用 AppleScript 自动点。

受影响：`self.screen.mirror` / `self.screen.stop`（已加前置判断，直接返回可读的原因，不让用户看 AX 报错）。
待选方案：

1. `ffmpeg -f avfoundation` 抓屏 → HLS → `pyatv play_url` 推给 Apple TV（可自动化，延迟 3–8s，需装 pyatv）
2. 用户手动投屏，语音只负责查设备/提示
3. 等系统恢复可访问性（不可控）

**平台会在对话结束时直接断链。** 首轮实测：TTS 播完后收到 `no close frame received or sent`（没有正常 close 帧），
我们 3 秒后自动重连并重新握手，平台接受。所以**不要**把断链当错误处理，重连逻辑必须保留。

## 待验证

- 2026-10-25：实测会议路径已保存但灵矽云端未调用工具就索要路径。MCP 工具发现附带连接时的会议目录，status/run/result 返回实时会议目录与阶段；工具发现上下文可能过期，云端提示词必须先查状态。普通 STT 仍只记录讨论，不因云端改写而生成或实施。

- [ ] macOS 27 上控制中心的「屏幕镜像」菜单结构（当前未开辅助功能权限，脚本未端到端验证）
- [ ] 灵矽 `realtime` 模式下连续推流的实际表现（是否会被平台限流）
- [ ] 服务器 TTS 实际采样率（文档说 16000 或 24000，代码按 hello 返回值自适应）

- 2026-10-25：会议代码修改模式显式开启，STT 普通讨论只持久化；本地完整指令触发整理/确认，MCP 只返回模式状态，避免云端改写讨论绕过确认。确认使用 SQLite 条件更新绑定当前方案哈希、展示回执和讨论截止序号。生成只提供只读工具和固定 Node 渲染，实施保留现有 shell 边界，不宣称文件级沙箱。
- 2026-10-25：Birdview 固定 cdea534（0.4.0），保留上游契约/渲染器与许可；lucide-static 完整包约 49MB，改为分发实际用到的 21 个图标并只安装 ajv/typebox。第三方分发代码不按本项目自有文件 300 行拆写；tests/smoke.py 在已有 344 行基础增加会议执行边界验证，保持唯一边界测试入口，后续仅在有重复覆盖时精简。
- 2026-10-25：实施 shell 完成后对比 Git 可见文件指纹，发现未批准文件变化时停止并记录，不自动回滚；此检查不能拦截执行过程，不能覆盖 Git 忽略路径和工作区外文件，仍不是沙箱。实际 diff 使用实施前后文件内容，包含批准的新建/暂存内容，不把已有用户 diff 当成本次成果。

- 2026-10-25：整合项目/会议/调度分支时保留所有边界断言，按实际领域拆meeting_smoke/task_smoke/story_smoke并由check统一调用，基础smoke仍≤300；Store原有DAG操作拆task_store，与会话复用同一连接锁，保持Store调用API。设备WebAgent与任务工具拆现有具体实现以遵守300行限制。调度Runtime复用run/user消息；会议保留受约束异步回调，线程审批回主循环。重启暂停遗留调度任务，避免未知写命令重放，活跃worker续租，取消真实退出后释放闸门。

- 2026-10-25：移除 meetings 单活唯一索引；work_gate 按会话 ID 互斥，配置仍全局独占。当前选择与各对话后台任务分开，语音只路由当前对话、确认固定接收时的方案；普通任务共享4个worker。会话隔离不隔离磁盘，同目录并行改动仍由方案基线和既有范围检查发现。

- 2026-10-25（本地故事集成）：故事实例按对话 ID 保存，SSE 捕获请求时实例，切换不影响原故事生成；故事与代码模式仅在同一对话互斥，画布随对话清空。故事持久化 API 归入已有 story_store，保持 Web 入口≤300行。
