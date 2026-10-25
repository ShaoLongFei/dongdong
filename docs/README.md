# 文档导航

> 更新于 2026-10-25。当前架构以代码和 [项目架构](architecture.md) 为准；设计规格、实施计划记录各阶段的目标与过程，不代表全部已交付。

## 从哪里开始

| 需要了解 | 文档 | 内容 |
| --- | --- | --- |
| 项目概况与启动 | [仓库首页](../README.md) | 能力、安装命令、目录 |
| 整体设计与代码定位 | [项目架构](architecture.md) | 总体图、任务时序、数据关系、模块和实现边界 |
| 安装、配置和排错 | [操作手册](runbook.md) | 绑定、语音开关、工作台、Skills/MCP/审批、投屏 |
| 配置外部 CLI agent | [接入 code agent](agents.md) | codex/claude/deepseek 与自定义命令；仅 CLI 后端 |
| 配置灵矽智能体 | [语音提示词](linx-voice-prompt.md) | 工具选择、8 秒等待、连续任务与审批 |
| 设备协议字段 | [灵矽协议摘要](linx-protocol.md) | OTA、WSS、音频与设备 MCP |
| 设计取舍与平台限制 | [决策记录](decisions.md) | 按时间记录的选择；后续记录可替代早期方案 |
| 任务调度设计与计划 | [设计](superpowers/specs/2026-10-25-task-scheduler-design.md) / [计划](superpowers/plans/2026-10-25-task-scheduler.md) | 已交付 SQLite DAG、自动规划、Goal 验收证据、worker、重试、恢复与统一状态；受限修复规划、文件范围冲突、停摆检测和有限 drain 已交付；跨进程自动接管与最终验收 Planner 仍在计划中 |
| 开发规范 | [AGENTS.md](../AGENTS.md) | 简化设计、依赖、测试与提交要求 |
| ESP32 路线 | [固件规划](../firmware/README.md) | 尚未实施的固件转发路线 |

## 当前 API 定位

Web API 由 `apps/device-mac/dongdong/web_server.py` 及三个能力 router 定义，默认仅在 `http://127.0.0.1:8766` 提供服务。运行后可通过 FastAPI 的 `/docs` 查看实际 schema。

| 接口 | 用途 |
| --- | --- |
| `GET /api/state` | 模型、任务、语音状态和当前会话入口 |
| `PUT /api/voice` | `{enabled: bool}`，开关语音 |
| `GET/POST /api/projects`、`PATCH /api/projects/{id}`、`PUT /api/projects/current` | 项目管理与显式语音项目 |
| `GET/POST /api/sessions`、`PATCH /api/sessions/{sid}` | 分页会话、移动、归档/恢复 |
| `GET/POST/DELETE /api/memories` | 全局/项目记忆管理 |
| `GET /api/sessions/{sid}` | 会话消息与 runs |
| `POST /api/sessions/{sid}/messages` | `{text: string}`，发起任务并返回 run_id |
| `GET /api/runs/{rid}` | 状态；`include_events=true` 包含历史事件 |
| `GET /api/runs/{rid}/events` | SSE；`since` 指定事件游标，结束发 `event: end` |
| `POST /api/runs/{rid}/cancel` | 请求取消；清理异步完成 |
| `GET /api/settings`、`PUT /api/settings` | 模型、工作区、语音编程后端与执行策略 |
| `GET /api/skills`、`GET /api/skills/{name}` | 本地技能列表 / 启用技能正文 |
| `PUT /api/skills/{name}` | `{enabled: bool}`，保存技能开关 |
| `GET /api/mcp`、`PUT /api/mcp/{name}`、`DELETE /api/mcp/{name}` | 外部 MCP 配置管理 |
| `POST /api/mcp/{name}/test` | 显式连接、发现工具后关闭，不执行工具 |
| `GET /api/approvals` | 全局待审批列表 |
| `POST /api/approvals/{run_id}/{call_id}` | `{approved: bool}`，批准一次 / 拒绝 |
| `PUT /api/story/mode`、`GET /api/story/state` | 开始/结束故事，读取当前故事状态 |
| `GET /api/story/events` | 当前或指定故事的 SSE 场景流，支持 `story_id` 与 `since` |
| `GET /api/stories`、`GET /api/stories/{id}`、`POST /api/stories/{id}/replay` | 故事历史、详情和重播 |
| `POST/GET /api/stories/{id}/exports` | 创建/查询 PNG、PDF、MP4 导出任务；完成后使用返回的同源下载地址 |

