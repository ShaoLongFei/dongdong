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
| 任务调度设计与计划 | [设计](superpowers/specs/2026-10-25-task-scheduler-design.md) / [计划](superpowers/plans/2026-10-25-task-scheduler.md) | SQLite DAG、worker、重试、恢复与统一状态 |
| 开发规范 | [AGENTS.md](../AGENTS.md) | 简化设计、依赖、测试与提交要求 |
| ESP32 路线 | [固件规划](../firmware/README.md) | 尚未实施的固件转发路线 |

## 当前 API 定位

Web API 由 `apps/device-mac/dongdong/web_server.py` 及三个能力 router 定义，默认仅在 `http://127.0.0.1:8766` 提供服务。运行后可通过 FastAPI 的 `/docs` 查看实际 schema。

| 接口 | 用途 |
| --- | --- |
| `GET /api/state` | 模型、任务、语音状态和当前会话入口 |
| `PUT /api/voice` | `{enabled: bool}`，开关语音 |
| `GET /api/sessions`、`POST /api/sessions` | 列出 / 新建会话 |
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
| [项目 / 分层记忆 / 归档设计](superpowers/specs/2026-10-25-projects-memory-archive-design.md) | 已确认项目绑定本地目录；完整设计待审阅，尚未实施 |
| [旧面板交接单](panel-handoff.md) | 历史记录；panel.py/panel.html 不再由 run 启动 |

## 维护约定

- 修改入口、进程边界、工具能力或存储路径时同步更新 `architecture.md`。
- 修改用户启动/配置步骤时更新 `runbook.md`；新增文档在本页登记。
- 平台坑和架构取舍记入 `decisions.md`；历史方案加状态说明，不把未实现目标写成当前能力。
- 架构图使用 Markdown 内的 Mermaid 源码维护，与文字一起审查，无需另装绘图软件。
