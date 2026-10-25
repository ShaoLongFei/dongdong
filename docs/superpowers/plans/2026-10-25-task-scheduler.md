# 咚咚生产任务调度内核实施计划

> **当前状态（2026-10-25）**：基础调度、自动规划、多 Item DAG、独立 Item Runtime 上下文、Web/MCP 投影和规划状态展示已完成并推送到 `main`。本文下面的 checkbox 是历史实施记录；继续演进请以“剩余事项”为准。

## 剩余事项

1. **Goal 验收证据**：已完成。Run 收口前持久化 acceptance、Item 结果摘要和 `goal_verified` 事件；有约束但无 verifier 时进入 `needs_review`。
2. **失败修复与重新规划**：根据失败 Item、工具输出或用户 steer 追加修复 Item、替换规划或重新规划。
3. **文件范围冲突**：为 Item 增加读写范围，检测重叠并自动串行化或拒绝不安全并行。
4. **完整 Monitor 与收口**：补齐自动监察循环、停摆检测、有限 drain、最终验收 Planner 和严格收口顺序。
5. **调度配置化**：增加 `task_scheduler` 配置节，统一约束 worker、lease、poll、retry、deadline 和 drain。
6. **跨进程恢复**：在现有 WAL/CAS/lease 之上实现真正的 worker 协调与可选自动恢复；当前重启策略仍要求用户显式恢复遗留 Run。

故事模式、真实浏览器视觉交互、真实 DeepSeek、灵矽音频、用户自有 MCP 和云端记忆不属于本模块剩余事项。

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 将咚咚的一次 Web/灵矽请求持久化为可观察、可恢复的 Run/DAG，并以 SQLite CAS、有限重试、多 worker 和统一任务状态兼容现有 Runtime/CLI 入口。

**Architecture:** 在现有 Store 之上增加幂等迁移和调度领域表；用 TaskManager 统一创建 Run、规划 Item、claim/lease、执行和收口。Worker 通过执行器适配器调用现有内置 Runtime 或 CLI，所有状态变化写入同一事件流，Web/MCP 只读取任务投影。第一版保持单进程默认运行，多 worker 通过线程池和 SQLite lease 工作，跨进程恢复依靠 WAL 与 lease 扫描。

**Tech Stack:** Python 3.11+ 标准库 sqlite3、asyncio、concurrent.futures；现有 FastAPI、Runtime、agent CLI；SQLite WAL。

**Spec:** docs/superpowers/specs/2026-10-25-task-scheduler-design.md

## Global Constraints

- Python 3.12 目标，保持当前项目 requires-python >=3.11 兼容。
- 单文件不超过 300 行；新增模块按 Store、模型、调度、执行器职责拆分。
- 依赖只使用标准库和当前已安装依赖，不引入 Redis/RabbitMQ。
- 所有用户工作区路径必须位于配置 workspace 内；Agent 名称来自配置。
- 并发写入同一 workspace 默认串行；只有明确的文件范围元数据才允许并行。
- 协议、并发、租约、执行命令边界写入现有 smoke 入口，不为 getter/UI 胶水写测试。
- 每个阶段提交前运行 ./scripts/check.sh；提交前先 git pull --rebase，再推送。

## Review Focus

- 两个 worker 同时 claim 一个 open Item 时只能有一个成功；测试归 Task 1。
- 依赖环、缺失依赖、依赖失败后的阻断必须在落库前拒绝或收口；测试归 Task 1。
- lease 过期、进程重启和终态 Run 不得留下可重复执行或永久 running 的 Item；测试归 Task 1/4。
- CLI/builtin 执行失败的 retryable、blocked、exhausted 分类不能互相混淆；测试归 Task 2/4。
- self.code.run/result/stop 与 Web SSE 必须继续支持旧调用并看到统一 Run 状态；测试归 Task 3/5。

### Task 1: SQLite 调度领域模型与原子 Store

**Files:**
- Modify: apps/device-mac/dongdong/schema.sql
- Modify: apps/device-mac/dongdong/store.py
- Create: apps/device-mac/dongdong/task_model.py
- Test: tests/smoke.py

**Interfaces:**
- Produces RunSpec、ItemSpec、TaskView 数据结构。
- Produces Store.create_task_run、Store.add_items、Store.claim_item、Store.complete_item、Store.fail_item、Store.requeue_expired、Store.task_view。

- [ ] Step 1: 在 tests/smoke.py 增加临时 SQLite 调度场景：创建两个 Item DAG；断言环和缺失依赖被拒绝；两个独立 Store 连接同时 claim 同一 Item 只有一个成功；lease 过期后可重领；失败 Item 阻断下游；终态 Run 不可 claim。
- [ ] Step 2: 运行 .venv/bin/python tests/smoke.py，确认新增调度测试因接口不存在而失败。
- [ ] Step 3: 在 task_model.py 定义 RunSpec、ItemSpec、AttemptResult、TaskView；在 schema.sql 增加 goals/items/item_deps/attempts 字段及索引，并为旧 runs/events 幂等补列。
- [ ] Step 4: add_items 在同一事务做依赖存在性检查和 DFS 环检测；claim_item 使用 BEGIN IMMEDIATE、状态/Run/依赖条件更新和 lease；状态变化同事务写 event；返回字典或 dataclass。
- [ ] Step 5: 运行新增 smoke 与现有验证，确认调度及既有场景通过。
- [ ] Step 6: git add 相关文件并提交 feat: add persistent task DAG store。

### Task 2: Planner、Scheduler、Worker 与执行器适配器

