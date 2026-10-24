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
./scripts/dev.sh run          # 开始语音控制
```

详细步骤见 [`docs/runbook.md`](docs/runbook.md)。

## 目录

| 目录 | 放什么 |
| --- | --- |
| `apps/device-mac/` | Mac 设备端：灵矽 WS 协议、音频、MCP 工具实现 |
| `firmware/` | ESP32 固件规划（复用 xiaozhi-esp32） |
| `docs/` | [架构](docs/architecture.md) · [协议摘要](docs/linx-protocol.md) · [操作手册](docs/runbook.md) · [接入 agent](docs/agents.md) · [决策记录](docs/decisions.md) |
| `scripts/` | setup / dev / check |
| `deploy/` | launchd 自启、环境变量样例 |
| `tests/` | 唯一的 smoke 测试 |

## 接入自己的 agent

`codex` / `claude` / `deepseek` 都只是配置项。想换成 ollama、某个内部 CLI，改 `~/.dongdong/config.json` 的 `agents` 就行，代码不用动：

```json
{ "agents": { "ollama": { "cmd": ["ollama", "run", "qwen2.5-coder:7b", "{prompt}"], "timeout_s": 300 } } }
```

见 [docs/agents.md](docs/agents.md)。

## 规矩

开发规范见 [`AGENTS.md`](AGENTS.md)：不写冗余单测、能借开源库就不自己写、单文件不超 300 行。
