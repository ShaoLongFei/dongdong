# 架构

## 一句话

Mac 上跑一个「灵矽设备」：它负责听声音、放声音、把大模型的工具调用转成两件事——让 agent 写代码、让 Mac 投屏。

## 数据流

```
        ┌──────────── 灵矽平台 (xrobo.qiniu.com) ────────────┐
        │  ASR  →  智能体大模型（带 MCP 工具）  →  TTS        │
        └───────▲────────────────────────────┬──────────────┘
      WSS 音频/JSON │                        │ MCP tools/call
                ┌───┴────────────────────────▼──────────────┐
                │        apps/device-mac  (Python)           │
                │  linx.py   设备协议 + 会话     audio.py    │
                │  mcp.py    JSON-RPC 工具分发   tools.py    │
                │             Mem0 SDK                       │
                └───┬────────────────────────────┬───────────┘
                    │                            │
      ┌─────────────▼──────────┐      ┌──────────▼────────────┐
      │ agent.py               │      │ screen.py             │
      │ codex / claude /       │      │ zeroconf 发现 +       │
      │ deepseek 子进程         │      │ AppleScript 点投屏     │
      └────────────────────────┘      └───────────────────────┘

       Mem0 提取/嵌入 → OpenAI API
       本地持久化（仓库根目录 .memory/）：Qdrant 向量库 + SQLite 历史库
```

## 为什么是 Python

- 和 agent CLI（codex / claude）交互就是子进程 + 文本，Python 最短
- 音频、Opus、mDNS 都有现成库，不需要自己写
- 比赛周期短，先跑通；ESP32 端只做「音频 + 协议转发」，本来也不需要 Python

C++ 参考实现（[linx-linux](https://github.com/sunqirui1987/linx-linux)）留着做协议对照，不引入。

## 关键设计

**1. 工具只有一份实现。** `tools.py` 注册的工具同时供两条路用：
- 灵矽 MCP（`tools/call`）→ 语音直接控制
- 本地 HTTP bridge（`serve` 子命令）→ 以后 ESP32 用 HTTP 转发同一批工具

所以移植 ESP32 时，Mac 这边一行不改。

**2. agent 是子进程，不是 API。** 旧路径（`agent.py`）里 `codex exec` / `claude -p` 本身就是
完整 agent，自己做工具循环；我们只负责喂 prompt、收 stdout、管超时，不自己实现 agent loop。
新的内置 agent 不走子进程：`Runtime` 自己实现工具循环，见下文「内置 agent（新）」。

**3. 设备身份存本地。** `~/.dongdong/config.json` 存 Device-Id、Client-Id、token。
删除文件 = 重新注册一台设备。

## 内置 agent（新）

`apps/device-mac/dongdong/` 里新加了一套自己跑的 agent，和旧的 CLI 子进程方案并存：

| 文件 | 作用 |
| --- | --- |
| `runtime.py` | agent loop：模型流式返回 → 执行工具 → 工具结果回灌 → 再问模型，直到没有工具调用 |
| `llm.py` | OpenAI 兼容的流式模型客户端，用项目已有的 `httpx`，不抽 provider |
| `builtin_tools.py` | 内置工具：shell、读、写、改、列目录、glob、grep |
| `store.py` | SQLite 持久化：会话、消息、run、事件、工具调用；库在 `~/.dongdong/dongdong.db` |
| `chat_cli.py` | 终端入口，`dongdong chat` 直接和内置 agent 对话 |

- 旧 `agent.py` 仍是「CLI 执行器」，`dongdong run` 的灵矽语音链路完全不动。
- `dongdong chat` 不需要灵矽绑定，配好模型 key 就能用。
- 默认关闭 DeepSeek thinking：带 `tools` 时它要求回传历史 `reasoning_content`，Phase 1 不存，开着会 400。
- Web 面板和语音后续都复用 `Runtime`，不再各写一套循环。
- 边界测试在 `tests/agent_smoke.py` 和 `tests/agent_transport.py`，由 `scripts/check.sh` 统一跑。

## 工具清单

| 工具名 | 作用 |
| --- | --- |
| `self.system.status` | 当前 agent、工作目录、投屏目标、运行中的任务 |
| `self.code.run` | 把一句话交给 agent，同步等最多 N 秒，返回输出 |
| `self.code.result` | 查看上一条/指定任务的输出（跑太久时用） |
| `self.code.stop` | 杀掉正在跑的任务 |
| `self.screen.list` | 局域网里的 AirPlay 目标 |
| `self.screen.mirror` | 把 Mac 屏幕投到目标 |
| `self.screen.stop` | 停止投屏 |
| `self.memory.search` | 搜索当前设备的长期记忆 |
| `self.memory.save` | 保存稳定事实或偏好 |
| `self.memory.list` | 列出记忆及 ID |
| `self.memory.delete` | 按指定 ID 删除记忆 |

## ESP32 迁移路径

1. 烧官方 [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) 固件到板子，先跑通音频对话
2. 在固件 `mcp_server.cc` 加 7 个转发工具，POST `http://<mac-ip>:8765/call`
3. Mac 上 `dongdong serve --host 0.0.0.0`，配 `bridge.token`
4. 同一套工具名、同一套参数，平台侧不用改智能体

细节见 [`firmware/README.md`](../firmware/README.md)。
