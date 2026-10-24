# 咚咚通用 Agent 桌面版 · 设计规格

> 状态：已定方向，待实现。
> 范围：本地单用户 Mac 桌面软件。先做 coding + 文件 + shell，再做 Web 和语音。

## 0. 一句话

浏览器打开咚咚，和自己的 agent 对话；agent 能读写文件、跑 shell；灵矽通过“自定义模型”接进来当语音入口。不绑定灵矽，Web 也能完整使用。

## 1. 已定决策

| # | 决策 | 选择 | 后果 |
| --- | --- | --- | --- |
| 1 | 产品形态 | 本地单用户桌面软件 | 不做账号、多租户、团队协作；SQLite 足够 |
| 2 | 语音接入 | 灵矽“自定义模型”（C 方案） | 咚咚必须提供 OpenAI 兼容 `/v1/chat/completions`；灵矽云端必须能访问它 |
| 3 | Agent 内核 | 自研 loop | 自己做模型调用、工具循环、上下文、取消、持久化 |
| 4 | 第一版能力 | coding + 文件 + shell | 浏览器/Office/系统自动化后置 |
| 5 | Web | 像 ARTEX：侧栏 + 多页面 + 深色紧凑 | 用 React + Vite + Tailwind + shadcn/ui，静态构建后由 Python 服务 |

## 2. 产品边界

**做：**
- 一个本地 Web 应用，默认 `http://127.0.0.1:8766`。
- 一个内置 agent，会话持久化，可停止、可回看。
- 内置工具：shell、读文件、写文件、改文件、列目录、glob、grep。
- Skill 目录（`~/.dongdong/skills/<name>/SKILL.md`）和 MCP 客户端（stdio / HTTP / SSE）。
- 灵矽语音：自定义模型 + 音频设备链路；语音和 Web 共用同一个 agent。
- 首次引导、模型配置、工作区配置、日志、用量。

**不做（至少 MVP 不做）：**
- 多用户、登录体系、云端数据库、团队空间。
- ARTEX 的 planner/worker/探索图/资产图/漏洞/流量/报告。
- 浏览器自动化、Office、系统级 GUI 自动化。
- 通知、定时任务、插件市场、ESP32 固件。
- 自建云端 relay（如果以后要“零配置语音”，单独做）。

## 3. 架构

```
┌────────────────────────── 本机单进程 ──────────────────────────┐
│  FastAPI / uvicorn                                             │
│    ├── /api/*        Web UI 的 REST + SSE                      │
│    ├── /v1/*         灵矽自定义模型的 OpenAI 兼容端点           │
│    └── /             React 静态构建产物                         │
│                                                                │
│  Agent Runtime (asyncio)                                       │
│    ├── LLM client    OpenAI 兼容 /v1/chat/completions           │
│    ├── Tool loop     shell / read / write / edit / glob / grep  │
│    ├── Skills        SKILL.md 目录，按需读取                    │
│    └── MCP client    stdio / Streamable HTTP / SSE              │
│                                                                │
│  SQLite  ~/.dongdong/dongdong.db                               │
│  Linx 设备任务（可选） 现有 linx.py / audio.py                  │
└────────────────────────────────────────────────────────────────┘
        ▲                                   ▲
        │ 浏览器                             │ 灵矽云端
        │                                   │  HTTPS（公网地址/隧道）
```

**进程模型：** 一个 Python 进程，asyncio 事件循环；Web、agent、灵矽设备任务都在里面。MVP 不拆独立 worker，避免 IPC。长任务用 asyncio task，不阻塞 HTTP。

**前端：** 开发时 Vite dev server；生产 `npm run build` 出静态文件，FastAPI 直接服务。不在生产跑 Node。

**为什么不用 ARTEX 那套 Go/Postgres/planner-worker：** 那是渗透测试的探索图引擎；通用桌面 agent 先用单 agent loop + SQLite，复杂度低一个数量级。

## 4. Agent Runtime

### 4.1 一次 run

