"""SQLite 的任务 DAG、领取与尝试记录；与会话复用同一事务锁。"""

import json
import uuid
from datetime import UTC, datetime

from .task_model import ItemSpec, RunSpec
from .task_projection import TaskProjection


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TaskStore(TaskProjection):
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
            ended = (
                ts
                if status in {"done", "failed", "error", "timeout", "canceled", "stopped"}
                else None
            )
            self._conn.execute(
                "UPDATE runs SET status=?, error=?, ended_at=COALESCE(?, ended_at) WHERE id=?",
                (status, error[:4000], ended, run_id),
            )
            self._conn.execute(
                "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                (run_id, event_type, json.dumps(payload, ensure_ascii=False), ts),
            )
        return True

    def create_task_run(self, session_id: str, spec: RunSpec) -> str:
        rid, ts = uuid.uuid4().hex[:12], now()
        workers = max(1, min(int(spec.max_workers), 32))
        with self._lock, self._conn:
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
                "INSERT INTO goals(id, run_id, text, created_at) VALUES (?,?,?,?)",
                (gid, rid, spec.goal_text, ts),
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
                self._conn.execute(
                    "INSERT INTO items(id, run_id, goal_id, parent_id, instruction, acceptance, agent, cwd,"
                    " priority, max_attempts, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        ts,
                        ts,
                    ),
                )
                for dependency in item.depends_on:
                    self._conn.execute(
                        "INSERT INTO item_deps(item_id, depends_on_id) VALUES (?,?)",
                        (item_id, dependency),
                    )
            return [item_id for _, item_id in resolved]

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
        from datetime import timedelta

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
                    " WHERE d.item_id=items.id AND p.status!='done')",
                    (worker_id, expiry, stamp, item_id, run_id),
                )
                if cur.rowcount != 1:
                    self._conn.rollback()
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
                "SELECT run_id FROM items WHERE id=? AND lease_owner=? AND status='running'",
                (item_id, worker_id),
            ).fetchone()
            if not row:
                return False
            self._conn.execute(
                "UPDATE items SET status=?, lease_owner=NULL, lease_until=NULL, result=?, error=?, updated_at=? WHERE id=?",
                (status, result, error[:4000], ts, item_id),
            )
            self._conn.execute(
                "UPDATE attempts SET status=?, ended_at=?, output=?, error_kind=?, error=? WHERE item_id=? AND worker_id=? AND status='running'",
                (status, ts, result, error_kind, error[:4000], item_id, worker_id),
            )
            self._conn.execute(
                "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                (
                    row[0],
                    "item_" + status,
                    json.dumps(
                        {"item_id": item_id, "error": error, "result": result}, ensure_ascii=False
                    ),
                    ts,
                ),
            )
            if status in {"blocked", "exhausted"}:
                self._conn.execute(
                    "UPDATE items SET status='blocked', error=? WHERE id IN (SELECT item_id FROM item_deps WHERE depends_on_id=?) AND status='open'",
                    (f"依赖 {item_id} 未完成", item_id),
                )
            return True

    def requeue_expired(self, run_id: str | None = None, now_value: str | None = None) -> int:
        stamp = now_value or now()
        with self._lock, self._conn:
            query = "SELECT id, run_id FROM items WHERE status='running' AND lease_until < ?"
            args: tuple = (stamp,)
            if run_id:
                query += " AND run_id=?"
                args += (run_id,)
            rows = self._conn.execute(query, args).fetchall()
            for row in rows:
                self._conn.execute(
                    "UPDATE items SET status='open', lease_owner=NULL, lease_until=NULL, updated_at=? WHERE id=?",
                    (stamp, row[0]),
                )
                self._conn.execute(
                    "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                    (row[1], "lease_expired", json.dumps({"item_id": row[0]}), stamp),
                )
            return len(rows)
