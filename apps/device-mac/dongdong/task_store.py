"""SQLite 的任务 DAG、领取与尝试记录；与会话复用同一事务锁。"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .task_goal import GoalVerification
from .task_model import ItemSpec, RunSpec, acceptance_value, normalize_scope
from .task_projection import TaskProjection


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TaskStore(GoalVerification, TaskProjection):
    MAX_REPAIR_ITEMS_PER_RUN = 8
    MAX_REPAIRS_PER_ITEM = 3

    def begin_settle(self, run_id: str) -> bool:
        """原子地占用运行收口，避免 stop/timeout/worker 重复收口。"""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE runs SET status='verifying' WHERE id=? AND status='running'", (run_id,)
            )
            return cur.rowcount == 1

    def set_goal_acceptance(self, run_id: str, criteria: list[dict]) -> bool:
        with self._lock, self._conn:
            self._ensure_goal_columns()
            cur = self._conn.execute(
                "UPDATE goals SET acceptance=? WHERE run_id=? AND COALESCE(acceptance,'')=''",
                (json.dumps(criteria, ensure_ascii=False), run_id),
            )
            return cur.rowcount == 1

    def set_task_status(
        self, run_id: str, status: str, error: str = "", event_type: str = "run_status", data=None
    ) -> bool:
        """在同一事务中更新 Run 快照并记录状态事件。"""
        ts = now()
        payload = {"status": status}
        if error:
            payload["error"] = error[:4000]
        if data:
            payload.update(data)
        with self._lock, self._conn:
            row = self._conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return False
            current = row[0]
            if status == "done" and current != "verifying":
                return False
            if status in {"timeout", "canceled", "stopped"} and current not in {
                "running",
                "planning",
                "paused",
            }:
                return False
            if status in {"failed", "needs_review"} and current not in {
                "running",
                "verifying",
            }:
                return False
            if current in {
                "done",
                "failed",
                "error",
                "timeout",
                "canceled",
                "stopped",
                "needs_review",
            }:
                return False
            ended = (
                ts
                if status
                in {
                    "done",
                    "failed",
                    "error",
                    "timeout",
                    "canceled",
                    "stopped",
                    "needs_review",
                }
                else None
            )
            self._conn.execute(
                "UPDATE runs SET status=?, error=?, ended_at=COALESCE(?, ended_at) "
                "WHERE id=? AND status NOT IN ('done','failed','error','timeout','canceled','stopped')",
                (status, error[:4000], ended, run_id),
            )
            if self._conn.execute("SELECT changes()").fetchone()[0] != 1:
                return False
            self._conn.execute(
                "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                (run_id, event_type, json.dumps(payload, ensure_ascii=False), ts),
            )
        return True

    def create_task_run(self, session_id: str, spec: RunSpec) -> str:
        rid, ts = uuid.uuid4().hex[:12], now()
        acceptance = acceptance_value(spec.acceptance)
        acceptance_text = json.dumps(acceptance, ensure_ascii=False) if acceptance != "" else ""
        workers = max(1, min(int(spec.max_workers), 32))
        with self._lock, self._conn:
            self._ensure_goal_columns()
            session = self._conn.execute(
                "SELECT archived_at FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None or session[0] is not None:
                raise ValueError("会话不存在或已归档，请先恢复")
            self._conn.execute(
                "INSERT INTO runs(id, session_id, status, goal_text, channel, workspace, max_workers,"
                " deadline_at, started_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    session_id,
                    "running",
                    spec.goal_text,
                    spec.channel,
                    spec.workspace,
                    workers,
                    spec.deadline_at,
                    ts,
                ),
            )
            gid = uuid.uuid4().hex[:12]
            self._conn.execute(
                "INSERT INTO goals(id, run_id, text, acceptance, created_at) VALUES (?,?,?,?,?)",
                (gid, rid, spec.goal_text, acceptance_text, ts),
            )
            self._conn.execute(
                "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                (rid, "run_start", json.dumps({"goal_id": gid}, ensure_ascii=False), ts),
            )
        return rid

    def add_items(self, run_id: str, specs: list[ItemSpec]) -> list[str]:
        if not specs:
            return []
        ids = {item.id or uuid.uuid4().hex[:12] for item in specs}
        if len(ids) != len(specs):
            raise ValueError("Item ID 重复")
        # 对自动 ID 重新建立稳定映射，调用方通常显式提供 ID；默认项按输入顺序生成。
        ordered = list(specs)
        resolved = []
        for index, item in enumerate(ordered):
            resolved.append((item, item.id or f"{run_id}-{index}"))
        ids = {item_id for _, item_id in resolved}
        edges = {item_id: set(item.depends_on) for item, item_id in resolved}
        with self._lock, self._conn:
            run = self._conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run or run[0] not in {"planning", "running"}:
                raise ValueError("任务不在规划或运行状态")
            existing = {
                row[0]
                for row in self._conn.execute("SELECT id FROM items WHERE run_id=?", (run_id,))
            }
            known = existing | ids
            if any(dep not in known for deps in edges.values() for dep in deps):
                raise ValueError("依赖 Item 不存在")
            all_edges = {node: set() for node in known}
            for row in self._conn.execute(
                "SELECT d.item_id, d.depends_on_id FROM item_deps d JOIN items i ON i.id=d.item_id WHERE i.run_id=?",
                (run_id,),
            ):
                all_edges[row[0]].add(row[1])
            for node, deps in edges.items():
                all_edges[node] = set(deps)
            self._assert_acyclic(all_edges)
            ts = now()
            goal = self._conn.execute(
                "SELECT id FROM goals WHERE run_id=? ORDER BY rowid LIMIT 1", (run_id,)
            ).fetchone()
            for item, item_id in resolved:
                scope = self._absolute_scope(item)
                self._conn.execute(
                    "INSERT INTO items(id, run_id, goal_id, parent_id, instruction, acceptance, agent, cwd,"
                    " priority, max_attempts, scope_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item_id,
                        run_id,
                        item.goal_id or (goal[0] if goal else None),
                        item.parent_id or None,
                        item.instruction.strip(),
                        item.acceptance,
                        item.agent,
                        item.cwd,
                        item.priority,
                        max(1, min(item.max_attempts, 5)),
                        json.dumps(scope, ensure_ascii=False),
                        ts,
                        ts,
                    ),
                )
                for dependency in item.depends_on:
                    self._conn.execute(
                        "INSERT INTO item_deps(item_id, depends_on_id) VALUES (?,?)",
                        (item_id, dependency),
                    )
            criteria = [
                {"item_id": item_id, "criteria": item.acceptance.strip()}
                for item, item_id in resolved
                if item.acceptance.strip()
            ]
            if criteria:
                goal = self._conn.execute(
                    "SELECT id, acceptance FROM goals WHERE run_id=? ORDER BY rowid LIMIT 1",
                    (run_id,),
                ).fetchone()
                if goal and not goal[1]:
                    self._conn.execute(
                        "UPDATE goals SET acceptance=? WHERE id=?",
                        (json.dumps(criteria, ensure_ascii=False), goal[0]),
                    )
            return [item_id for _, item_id in resolved]

    @staticmethod
    def _absolute_scope(item: ItemSpec) -> dict[str, list[str]]:
        scope = normalize_scope(item.scope)
        root = Path(item.cwd or ".").expanduser().resolve()
        result: dict[str, list[str]] = {}
        seen: set[str] = set()
        for mode, paths in scope.items():
            values = []
            for relative in paths:
                path = Path(relative)
                absolute = path if path.is_absolute() else root / relative
                absolute = absolute.resolve()
                if not (absolute == root or absolute.is_relative_to(root)):
                    raise ValueError("scope 路径必须位于 Item cwd 内")
                canonical = str(absolute)
                if canonical in seen:
                    raise ValueError("scope 存在重复或读写交叠")
                seen.add(canonical)
                values.append(canonical)
            if values:
                result[mode] = values
        return result

    def add_repair_item(
        self, run_id: str, failed_item_id: str, instruction: str | None = None
    ) -> str:
        """追加修复 Item；旧 Item 与其 attempts 永远保留。"""
        text = (instruction or "").strip()
        with self._lock, self._conn:
            run = self._conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            failed = self._conn.execute(
                "SELECT * FROM items WHERE id=? AND run_id=?", (failed_item_id, run_id)
            ).fetchone()
            if not run or not failed:
                raise ValueError("Run 或失败 Item 不存在")
            if run[0] not in {"failed", "needs_review"}:
                raise ValueError("只有失败或待验收 Run 可以修复")
            if failed["status"] not in {"blocked", "exhausted", "failed", "error"}:
                raise ValueError("只能修复已失败的 Item")
            total = self._conn.execute(
                "SELECT COUNT(*) FROM items WHERE run_id=? AND parent_id IS NOT NULL", (run_id,)
            ).fetchone()[0]
            count = self._conn.execute(
                "SELECT COUNT(*) FROM items WHERE run_id=? AND parent_id=?",
                (run_id, failed_item_id),
            ).fetchone()[0]
            if total >= self.MAX_REPAIR_ITEMS_PER_RUN or count >= self.MAX_REPAIRS_PER_ITEM:
                raise ValueError("修复 Item 数量已达上限")
            item_id = f"{run_id}-repair-{uuid.uuid4().hex[:8]}"
            ts = now()
            failure_summary = "\n失败结果摘要：" + (
                (failed["error"] or failed["result"] or "无")[-2000:]
            )
            repair_instruction = (
                text + failure_summary
                if text
                else f"修复失败 Item：{failed['instruction']}\n原错误：{failed['error'] or '无'}"
            )
            goal = self._conn.execute(
                "SELECT goal_id FROM items WHERE id=?", (failed_item_id,)
            ).fetchone()
            self._conn.execute(
                "INSERT INTO items(id, run_id, goal_id, parent_id, instruction, acceptance, agent, cwd,"
                " priority, max_attempts, scope_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    run_id,
                    goal[0] if goal else None,
                    failed_item_id,
                    repair_instruction,
                    failed["acceptance"],
                    failed["agent"],
                    failed["cwd"],
                    failed["priority"],
                    failed["max_attempts"],
                    failed["scope_json"] or "{}",
                    ts,
                    ts,
                ),
            )
            self._conn.execute(
                "INSERT INTO item_deps(item_id, depends_on_id) VALUES (?,?)",
                (item_id, failed_item_id),
            )
            self._conn.execute(
                "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                (
                    run_id,
                    "repair_planned",
                    json.dumps(
                        {
                            "item_id": item_id,
                            "failed_item_id": failed_item_id,
                            "agent": failed["agent"],
                            "cwd": failed["cwd"],
                        },
                        ensure_ascii=False,
                    ),
                    ts,
                ),
            )
            return item_id

    @staticmethod
    def _assert_acyclic(edges: dict[str, set[str]]) -> None:
        visiting, visited = set(), set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("Item 依赖存在环")
            if node in visited:
                return
            visiting.add(node)
            for dep in edges.get(node, ()):
                if dep in edges:
                    visit(dep)
            visiting.remove(node)
            visited.add(node)

        for node in edges:
            visit(node)

    def claim_item(
        self, run_id: str, item_id: str, worker_id: str, lease_seconds: int = 60
    ) -> bool:
        ts = datetime.now(UTC)
        expiry = (ts + timedelta(seconds=max(1, lease_seconds))).isoformat(timespec="seconds")
        stamp = ts.isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "UPDATE items SET status='running', lease_owner=?, lease_until=?, attempts=attempts+1,"
                    " updated_at=? WHERE id=? AND run_id=? AND status='open' AND EXISTS"
                    " (SELECT 1 FROM runs WHERE id=items.run_id AND status='running') AND NOT EXISTS"
                    " (SELECT 1 FROM item_deps d JOIN items p ON p.id=d.depends_on_id"
                    " WHERE d.item_id=items.id AND p.status!='done' AND NOT"
                    " (p.status!='done' AND items.parent_id=p.id AND"
                    " p.status IN ('blocked','exhausted','failed','error'))) ",
                    (worker_id, expiry, stamp, item_id, run_id),
                )
                if cur.rowcount != 1:
                    self._conn.rollback()
                    return False
                candidate = self._conn.execute(
                    "SELECT i.scope_json, r.workspace FROM items i JOIN runs r ON r.id=i.run_id "
                    "WHERE i.id=? AND i.run_id=?",
                    (item_id, run_id),
                ).fetchone()
                running = self._conn.execute(
                    "SELECT i.id, i.scope_json, r.workspace, r.id, i.cwd FROM items i "
                    "JOIN runs r ON r.id=i.run_id WHERE r.status='running' "
                    "AND i.status='running' AND i.id!=?",
                    (item_id,),
                ).fetchall()
                if candidate and any(
                    self._same_workspace(candidate[1], row[2])
                    and self._scope_conflicts(candidate[0], row[1])
                    for row in running
                ):
                    self._conn.execute(
                        "UPDATE items SET status='open', lease_owner=NULL, lease_until=NULL, "
                        "attempts=attempts-1, updated_at=? WHERE id=?",
                        (stamp, item_id),
                    )
                    self._conn.execute(
                        "UPDATE attempts SET status='stopped', ended_at=?, error_kind='scope_conflict',"
                        " error='范围与运行中 Item 冲突' WHERE item_id=? AND worker_id=? AND status='running'",
                        (stamp, item_id, worker_id),
                    )
                    recent = self._conn.execute(
                        "SELECT 1 FROM events WHERE run_id=? AND type='scope_conflict' "
                        "AND json_extract(data, '$.item_id')=? AND created_at>? LIMIT 1",
                        (
                            run_id,
                            item_id,
                            (ts - timedelta(seconds=2)).isoformat(timespec="seconds"),
                        ),
                    ).fetchone()
                    if not recent:
                        self._conn.execute(
                            "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                            (
                                run_id,
                                "scope_conflict",
                                json.dumps(
                                    {"item_id": item_id, "worker_id": worker_id}, ensure_ascii=False
                                ),
                                stamp,
                            ),
                        )
                    self._conn.commit()
                    return False
                self._conn.execute(
                    "INSERT INTO attempts(item_id, worker_id, status, started_at) VALUES (?,?,?,?)",
                    (item_id, worker_id, "running", stamp),
                )
                self._conn.execute(
                    "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                    (
                        run_id,
                        "item_claimed",
                        json.dumps({"item_id": item_id, "worker_id": worker_id}),
                        stamp,
                    ),
                )
                self._conn.commit()
                return True
            except Exception:
                self._conn.rollback()
                raise

    @staticmethod
    def _same_workspace(left: str, right: str) -> bool:
        """工作区相等或嵌套时视为可能共享文件，兼容旧空 scope 的 cwd 独占。"""
        if not left or not right:
            return bool(left or right) is False
        try:
            first = Path(left).expanduser().resolve()
            second = Path(right).expanduser().resolve()
            return first == second or first.is_relative_to(second) or second.is_relative_to(first)
        except (OSError, RuntimeError, ValueError):
            return left == right

    @staticmethod
    def _scope_conflicts(left_json: str, right_json: str) -> bool:
        """保守判断范围冲突；空、损坏或目录范围都按独占处理。"""
        try:
            left = json.loads(left_json or "{}")
            right = json.loads(right_json or "{}")
        except (TypeError, json.JSONDecodeError):
            return True
        if not isinstance(left, dict) or not isinstance(right, dict):
            return True
        if not left or not right:
            return True
        reads_left = set(left.get("read", []))
        writes_left = set(left.get("write", []))
        reads_right = set(right.get("read", []))
        writes_right = set(right.get("write", []))
        all_paths = (*reads_left, *writes_left, *reads_right, *writes_right)
        if not all(isinstance(path, str) and path for path in all_paths):
            return True

        def overlaps(paths_a: set[str], paths_b: set[str]) -> bool:
            return any(
                first == second or first.startswith(second + "/") or second.startswith(first + "/")
                for first in paths_a
                for second in paths_b
            )

        return overlaps(writes_left, writes_right | reads_right) or overlaps(
            reads_left, writes_right
        )

    def complete_item(self, item_id: str, worker_id: str, result: str = "") -> bool:
        return self._finish_item(item_id, worker_id, "done", result=result)

    def fail_item(
        self, item_id: str, worker_id: str, error: str, error_kind: str = "retryable"
    ) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT run_id, attempts, max_attempts FROM items WHERE id=?", (item_id,)
            ).fetchone()
            if not row:
                return False
            status = (
                "blocked"
                if error_kind == "blocked"
                else "exhausted"
                if row[1] >= row[2]
                else "open"
            )
        return self._finish_item(item_id, worker_id, status, error=error, error_kind=error_kind)

    def _finish_item(
        self,
        item_id: str,
        worker_id: str,
        status: str,
        result: str = "",
        error: str = "",
        error_kind: str = "",
    ) -> bool:
        ts = now()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT i.run_id FROM items i JOIN runs r ON r.id=i.run_id "
                "WHERE i.id=? AND i.lease_owner=? AND i.status='running' AND r.status='running'",
                (item_id, worker_id),
            ).fetchone()
            if not row:
                return False
            self._conn.execute(
                "UPDATE items SET status=?, lease_owner=NULL, lease_until=NULL, result=?, error=?, updated_at=? WHERE id=?",
                (status, result, error[:4000], ts, item_id),
            )
            attempt_status = "retryable_error" if status == "open" else status
            self._conn.execute(
                "UPDATE attempts SET status=?, ended_at=?, output=?, error_kind=?, error=? WHERE item_id=? AND worker_id=? AND status='running'",
                (attempt_status, ts, result, error_kind, error[:4000], item_id, worker_id),
            )
            self._conn.execute(
                "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                (
                    row[0],
                    "item_" + status,
                    json.dumps(
                        {
                            "item_id": item_id,
                            "error": error,
                            "result": result,
                            "evidence": {
                                "item_id": item_id,
                                "attempt": self._conn.execute(
                                    "SELECT COUNT(*) FROM attempts WHERE item_id=?", (item_id,)
                                ).fetchone()[0],
                                "output": result[-2000:],
                            },
                        },
                        ensure_ascii=False,
                    ),
                    ts,
                ),
            )
            if status in {"blocked", "exhausted"}:
                self._conn.execute(
                    "UPDATE items SET status='blocked', error=? WHERE id IN (SELECT item_id FROM item_deps WHERE depends_on_id=?) AND status='open'",
                    (f"依赖 {item_id} 未完成", item_id),
                )
            parent = self._conn.execute(
                "SELECT parent_id FROM items WHERE id=?", (item_id,)
            ).fetchone()
            if parent and parent[0] and status in {"blocked", "exhausted"}:
                self._conn.execute(
                    "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                    (
                        row[0],
                        "repair_failed",
                        json.dumps(
                            {"item_id": item_id, "failed_item_id": parent[0], "error": error},
                            ensure_ascii=False,
                        ),
                        ts,
                    ),
                )
            if (
                status == "done"
                and self._conn.execute(
                    "SELECT 1 FROM items WHERE id=? AND parent_id IS NOT NULL", (item_id,)
                ).fetchone()
            ):
                ancestor = item_id
                while True:
                    parent_row = self._conn.execute(
                        "SELECT parent_id FROM items WHERE id=?", (ancestor,)
                    ).fetchone()
                    if not parent_row or not parent_row[0]:
                        break
                    ancestor = parent_row[0]
                self._conn.execute(
                    "UPDATE items SET status='open', error='', updated_at=? WHERE run_id=? AND status='blocked'"
                    " AND id IN (SELECT item_id FROM item_deps WHERE depends_on_id=?)"
                    " AND NOT EXISTS (SELECT 1 FROM item_deps d JOIN items dep ON dep.id=d.depends_on_id"
                    " WHERE d.item_id=items.id AND dep.status!='done'"
                    " AND NOT (dep.status IN ('blocked','exhausted','failed','error')"
                    " AND EXISTS (SELECT 1 FROM items r WHERE r.parent_id=dep.id AND r.status='done')))",
                    (ts, row[0], ancestor),
                )
            return True

    def requeue_expired(self, run_id: str | None = None, now_value: str | None = None) -> int:
        stamp = now_value or now()
        with self._lock, self._conn:
            query = (
                "SELECT id, run_id, attempts, max_attempts FROM items "
                # 旧版本或异常中断可能留下 NULL lease；running Item 不能永久占住 DAG。
                "WHERE status='running' AND (lease_until IS NULL OR lease_until < ?)"
            )
            args: tuple = (stamp,)
            if run_id:
                query += " AND run_id=?"
                args += (run_id,)
            rows = self._conn.execute(query, args).fetchall()
            for row in rows:
                next_status = "exhausted" if row[2] >= row[3] else "open"
                self._conn.execute(
                    "UPDATE items SET status=?, lease_owner=NULL, lease_until=NULL, updated_at=? WHERE id=?",
                    (next_status, stamp, row[0]),
                )
                self._conn.execute(
                    "UPDATE attempts SET status='retryable_error', ended_at=?, error_kind='lease_expired',"
                    " error='租约过期' WHERE item_id=? AND status='running'",
                    (stamp, row[0]),
                )
                self._conn.execute(
                    "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                    (row[1], "lease_expired", json.dumps({"item_id": row[0]}), stamp),
                )
                if next_status == "exhausted":
                    self._conn.execute(
                        "UPDATE items SET status='blocked', error=? WHERE id IN (SELECT item_id FROM item_deps WHERE depends_on_id=?) AND status='open'",
                        (f"依赖 {row[0]} 重试耗尽", row[0]),
                    )
            return len(rows)