### 故事模式

故事模式可以从工作台按钮或语音命令“开始讲故事”进入。每段语音会生成受控的 JSON 场景和 SVG，实时通过 `GET /api/story/events?story_id=<id>&since=<cursor>` 推送到画布；“结束讲故事”会等待最后一段并把故事标记为完成。故事文件按故事 ID 存放在 `~/.dongdong/stories/<story_id>/`，包含 `manifest.json`、场景 JSON/SVG 和 `exports/` 目录。

历史故事只能恢复画布、重播和导出，不会自动打开麦克风；查看历史时 SSE 使用所选 `story_id`，开始新故事后画布自动切回当前故事。PNG/PDF/MP4 导出都是后台任务，可用上表中的查询接口轮询；PNG 及 PDF 当前按故事首幕渲染，MP4 按全部场景和各幕时长合成，并附带静音 AAC 音轨。

MP4 只调用仓库内的 FFmpeg：Apple Silicon 使用 `tools/ffmpeg/darwin-arm64/ffmpeg`，Intel macOS 使用 `tools/ffmpeg/darwin-x86_64/ffmpeg`，启动导出前会校验对应 `SHA256` 文件，不依赖系统 `PATH`。当前随仓库提供的构建启用了 GPL、version3 和 nonfree 组件；公开分发二进制或产品时需按 FFmpeg 及其编解码器的许可要求复核并补充声明。

设置和能力变更与任务共用进程内互斥，冲突返回409；已处理或过期审批也返回409。模型 key、MCP env/header 的值不经查询 API 回显。

独立 `serve` 默认 `127.0.0.1:8765`，提供 `GET /tools` 与 `POST /call`，属于设备工具 HTTP bridge；不是上述 Web API，也没有实现灵矽自定义模型 `/v1/chat/completions` 入站接口。

## 设计和实施记录

这些文件保留原阶段内容，阅读时先看状态说明和后续决策。

| 记录 | 当前阅读方式 |
| --- | --- |
| [通用 Agent 规格](superpowers/specs/2026-10-24-general-agent-design.md) | 产品方向；早期 `/v1` 语音入口已由设备 MCP 路线替代，部分后续功能未做 |
| [内置 Runtime 计划](superpowers/plans/2026-10-24-agent-runtime.md) | 早期任务拆解；当前代码采用同目录模块，不能按旧清单判断待办 |
| [Web 工作台计划](superpowers/plans/2026-10-25-web-console.md) | Web MVP 的实施记录 |
| [统一运行计划](superpowers/plans/2026-10-25-unified-runtime.md) | Web/语音共用执行宿主；后续演进为 run/web 同入口且语音默认关闭 |
| [进度规格](superpowers/specs/2026-10-24-task-progress-design.md) / [计划](superpowers/plans/2026-10-24-task-progress.md) | 与内置 Runtime 无关的早期 CLI 多步骤调度方案，未作为当前实施依据 |
| [记忆规格](superpowers/specs/2026-10-24-mem0-memory-design.md) / [计划](superpowers/plans/2026-10-24-mem0-memory.md) | 设备工具层 Mem0 接入；不代表 Runtime 自动记忆 |
| [Skills / MCP / 审批计划](superpowers/plans/2026-10-25-agent-capabilities.md) | 已实施，含验证和集成交付记录 |
| [项目 / 分层记忆 / 归档设计](superpowers/specs/2026-10-25-projects-memory-archive-design.md) | 已实现并整合main：项目目录、分层记忆、会话归档/恢复 |
| [会议代码修改模式与 Birdview](superpowers/specs/2026-10-25-meeting-code-mode-design.md) | 已确认显式召唤整理方案、按钮及语音确认；已在隔离分支实施，真实会议收音及视觉验收待进行；[实施记录](superpowers/plans/2026-10-25-meeting-code-mode.md) |
| [旧面板交接单](panel-handoff.md) | 历史记录；panel.py/panel.html 不再由 run 启动 |

## 维护约定

- 修改入口、进程边界、工具能力或存储路径时同步更新 `architecture.md`。
- 修改用户启动/配置步骤时更新 `runbook.md`；新增文档在本页登记。
- 平台坑和架构取舍记入 `decisions.md`；历史方案加状态说明，不把未实现目标写成当前能力。
- 架构图使用 Markdown 内的 Mermaid 源码维护，与文字一起审查，无需另装绘图软件。
