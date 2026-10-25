"""SQLite 驱动的任务调度器：poll frontier、claim lease、执行并收口。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from . import config, work_gate
from .store import Store
from .task_lifecycle import close as close_manager
from .task_lifecycle import monitor, recover
from .task_model import AttemptResult, ItemSpec, RunSpec
from .task_plan_runtime import plan_run
from .task_planner import TaskPlanner
from .task_runtime import TaskExecutor


class TaskManager:
    def __init__(
        self,
        store: Store,
        *,
        cfg: dict | None = None,
        model=None,
        runner=None,
        approve=None,
        max_workers: int | None = None,
        poll_seconds: float | None = None,
        lease_seconds: int | None = None,
        verifier=None,
    ):
        self.store = store
        self.cfg = cfg or config.load()
        self.model = model
        self.runner = runner
        scheduler_cfg = self.cfg.get("task_scheduler") or {}
        worker_limit = (
            max_workers if max_workers is not None else scheduler_cfg.get("max_workers", 1)
        )
        self.max_workers = max(1, min(int(worker_limit), 32))
        poll_limit = (
            poll_seconds if poll_seconds is not None else scheduler_cfg.get("poll_seconds", 0.1)
        )
        self.poll_seconds = max(0.01, min(float(poll_limit), 60.0))
        lease_limit = (
            lease_seconds if lease_seconds is not None else scheduler_cfg.get("lease_seconds", 60)
        )
        self.lease_seconds = max(1, min(int(lease_limit), 3600))
        drain_limit = scheduler_cfg.get("drain_seconds", 2)
        self.drain_seconds = max(0.0, min(float(drain_limit), 30.0))
        auto_recover = scheduler_cfg.get("auto_recover", False)
        # 只接受 bool；字符串 "true" 不得意外打开跨进程自动续跑。
        self.auto_recover = auto_recover if isinstance(auto_recover, bool) else False
        final_verifier = scheduler_cfg.get("model_final_verifier", False)
        self.model_final_verifier = final_verifier if isinstance(final_verifier, bool) else False
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="dongdong-task"
        )
        self.task_executor = TaskExecutor(self.cfg, store, model, runner, approve)
        self.verifier = verifier
        self.planner = TaskPlanner(self.model, self.cfg)
        self._runs: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._planning: dict[str, tuple[str, str]] = {}
        self._closed = False
        self._recovery_lock = threading.Lock()
        self._recovery_done = False
        self._last_recovered_runs: list[str] = []
        self.on_finished = None

    def submit(
        self,
        session_id: str,
        text: str,
        cwd: str | None = None,
        channel: str = "web",
        items: list[ItemSpec] | None = None,
        acceptance=None,
    ) -> str:
        if self._closed:
            raise RuntimeError("调度器已关闭")
        session = self.store.get_session(session_id)
        if not session or session.get("archived_at"):
            raise ValueError("会话不存在或已归档，请先恢复")
        if work_gate.current(session_id):
            raise RuntimeError("本对话任务还在跑")
        workspace = cwd or self.cfg.get("workspace", "")
        user_id = self.store.append_message(session_id, "user", text)
        run_id = self.store.create_task_run(
            session_id,
            RunSpec(
                goal_text=text,
                channel=channel,
                workspace=workspace,
                max_workers=self.max_workers,
                acceptance=acceptance,
            ),
        )
        with self.store._lock, self.store._conn:
            row = self.store._conn.execute(
                "SELECT id, data FROM events WHERE run_id=? AND type='run_start' ORDER BY id LIMIT 1",
                (run_id,),
            ).fetchone()
            if row:
                data = json.loads(row[1] or "{}")
                data["user_message_id"] = user_id
                self.store._conn.execute(
                    "UPDATE events SET data=? WHERE id=?",
                    (json.dumps(data, ensure_ascii=False), row[0]),
                )
        if items is None:
            self.store.set_task_status(run_id, "planning", event_type="planning_queued")
            self._planning[run_id] = (text, workspace)
        else:
            specs = items or [
                ItemSpec(id=f"{run_id}-0", instruction=text, cwd=workspace, agent="builtin")
            ]
            self.store.add_items(run_id, specs)
        self._launch(run_id)
        return run_id

    def _launch(self, run_id: str) -> None:
        view = self.status(run_id)
        session_id = view["run"]["session_id"]
        if work_gate.current(session_id):
            raise RuntimeError("本对话任务还在跑")
        owns_gate = all(item["agent"] == "builtin" for item in view["items"])
        if owns_gate and not work_gate.acquire(run_id, session_id):
            raise RuntimeError("另一个任务还在跑")
        cancel = threading.Event()
        self._runs[run_id] = cancel
        thread = threading.Thread(
            target=self._loop, args=(run_id, cancel, owns_gate), name=f"run-{run_id}", daemon=True
        )
        self._threads[run_id] = thread
        thread.start()

    def _loop(self, run_id: str, cancel: threading.Event, owns_gate: bool) -> None:
        futures = {}
        try:
            planning = self._planning.pop(run_id, None)
            if planning:
                plan_run(self, run_id, *planning, cancel)
            while not cancel.is_set() and not self._closed:
                view = self.monitor_once(run_id)
                if not view or view["run"]["status"] != "running":
                    break
                active = sum(item["status"] == "running" for item in view["items"])
                for item in view["items"]:
                    if active >= self.max_workers:
                        break
                    if item["status"] != "open":
                        continue
                    worker = f"{run_id[:6]}-{uuid.uuid4().hex[:6]}"
                    if self.store.claim_item(
                        run_id, item["id"], worker, lease_seconds=self.lease_seconds
                    ):
                        future = self.executor.submit(
                            self._execute, run_id, worker, item["id"], cancel
                        )
                        futures[future] = worker
                        active += 1
                futures = {
                    future: worker for future, worker in futures.items() if not future.done()
                }
                # 活跃 worker 续租；不能因模型等待超过 60 秒便重复执行命令。
                with self.store._lock, self.store._conn:
                    lease_until = datetime.fromtimestamp(
                        time.time() + self.lease_seconds, UTC
                    ).isoformat(timespec="seconds")
                    for worker in futures.values():
                        self.store._conn.execute(
                            "UPDATE items SET lease_until=? WHERE run_id=? AND lease_owner=? "
                            "AND status='running'",
                            (lease_until, run_id, worker),
                        )
                view = self.store.task_view(run_id)
                statuses = {item["status"] for item in view["items"]}
                if statuses and self._items_resolved(view["items"]):
                    self._finish(run_id, "done")
                    break
                if (
                    statuses
                    and not statuses & {"open", "running"}
                    and not self._items_resolved(view["items"])
                ):
                    self._finish(run_id, "failed", "没有可恢复的 Item")
                    break
                time.sleep(self.poll_seconds)
        finally:
            try:
                deadline = time.monotonic() + self.drain_seconds
                for future in futures:
                    remaining = max(0.0, deadline - time.monotonic())
                    try:
                        future.result(timeout=remaining)
                    except Exception:  # noqa: BLE001 worker 收口不应阻塞 Run 终态
                        pass
                self._finalize_stop(run_id)
            finally:
                if owns_gate:
                    work_gate.release(run_id)
                if self.on_finished:
                    self.on_finished(run_id)

    @staticmethod
    def _items_resolved(items: list[dict]) -> bool:
        """失败 Item 只有在其 repair 子 Item 完成后才算收口。"""
        by_parent: dict[str, list[dict]] = {}
        for item in items:
            if item.get("parent_id"):
                by_parent.setdefault(item["parent_id"], []).append(item)

        def resolved(item: dict) -> bool:
            if item["status"] == "done":
                return True
            if item["status"] in {"blocked", "exhausted", "failed", "error"}:
                return any(child["status"] == "done" for child in by_parent.get(item["id"], []))
            return False

        return bool(items) and all(resolved(item) for item in items)

    def _execute(self, run_id: str, worker: str, item_id: str, cancel: threading.Event) -> None:
        view = self.store.task_view(run_id)
        item = next(item for item in view["items"] if item["id"] == item_id)
        try:
            result = self.task_executor.execute(item, view["run"], cancel)
        except Exception as exc:
            result = AttemptResult("blocked", error=str(exc), error_kind="blocked")
        if cancel.is_set() or (self.store.get_run(run_id) or {}).get("status") != "running":
            result = AttemptResult("stopped", error="Run 已停止")
        if result.status == "done":
            self.store.complete_item(item_id, worker, result.output)
        elif result.status == "stopped":
            self.store.fail_item(item_id, worker, result.error or "已停止", error_kind="blocked")
        else:
            self.store.fail_item(
                item_id,
                worker,
                result.error,
                error_kind=result.error_kind or result.status.removesuffix("_error"),
            )

    def _finish(self, run_id: str, status: str, error: str = "") -> None:
        if status == "done" and not self.store.begin_settle(run_id):
            return
        if status == "done":
            self.store.add_event(run_id, "final_verification_started", {})
            view = self.store.task_view(run_id)
            verifier = self.verifier
            if verifier is None and self.model_final_verifier and self.model is not None:
                verifier = self.planner.verify_goal
            verification = self.store.verify_goal(run_id, verifier)
            self.store.add_event(
                run_id,
                "final_verification_done",
                {
                    "status": verification["status"],
                    "reason": verification.get("reason", ""),
                },
            )
            if verification["status"] != "met":
                reason = verification.get("reason") or "Goal 未通过验收"
                self.store.set_task_status(
                    run_id,
                    "needs_review" if verification["status"] == "needs_review" else "failed",
                    error=reason,
                    event_type="run_finished",
                    data={"goal_status": verification["status"]},
                )
                return
            outputs = [item["result"] for item in view["items"] if item.get("result")]
            if outputs:
                summary = "任务完成：\n" + "\n\n".join(outputs)
                self.store.append_message(view["run"]["session_id"], "assistant", summary)
                self.store.add_event(run_id, "delivery", {"text": summary})
        self.store.set_task_status(run_id, status, error=error, event_type="run_finished")

    def wait(self, run_id: str, timeout: float = 10) -> dict:
        thread = self._threads.get(run_id)
        if thread:
            thread.join(timeout=max(0, timeout))
        return self.status(run_id)

    def status(self, run_id: str) -> dict:
        return self.store.task_view(run_id) or {
            "run": None,
            "goals": [],
            "items": [],
            "attempts": [],
            "events": [],
        }

    def stop(self, run_id: str) -> dict:
        if not self._request_stop(run_id, "canceled", "用户停止"):
            return self.status(run_id)
        thread = self._threads.get(run_id)
        if thread and thread is not threading.current_thread():
            thread.join(timeout=self.drain_seconds)
        self._finalize_stop(run_id)
        return self.status(run_id)

    def _request_stop(self, run_id: str, status: str, error: str) -> bool:
        cancel = self._runs.get(run_id)
        if cancel:
            cancel.set()
        with self.store._lock, self.store._conn:
            changed = self.store._conn.execute(
                "UPDATE runs SET status='stopping', error=? "
                "WHERE id=? AND status IN ('running','planning','paused')",
                (error[:4000], run_id),
            ).rowcount
            if changed:
                self.store._conn.execute(
                    "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                    (
                        run_id,
                        "run_stopping",
                        json.dumps({"status": status, "error": error}, ensure_ascii=False),
                        datetime.now(UTC).isoformat(timespec="seconds"),
                    ),
                )
            return changed == 1

    def _finalize_stop(self, run_id: str) -> None:
        with self.store._lock, self.store._conn:
            row = self.store._conn.execute(
                "SELECT status, error FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if not row or row[0] != "stopping":
                return
            error = row[1] or "任务已停止"
            target = "timeout" if error == "超过任务截止时间" else "canceled"
            stamp = datetime.now(UTC).isoformat(timespec="seconds")
            self.store._conn.execute(
                "UPDATE items SET status='stopped', lease_owner=NULL, lease_until=NULL "
                "WHERE run_id=? AND status IN ('open','running')",
                (run_id,),
            )
            self.store._conn.execute(
                "UPDATE attempts SET status='stopped', ended_at=?, error_kind=?, error=? "
                "WHERE item_id IN (SELECT id FROM items WHERE run_id=?) AND status='running'",
                (stamp, target, error[:4000], run_id),
            )
            self.store._conn.execute(
                "UPDATE runs SET status=?, ended_at=? WHERE id=? AND status='stopping'",
                (target, stamp, run_id),
            )
            self.store._conn.execute(
                "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                (
                    run_id,
                    "run_finished",
                    json.dumps({"status": target, "error": error}, ensure_ascii=False),
                    stamp,
                ),
            )

    def resume(self, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if not run or run["status"] not in {"paused", "failed", "needs_review", "canceled"}:
            return self.status(run_id)
        session = self.store.get_session(run["session_id"])
        if not session or session.get("archived_at"):
            raise ValueError("会话已归档，请先恢复")
        if work_gate.current(run["session_id"]) or (
            self._threads.get(run_id) and self._threads[run_id].is_alive()
        ):
            raise RuntimeError("另一个任务还在跑")
        project = (
            self.store.get_project(session.get("project_id")) if session.get("project_id") else None
        )
        root = Path(project["path"] if project else self.cfg["workspace"]).expanduser().resolve()
        paths = [run.get("workspace") or str(root)] + [
            item["cwd"] or str(root) for item in self.status(run_id)["items"]
        ]
        if any(
            not Path(path).is_dir() or not Path(path).resolve().is_relative_to(root)
            for path in paths
        ):
            raise ValueError("原任务目录不属于会话当前项目，请新建任务")
        with self.store._lock, self.store._conn:
            row = self.store._conn.execute(
                "SELECT status FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if not row or row[0] not in {"paused", "failed", "needs_review", "canceled"}:
                return self.status(run_id)
            self.store._conn.execute(
                "UPDATE runs SET status='running', error='' WHERE id=?", (run_id,)
            )
            # blocked 表示依赖或 repair 失败，交给 repair 流程解阻断；resume 只恢复被收口的 stopped。
            self.store._conn.execute(
                "UPDATE items SET status='open' WHERE run_id=? AND status='stopped'",
                (run_id,),
            )
        if not self.status(run_id)["items"]:
            self._planning[run_id] = (run.get("goal_text", ""), run.get("workspace", ""))
            self.store.set_task_status(run_id, "planning", event_type="planning_queued")
        self._launch(run_id)
        return self.status(run_id)

    def repair(
        self, run_id: str, item_id: str | None = None, instruction: str | None = None
    ) -> dict:
        """显式重规划失败 Item，并在完成后重新验收 Goal。"""
        run = self.store.get_run(run_id)
        if not run or run["status"] not in {"failed", "needs_review"}:
            raise ValueError("只有失败或待验收 Run 可以修复")
        view = self.status(run_id)
        failed = [
            item
            for item in view["items"]
            if item["status"] in {"blocked", "exhausted", "failed", "error"}
        ]
        target = next((item for item in failed if item["id"] == item_id), None) if item_id else None
        if item_id and target is None:
            raise ValueError("指定 Item 不是失败状态或不属于该 Run")
        target = target or next((item for item in failed if not item.get("parent_id")), None)
        if target is None:
            raise ValueError("没有可修复的失败 Item")
        session = self.store.get_session(run["session_id"])
        project = self.store.get_project(session.get("project_id")) if session else None
        root = Path(project["path"] if project else self.cfg["workspace"]).expanduser().resolve()
        paths = [run.get("workspace") or str(root), target.get("cwd") or str(root)]
        if any(
            not Path(path).is_dir() or not Path(path).resolve().is_relative_to(root)
            for path in paths
        ):
            raise ValueError("原任务目录不属于会话当前项目")
        repair_source = "user"
        if not (instruction or "").strip() and self.model is not None:
            try:
                instruction, repair_source = self.planner.repair_instruction(target, run)
                self.store.add_event(
                    run_id,
                    "repair_planning_done",
                    {"failed_item_id": target["id"], "source": repair_source},
                )
            except Exception as exc:  # noqa: BLE001 模型失败必须回退安全默认指令
                repair_source = "fallback"
                self.store.add_event(
                    run_id,
                    "repair_planning_fallback",
                    {"failed_item_id": target["id"], "reason": str(exc)[:4000]},
                )
        self.store.add_event(
            run_id,
            "repair_requested",
            {
                "failed_item_id": target["id"],
                "instruction": (instruction or "").strip(),
                "source": repair_source,
            },
        )
        self.store.add_repair_item(run_id, target["id"], instruction)
        with self.store._lock, self.store._conn:
            changed = self.store._conn.execute(
                "UPDATE runs SET status='running', error='', ended_at=NULL WHERE id=? AND status IN ('failed','needs_review')",
                (run_id,),
            ).rowcount
            if changed != 1:
                raise ValueError("Run 状态已变化，请刷新后重试")
        self._launch(run_id)
        return self.status(run_id)

    def pause(self, run_id: str) -> dict:
        with self.store._lock, self.store._conn:
            changed = self.store._conn.execute(
                "UPDATE runs SET status='paused' WHERE id=? AND status IN ('running','planning')",
                (run_id,),
            ).rowcount
            if changed:
                stamp = datetime.now(UTC).isoformat(timespec="seconds")
                # 暂停也必须释放租约，否则进程重启前会留下永远 running 的 Item。
                self.store._conn.execute(
                    "UPDATE items SET status='stopped', lease_owner=NULL, lease_until=NULL, "
                    "updated_at=? WHERE run_id=? AND status IN ('open','running')",
                    (stamp, run_id),
                )
                self.store._conn.execute(
                    "UPDATE attempts SET status='stopped', ended_at=?, error_kind='paused', "
                    "error='任务已暂停' WHERE item_id IN "
                    "(SELECT id FROM items WHERE run_id=?) AND status='running'",
                    (stamp, run_id),
                )
                self.store._conn.execute(
                    "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                    (run_id, "run_paused", "{}", stamp),
                )
        event = self._runs.get(run_id)
        if event:
            event.set()
        return self.status(run_id)

    def monitor_once(self, run_id: str) -> dict:
        return monitor(self, run_id)

    def recover_runs(self, *, auto_recover: bool | None = None) -> int:
        # activate 可能被生命周期重复调用；同一 manager 只允许执行一次恢复。
        with self._recovery_lock:
            if self._recovery_done:
                return 0
            self._recovery_done = True
            count = recover(self)
            run_ids = list(getattr(self, "_last_recovered_runs", []))
        enabled = (
            self.auto_recover
            if auto_recover is None
            else (auto_recover if isinstance(auto_recover, bool) else False)
        )
        if enabled:
            for run_id in run_ids:
                self.store.add_event(
                    run_id,
                    "auto_recover_started",
                    {
                        "message": "单主机 SQLite 可选自动恢复；旧进程仍存活时不保证避免双主风险",
                    },
                )
                try:
                    self.resume(run_id)
                except Exception as exc:  # noqa: BLE001 自动恢复失败必须保持可确认的暂停态
                    run = self.store.get_run(run_id)
                    if run and run.get("status") in {"running", "planning"}:
                        self.pause(run_id)
                    self.store.add_event(
                        run_id,
                        "auto_recover_failed",
                        {"error": str(exc)[:4000], "status": "paused"},
                    )
        return count

    def close(self) -> None:
        close_manager(self)
