# 咚咚 (dongdong)

对着空气说一句话，Mac 上的 AI 就开始写代码，屏幕自动投到电视上。

1024 程序设计大赛参赛项目：**通过 AirPlay 连接屏幕，通过语音控制 code agent 做开发。**

```
🎙 你说话
   ↓
灵矽平台（ASR → 大模型 → TTS）  ←── WebSocket ──┐
   ↓ MCP tools/call                              │
apps/device-mac（Python，跑在 Mac 上）──────────┘
   ├── codex / claude / deepseek  写代码
   └── AirPlay 屏幕镜像           投到电视 / 投影
```

## 快速开始

```bash
./scripts/setup.sh            # 建 venv、装依赖
./scripts/dev.sh doctor       # 环境体检（麦克风、扬声器、agent、投屏目标）
./scripts/dev.sh bind         # 首次：拿激活码，去 xrobo.qiniu.com 绑定设备
./scripts/dev.sh run          # 启动工作台，界面按钮开启语音

# 不绑定灵矽也能手动用内置 agent（需要 DEEPSEEK_API_KEY）
export DEEPSEEK_API_KEY=...
./scripts/dev.sh chat --message "读一下 README 并总结"

# 独立 Web 工作台：对话、运行记录、模型/工作区设置
cd apps/web && npm ci && npm run build && cd ../..
./scripts/dev.sh web          # http://127.0.0.1:8766
```

详细步骤见 [`docs/runbook.md`](docs/runbook.md)。

`web` 和 `run` 二选一启动，语音默认关闭，顶部按钮可随时开启/关闭麦克风和播报。设置页把语音后端设为 `builtin` 后，语音任务和 Web 共享模型、工作区、运行记录和停止入口；配置保存后下一任务生效。

对话内会展开执行卡：多步骤任务显示步骤清单、当前与接下来要做的事，工具调用和耗时实时更新。交付后自动折叠，可展开回看；刷新后保留历史。语音发起任务时，点击“查看当前步骤”进入对应任务。连续语音任务会追加到当前任务会话并保留上下文，重连和麦克风开关不会创建新会话；需要独立上下文时点击“开启新对话”。步骤由内置 agent 更新，模型未提供清单时显示实际工具或等待状态；此进度卡适用于 `builtin` 后端。

## 技能、外部工具和审批

- **技能**：将已有技能放到 `~/.dongdong/skills/<名称>/SKILL.md`，在“技能”页刷新、查看和启停。内置 agent 先看描述，需要时读取完整指令；不自动下载或安装。
- **MCP**：在“MCP”页添加本机 stdio、Streamable HTTP 或 SSE 服务，保存后可测试连接和查看工具。只有启用的服务才会在任务开始时连接，任务结束或取消后关闭。密钥放环境变量或请求头字段（支持 `${ENV}`），列表不回显其值。
- **审批**：设置中的“工具执行方式”默认自动执行；切换“逐次确认”后，内置 agent 写文件、跑命令、调用外部 MCP 工具前会等待“允许一次”或“拒绝”。审批卡在所有页面可见，停止任务会撤销等待。CLI 无审批界面时直接拒绝，不会挂住。此设置不接管旧 CLI 后端自身的权限行为。

## 目录

| 目录 | 放什么 |
| --- | --- |
| `apps/device-mac/` | Mac 设备端：灵矽 WS 协议、音频、MCP 工具实现 |
| `apps/web/` | React + Vite + Tailwind 工作台；构建后由 Python 服务 |
| `firmware/` | ESP32 固件规划（复用 xiaozhi-esp32） |
| `docs/` | [架构](docs/architecture.md) · [协议摘要](docs/linx-protocol.md) · [操作手册](docs/runbook.md) · [接入 agent](docs/agents.md) · [语音提示词](docs/linx-voice-prompt.md) · [决策记录](docs/decisions.md) |
| `scripts/` | setup / dev / check |
| `deploy/` | launchd 自启、环境变量样例 |
| `tests/` | 四套 smoke：协议/边界（`smoke.py`）+ agent loop（`agent_smoke.py`）+ 传输（`agent_transport.py`）+ MCP job（`agent_mcp.py`） |

## 语音指挥本地 agent

灵矽云端仍然负责 ASR/对话/TTS；需要操作 Mac 时，云端模型调用设备 MCP 的 `self.code.run`，
Mac 上的 agent 执行。**不需要公网穿透，也不开放入站端口。**

```json
{ "code_backend": "builtin" }
```

默认 `cli` 保持旧 codex/claude 路径不变；改成 `builtin` 后走本地 `Runtime`。提示词模板、
时间限制和安全边界见 [`docs/linx-voice-prompt.md`](docs/linx-voice-prompt.md)。

## 接入自己的 agent

`codex` / `claude` / `deepseek` 都只是配置项。想换成 ollama、某个内部 CLI，改 `~/.dongdong/config.json` 的 `agents` 就行，代码不用动：

```json
{ "agents": { "ollama": { "cmd": ["ollama", "run", "qwen2.5-coder:7b", "{prompt}"], "timeout_s": 300 } } }
```

见 [docs/agents.md](docs/agents.md)。

## 规矩

开发规范见 [`AGENTS.md`](AGENTS.md)：不写冗余单测、能借开源库就不自己写、单文件不超 300 行。