**Files:**
- Create: apps/device-mac/dongdong/task_runtime.py
- Create: apps/device-mac/dongdong/task_scheduler.py
- Modify: apps/device-mac/dongdong/config.py
- Test: tests/smoke.py

**Interfaces:**
- TaskPlanner.plan(text, cwd, agent) 返回合法的单 Item 或显式 DAG。
- TaskExecutor.execute(item, run, cancel_event) 返回 AttemptResult。
- TaskManager.submit(session_id, text, cwd=None, channel=web) 返回 run id；status/stop/resume 返回任务投影。
- TaskManager.start_workers、close 管理线程池；默认 max_workers=1。

- [ ] Step 1: 写执行器和调度 smoke：临时工作区和短命令/可注入 runner，断言单 Item 最终 done；retryable 最多两次后 exhausted；stop 设置 stopped；max_workers=2 时两个就绪 Item 并行且无重复 claim。
- [ ] Step 2: 运行 smoke 确认接口缺失或状态不满足。
- [ ] Step 3: 实现 TaskPlanner/TaskExecutor。单文本默认一个 builtin/CLI Item；CLI 复用 agent.run，builtin 复用 Runtime.run；异常分类为 retryable/blocked/exhausted，不保存凭据。
- [ ] Step 4: 实现 TaskManager polling/claim/lease/retry：恢复 expired lease，claim frontier，线程池执行，结果写 Attempt/Event，下游失败自动 blocked；全部 done 时 done，blocked/exhausted 且无可恢复项时 failed。
- [ ] Step 5: 增加取消事件和有限 drain；stop 标记 canceled，停止新 claim，worker 在工具边界检查，超时外部 CLI 由适配器终止。
- [ ] Step 6: 运行 ./scripts/check.sh，确保新旧 smoke 全绿。
- [ ] Step 7: git add 相关文件并提交 feat: run task DAG workers with retry and leases。

### Task 3: WebState 与灵矽 MCP 入口统一到 TaskManager

**Files:**
- Modify: apps/device-mac/dongdong/web_runtime.py
- Modify: apps/device-mac/dongdong/tools.py
- Modify: apps/device-mac/dongdong/web_server.py
- Test: tests/smoke.py

**Interfaces:**
- WebState.start 调用 TaskManager.submit 并保持返回 run id。
- WebAgent.result/stop/snapshot 读取 TaskManager 投影。
- 新增 self.task.status、self.task.stop、self.task.resume；旧 self.code.* 转发到同一 manager。
- GET /api/runs/{rid} 返回 goals/items/attempts 投影；SSE 继续读取同一 events。

- [ ] Step 1: 写兼容 smoke：工具列表含三个 self.task.*；旧 code result/stop 读取统一 Run；HTTP 创建消息后返回 run id，查询 run 包含 items。
- [ ] Step 2: 运行 smoke 确认新工具或投影缺失。
- [ ] Step 3: WebState 持有 TaskManager，启动恢复 expired lease；旧 _task/_run_id 仅兼容映射；关闭 manager。
- [ ] Step 4: WebAgent 和工具注册调用 manager submit/status/stop/resume，旧 code 文案和返回格式保持可读。
- [ ] Step 5: 扩展 Web API 投影，终态识别包含 done/failed/timeout/canceled/stopped。
- [ ] Step 6: 运行 ./scripts/check.sh。
- [ ] Step 7: git add 相关文件并提交 feat: expose unified task status to web and linx。

### Task 4: 暂停恢复、超时收口与跨进程恢复

**Files:**
- Modify: apps/device-mac/dongdong/task_scheduler.py
- Modify: apps/device-mac/dongdong/store.py
- Modify: apps/device-mac/dongdong/task_model.py
- Test: tests/smoke.py

- [ ] Step 1: 写 smoke：paused 不 claim；resume 后可 claim；deadline 使 Run timeout；重建 Store 后过期 running Item 回 open；取消后新 Item 不再领取。
- [ ] Step 2: 运行 smoke 确认 pause/recover/timeout 缺失。
- [ ] Step 3: 实现状态转移与 monitor_once；Goal 达成优先于 timeout；lease 过期记录恢复事件；连续无 frontier 的 Run 标 failed。
- [ ] Step 4: 增加 task_scheduler 配置节，钳制 worker、lease、poll、retry、drain 安全范围。
- [ ] Step 5: 运行 ./scripts/check.sh。
- [ ] Step 6: git add 相关文件并提交 feat: recover pause and settle task runs。

### Task 5: 运行记录展示、文档与设计索引

**Files:**
- Modify: apps/web/src/api.js
- Modify: apps/web/src/Conversation.jsx
- Modify: apps/web/src/style.css
- Modify: docs/architecture.md
- Modify: docs/README.md
- Modify: docs/decisions.md
- Test: tests/smoke.py

- [ ] Step 1: 写 API smoke：GET /api/runs/{rid}?include_events=true 含 item/attempt 字段且旧 event 可读。
- [ ] Step 2: 运行 smoke 确认投影缺字段。
- [x] Step 3: 复用会话运行记录显示 DAG Item、状态、尝试次数、错误摘要；MCP 管理页继续只显示外部服务。
- [x] Step 4: 更新架构、设计索引和任务状态说明，记录 SQLite WAL/CAS、workspace 串行规则及不采用外部队列原因。
- [x] Step 5: 运行 ./scripts/check.sh 和 cd apps/web && npm run build。
- [ ] Step 6: git add 相关文件并提交 feat: show task DAG in run history。

## Final Verification

- [ ] git pull --rebase，并解决非破坏性冲突。
- [ ] ./scripts/check.sh。
- [ ] cd apps/web && npm run build。
- [ ] git diff --check 和 git status --short。
- [ ] git push。

