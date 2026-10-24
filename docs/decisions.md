# 决策记录

只记「为什么这么选」，不记过程。新决策往下加。

| # | 决策 | 理由 | 代价 |
| --- | --- | --- | --- |
| 1 | Mac 端用 Python，不用 C++ | 和 CLI agent 交互就是子进程；音频/Opus/mDNS 都有现成库 | 不能直接复用到 ESP32，但 ESP32 本来也不需要这些 |
| 2 | 工具实现只有一份（`tools.py`），MCP 和本地 HTTP 共用 | ESP32 阶段只加转发，Mac 侧零改动 | 多一个 HTTP 服务（30 行） |
| 3 | agent 用 CLI 子进程，不自己实现 agent loop | codex / claude 自带工具循环，重写毫无意义 | 受 CLI 能力限制（如不能细粒度控制） |
| 4 | 投屏用 AppleScript 点控制中心 | 系统没有公开 API；私有框架（AirPlaySender）不稳定 | 依赖辅助功能权限，macOS 升级可能失效 → 提供 `mirror_command` 覆盖 |
| 5 | 不自己实现唤醒词 | 平台 `realtime` 模式自带 VAD/打断，够用 | 环境噪音会触发；后续可加本地 KWS |
| 6 | 单文件测试 `tests/smoke.py` | 只保护协议/编解码/路径边界，避免测试拖慢开发 | 没有覆盖 UI 交互，靠手动验 |
| 7 | DeepSeek 走 Claude Code 的兼容端点 | 不用自己写 agent loop，也不用装新 CLI | 需要 `DEEPSEEK_API_KEY`，功能受端点限制 |
| 8 | 面板跟 `run` 同进程（守护线程 HTTP），默认只听 `127.0.0.1` | 直接读取进程内状态，不加 IPC；本机使用默认不暴露到局域网 | 需要远程访问时自行配置监听地址和 token |

## 已知的技术债（`shortcut:`）

| 位置 | 限制 | 什么时候该还 |
| --- | --- | --- |
| `screen.py` | AppleScript 选择器随 macOS 版本变，未在 macOS 27 上逐一验证 | 换系统大版本 / 用户反馈点不中 |
| `agent.py` | 一次只跑一个 agent 任务，第二个请求直接拒绝 | 需要并行改多个仓库时 |
| `audio.py` | 用 `echo_guard` 闭麦防回声，代价是不能边说边打断 | 换耳机或加 AEC 时 |
| `linx.py` | 只支持 opus，没做 PCM 兜底 | 平台换音频格式时 |
| `bridge.py` | 裸 HTTP + 可选 token，没有 TLS | 需要跨公网时 |

## 实测踩到的坑（2026-10）

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

- [ ] macOS 27 上控制中心的「屏幕镜像」菜单结构（当前未开辅助功能权限，脚本未端到端验证）
- [ ] 灵矽 `realtime` 模式下连续推流的实际表现（是否会被平台限流）
- [ ] 服务器 TTS 实际采样率（文档说 16000 或 24000，代码按 hello 返回值自适应）