```
用户消息
  → 读会话历史 + system prompt + 工具 schema + skill 目录
  → 调模型（流式）
  → 有 tool_calls：逐个执行，结果写回消息，继续下一轮
  → 无 tool_calls：保存最终回答，run 结束
```

- 最大步数默认 20，超过记 `error`，不无限循环。
- 文本流、工具调用、工具结果、结束/错误都作为事件发出；Web 用 SSE 消费。
- 取消：取消 asyncio task；正在跑的 shell 子进程要 kill。
- 历史裁剪：MVP 保留最近 30 条消息；不能把 assistant(tool_calls) 和对应 tool 结果裁开（Review Focus 里单独测）。
- token 用量从模型响应 `usage` 取，存到 run 上。

### 4.2 工具

| 工具 | 行为 | 边界 |
| --- | --- | --- |
| `shell` | 在工作区跑命令，合并 stdout/stderr，超时 kill | 默认 30s；输出截断 12000 字；拒绝明显破坏性命令 |
| `read_file` | 读文本文件 | 最大 200KB；只允许工作区内 |
| `write_file` | 写文本，自动建父目录 | 只允许工作区内 |
| `edit_file` | 精确替换一段文本 | 旧串必须唯一；只允许工作区内 |
| `list_dir` | 列目录，目录带 `/` | 最多 200 项 |
| `glob` | 按 pattern 找文件 | 结果必须在工作区内；最多 200 项 |
| `grep` | 正则搜文件内容 | 跳过 `.git/.venv/node_modules`；最多 100 条 |

**路径安全：** 所有路径 `resolve()` 后必须 `is_relative_to(workspace)`；符号链接按解析后的真实路径判断。

**审批：** MVP 是本地单用户，先采用“工作区内允许 + 危险命令 denylist + 超时”。Web 端审批弹窗放到第三阶段；届时 `shell` 和写操作可以按配置改成 ask。

### 4.3 模型

- 只做 OpenAI 兼容协议（`/v1/chat/completions`，流式，tools）。
- 配置：`base_url`、`api_key`、`model`、`temperature`、`max_tokens`。
- 默认 DeepSeek：`https://api.deepseek.com/v1` + `deepseek-chat`，key 从 `DEEPSEEK_API_KEY` 读。
- 不急着做 provider 抽象；等真的接 Anthropic 再抽。

## 5. Web

### 5.1 页面

| 页面 | 内容 |
| --- | --- |
| 对话 | 会话列表、消息流、工具调用卡片、输入框、停止按钮 |
| 运行 | 当前/历史 run：步骤、工具、耗时、token、错误 |
| 技能 | 本地 skill 列表、查看 SKILL.md、启用/禁用 |
| MCP | 添加/测试/删除 MCP server，查看工具 |
| 设置 | 模型、工作区、语音、安全 |
| 日志 | 最近日志、用量 |

MVP 先做“对话 + 运行 + 设置”；技能/MCP 页面随第三阶段。

### 5.2 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/state` | 健康、模型是否配置、语音状态 |
| GET/POST | `/api/sessions` | 列出/新建会话 |
| GET | `/api/sessions/{id}` | 会话详情 + 消息 |
| POST | `/api/sessions/{id}/messages` | 发消息，返回 `run_id` |
| GET | `/api/runs/{id}/events?since=` | SSE 事件流 |
| POST | `/api/runs/{id}/cancel` | 停止 |
| GET/PUT | `/api/settings` | 读/写设置 |
| POST | `/v1/chat/completions` | 灵矽自定义模型入口 |

### 5.3 鉴权

- 默认只监听 `127.0.0.1`，本地单用户不登录。
- 监听 `0.0.0.0` 时必须设置访问密码/令牌；MVP 可以只允许改配置，不做账号体系。
- `/v1/chat/completions` 用独立 `voice.api_key` 校验 `Authorization: Bearer`。

## 6. 数据

路径统一在 `~/.dongdong/`：

```
config.json          配置（0600）
dongdong.db          SQLite
logs/                日志
skills/              SKILL.md
```

表：

