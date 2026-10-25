# Automatic Task Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 让 Web/灵矽任务在首次提交时由编程模型生成经过校验的多 Item DAG；规划失败安全回退为单 Item，并复用现有 SQLite 调度器执行与展示。

**Architecture:** 保留现有 TaskManager、TaskStore、TaskExecutor 和 Runtime。新增无工具的结构化 Planner，读取目标和工作区生成最多 8 个 ItemSpec；TaskManager 在规划阶段记录事件、事务性落 Item，然后切换到现有 frontier/claim/lease/worker 循环。显式传入 items 的调用继续绕过模型规划，兼容现有测试和会议入口。

**Tech Stack:** Python 3.12、stdlib asyncio/json/pathlib/threading、现有 httpx OpenAI 兼容流式客户端、SQLite、FastAPI/SSE。

**Spec:** docs/superpowers/specs/2026-10-25-task-scheduler-design.md

## Global Constraints

- 遵守 AGENTS.md：自有文件单文件不超过 300 行；调度边界只放 tests/task_smoke.py；提交前运行 ./scripts/check.sh。
- 不引入 Redis、RabbitMQ、队列服务或新的模型 SDK；Planner 复用 llm.stream_chat。
- Planner 只能返回结构化计划，不能调用工具、写 SQLite、执行 shell 或访问用户记忆。
- Item 数量上限为 8；空指令、重复 ID、缺失依赖、依赖环、越界 cwd 和未知 agent 必须回退单 Item。
- 规划失败不得将 Run 标记为 done；必须记录 planning_error，然后继续执行单 Item。
- 不修改用户真实配置、数据库或未跟踪故事模式文件；临时验证放 ~/tmp。

## Review Focus

- 非 JSON、截断 JSON 或 Markdown 围栏必须回退单 Item并保留错误事件。
- 缺失依赖、依赖环、越界 cwd、未知 agent 必须整批拒绝。
- 规划期间查询/停止必须可见且不能被 worker 提前 claim。
- 规划完成后独立 Item 并行、依赖 Item 等待前置完成，历史 SSE/API 能读取规划事件。

### Task 1: Structured Planner and Plan Validation

**Files:**
- Create: apps/device-mac/dongdong/task_planner.py
- Modify: apps/device-mac/dongdong/task_model.py only if a shared result type is needed
- Test: tests/task_smoke.py

**Interfaces:**
- TaskPlanner(model, cfg, stream_fn=stream_chat, max_items=8)
- TaskPlanner.plan(text: str, cwd: str) -> PlanResult
- PlanResult.items: list[ItemSpec], error: str, used_fallback: bool
- Planner calls stream_fn(model, messages, tools=[]) and never exposes Runtime tools.

- [ ] Add smoke cases for valid two-item JSON, fenced JSON, invalid JSON fallback, missing dependency/cycle, out-of-workspace cwd, unknown agent, and empty tools.
- [ ] Implement bounded JSON extraction, schema/type checks, ID and DAG validation, workspace/agent validation, and single Item fallback.
- [ ] Run focused task smoke and ./scripts/check.sh.
- [ ] Commit feat: add structured task planner.

### Task 2: Planning State and Scheduler Integration

**Files:**
- Modify: apps/device-mac/dongdong/task_scheduler.py
- Modify: apps/device-mac/dongdong/task_store.py
- Modify: apps/device-mac/dongdong/store.py or schema.sql only for required status/event helper
- Test: tests/task_smoke.py

**Interfaces:**
- TaskManager.submit(..., items=None) creates planning state for automatic runs; explicit items keeps the current immediate path.
- TaskManager._plan_run(run_id, text, cwd) records planning_started, calls TaskPlanner, inserts one complete Item batch, records planning_done or planning_error, then sets running.
- TaskStore.set_task_status(run_id, status, error='') updates status and writes an event atomically.

- [ ] Add smoke cases for planning lifecycle, multiple Items, fallback, and parallel frontier with dependent Item waiting.
- [ ] Run task smoke to observe missing planning state.
- [ ] Integrate one planner call per automatic Run in the existing background thread; check stop before and after planning.
- [ ] Preserve explicit item submissions, retries, lease renewal, cancellation drain, and self.task projections.
- [ ] Run ./scripts/check.sh and commit feat: plan task runs before scheduling.

### Task 3: Frontend Progress and Documentation

**Files:**
- Modify: apps/web/src/Conversation.jsx, apps/web/src/api.js, apps/web/src/style.css
- Modify: README.md, docs/architecture.md, docs/README.md
- Modify: docs/superpowers/plans/2026-10-25-task-scheduler.md
- Test: tests/task_smoke.py projection assertion; existing frontend build

**Interfaces:**
- Conversation renders planning as 正在规划任务 and shows planned Item count when available.
- Existing collapsed execution card remains compatible with all current terminal states.
- Documentation distinguishes delivered SQLite scheduling from pending Goal evidence, repair planning, file-range conflicts, and multi-machine scheduling.

- [ ] Add serialized planning event and Item projection assertion.
- [ ] Update UI status map/card without a new state store or polling loop.
- [ ] Update README, architecture navigation/status, and task plan checkboxes.
- [ ] Run ./scripts/check.sh, npm run build --prefix apps/web, git diff --check.
- [ ] Commit docs: show automatic task planning status.

### Task 4: Whole-Branch Verification

**Files:** Review Tasks 1–3; do not touch story-mode files or design/.

- [ ] Inspect changed files, 300-line limit, and accidental unrelated changes.
- [ ] Run ./scripts/check.sh and npm run build --prefix apps/web.
- [ ] Run an isolated local model fixture for two-item planning, concurrency, dependency order, malformed JSON fallback, and SSE history.
- [ ] Update .claude/cc10x/v10/activeContext.md and progress.md with decisions and evidence.
- [ ] Commit integration fixes. Push only after explicit user request.

## Execution Order

- Wave 1: Task 1 and Task 3 may proceed in parallel.
- Wave 2: Task 2 integrates Task 1; Task 4 verifies the complete branch.

## Rulings

- Planner runs in the existing TaskManager background thread so Web/MCP submission remains responsive.
- Planner failure falls back to one Item and records the original error.
- Goal evidence and file-range conflict metadata remain later phases.
