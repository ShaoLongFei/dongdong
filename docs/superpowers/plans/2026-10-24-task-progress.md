# Task Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the steps supplied by Linx and their live execution progress when Dongdong runs a code task.

**Architecture:** Linx remains responsible for planning and passes an optional `steps` array to `self.code.run`. The existing agent module owns one in-memory task, runs its steps sequentially as separate CLI processes, and exposes a thread-safe snapshot to the existing MCP tools and panel. Old callers without `steps` run their instruction as one step.

**Tech Stack:** Python 3.12, stdlib subprocess/threading, existing MCP registry, existing local HTTP panel.

**Spec:** `docs/superpowers/specs/2026-10-24-task-progress-design.md`

## Global Constraints

- Keep the current flow: voice input -> Linx -> local MCP -> code agent.
- Allow one active task and run steps sequentially in the same allowed working directory.
- Keep task state in memory; process restart clears it.
- Do not add dependencies, persistence, parallel workers, retries, or a planner call from Dongdong.
- Keep tests in `tests/smoke.py`; validate with `./scripts/check.sh`.
- Keep each new or changed source file at or below 300 lines.

## Review Focus

- A malformed or oversized `steps` array must be rejected before a child process starts; cover empty list, blank member, and more than 8 steps in `tests/smoke.py`.
- A nonzero CLI exit or launch error must block the current step and leave later steps pending or stopped without launching them; cover with a temporary Python child command.
- A stop arriving during a step must prevent the next step from starting; cover with a child command that waits on a marker or a short bounded sleep.
- Concurrent MCP and panel reads must see a consistent snapshot; use a lock around task and step state and exercise snapshot reads during a fake run.
- Legacy `self.code.run` without `steps` must still start a single step and keep directory and environment validation; cover both through the existing registry boundary.

---

### Task 1: Track and execute a sequential task

**Files:**
- Modify: `apps/device-mac/dongdong/agent.py`
- Test: `tests/smoke.py`

**Interfaces:**
- `start_task(cfg: dict, instruction: str, steps: list[str] | None = None, name: str | None = None, cwd: str | None = None) -> str` validates inputs and returns the task ID immediately.
- `snapshot() -> dict | None` adds task state, goal, `current_step`, completed/total counts, per-step state and summary, elapsed time, and recent output while retaining existing `id`, `agent`, `cwd`, `running`, `exit_code`, and `output` keys.
- `result(job_id: str = "") -> str` renders the same task and step state for MCP callers.
- `stop(job_id: str = "") -> str` marks the current task stopped, kills its active child process, and prevents future steps from starting.

- [ ] Add smoke assertions for step validation, single-step fallback, successful sequential progress, nonzero exit stopping the sequence, and stop preventing later steps.
- [ ] Implement one in-memory task record guarded by a lock; cap explicit steps at 8 and reject invalid content before spawning.
- [ ] Run each step as a separate CLI subprocess in order, passing goal, ordinal, and step text as one prompt argument; update status and summary after each child exits.
- [ ] Keep `run()` as the existing synchronous compatibility entry point, delegating task startup and waiting up to `wait_seconds`.
- [ ] Run `.venv/bin/python tests/smoke.py` and confirm existing agent tests still pass.

### Task 2: Pass the plan through MCP and expose status

**Files:**
- Modify: `apps/device-mac/dongdong/tools.py`
- Test: `tests/smoke.py`

**Interfaces:**
- `self.code.run` accepts optional `steps: array[string]`; its text result immediately contains the task ID and step count.
- Existing `self.code.result` returns per-step progress for the specified or latest task.
- Existing `self.code.stop` stops the task and reports its ID.

- [ ] Extend the `self.code.run` schema and description to tell Linx to pass a short ordered `steps` array, while preserving old arguments.
- [ ] Route `_code_run` to `agent.start_task`; keep the current preflight checks for configured agent and required environment variables.
- [ ] Add smoke assertions that tools/list exposes `steps` and a registered run call accepts a one-step legacy request using a harmless temporary command.
- [ ] Run `.venv/bin/python tests/smoke.py`.

### Task 3: Show step progress in the panel and document MCP behavior

**Files:**
- Modify: `apps/device-mac/dongdong/panel.html`
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`

**Interfaces:**
- Existing `GET /state` continues to return `job`; its expanded snapshot carries task progress.
- Existing `POST /task` remains compatible and sends one instruction, which the agent treats as a single step.

- [ ] Render overall progress, current step, and an ordered list of step statuses and summaries using text nodes, keeping output bounded to the existing tail.
- [ ] Show the returned task ID in the panel dispatch notice without changing endpoint shapes.
- [ ] Document how Linx supplies `steps`, how to query `self.code.result`, and that progress is not pushed after `self.code.run` returns.
- [ ] Run `./scripts/check.sh` and inspect the panel at desktop and narrow viewport widths; verify no new dependency or file above 300 lines.

## Handoff Checks

- `self.code.run` receives at most 8 non-empty steps and returns before the sequence completes.
- The panel and `self.code.result` report the same task ID, current step, step states, and terminal state.
- A failing or stopped step never causes a later step to launch.
- Calls without `steps` retain one-step behavior and existing workspace/environment guards.