- `sessions(id, title, channel, created_at, updated_at)`
- `messages(id, session_id, role, content, tool_calls, tool_call_id, created_at)`
- `runs(id, session_id, status, error, usage, started_at, ended_at)`
- `events(id, run_id, type, data, created_at)`
- `tool_calls(id, run_id, name, args, result, ok, ms, created_at)`
- `settings(key, value)`

后续加 `skills`、`mcp_servers`、`approvals`。

## 7. 灵矽语音（C 方案）

### 7.1 端点

`POST /v1/chat/completions`，兼容 OpenAI：

- 请求：`model`、`messages`、`stream`、`temperature`、`max_tokens`。
- 响应：`stream=true` 时逐条 `data: {...}`，最后 `data: [DONE]`；`stream=false` 时返回完整 JSON。
- 鉴权：`Authorization: Bearer <voice.api_key>`。
- 工具：灵矽请求里如果带 `tools`，忽略；咚咚用自己的工具，避免两个工具循环打架。

### 7.2 会话映射

- 优先用灵矽请求头里的 `X-Linx-Device-Id` / `X-Linx-Session-Id`（如果实际请求带）。
- 没有的话，用请求里的 `user` 字段；再没有就用一个固定的 `voice` 会话。
- 具体字段在 Phase 4 用真实请求抓一次再定；这是必须验证的点，不靠猜。

### 7.3 语音风格

- `channel=voice` 时 system prompt 要求：短句、口语、不要 Markdown、不要一次说太多。
- 长任务：先回一句“开始做了”，把任务放后台；完成后在 Web 看结果。灵矽的模型调用是同步的，不能指望平台主动推送后续语音；如果一定要语音回报，再走设备 MCP 的 `agent.chat` 工具（后续）。

### 7.4 公网可达性（硬约束）

灵矽云端必须能访问 `/v1/chat/completions`。本地 Mac 默认不可达；如果灵矽部署在同一内网，可以直接填局域网地址，公网版则必须走下面的方式。MVP 支持两种：

1. **用户自己的公网地址**：Cloudflare Tunnel / ngrok / Tailscale Funnel / 反代，设置里填 `voice.public_url`。
2. **内置 quick tunnel（可选）**：检测到 `cloudflared` 就起一个临时隧道，把 URL 显示在设置页。URL 重启会变，只适合演示。

MVP **不做**自建 relay。如果以后要“任何网络零配置”，再加一个只做转发的轻量云服务；那是独立项目。

### 7.5 灵矽控制台配置

1. 在灵矽“模型配置”里添加自定义模型：类型 `openai`，`base_url=https://<public>/v1`，`api_key=<voice.api_key>`，`model_name=dongdong`。
2. 智能体选择这个模型。
3. 智能体的 MCP 工具可以留空；咚咚自己的工具已经够用。设备控制类工具（屏幕/记忆）后续按需加。

## 8. 安全

- 默认只监听 `127.0.0.1`；语音端点单独 token。
- 工作区路径白名单；工具不能越界。
- shell 超时 + 输出上限 + denylist；MVP 接受“本机单用户”的风险，第三阶段加审批。
- API key 存 `~/.dongdong/config.json`，权限 0600；不写日志。
- 不收集遥测；语音/记忆数据发往灵矽/模型厂商的事实要在设置页写清楚。

## 9. MVP 验收

1. 不绑定灵矽，Web 里能新建会话、聊天、看流式输出、看工具调用、停止任务。
2. 重启进程后，会话、消息、run、工具调用都还在。
3. agent 能在一个工作区里读文件、改文件、跑 shell 完成一个真实小任务。
4. 设置页能配模型和工作区；没配模型时有明确引导。
5. 灵矽自定义模型能连上 `/v1/chat/completions`，语音说一句话，Web 里能看到同一个会话和工具调用。
6. 危险路径/越界路径被拒绝；shell 超时不会卡死进程。

## 10. 后续

- 第三阶段：Skills、MCP、审批、工作区文件浏览/产物预览。
- 第四阶段：灵矽设备绑定页、语音会话映射、长任务语音回报。
- 更后：浏览器/Office 工具、多 agent、任务编排、ESP32。
