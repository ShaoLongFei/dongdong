"""任务状态的只读投影，和 SQLite 写入操作分开。"""

import json


class TaskProjection:
    def task_view(self, run_id: str):
        with self._lock:
            run = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                return None
            goals = self._conn.execute(
                "SELECT * FROM goals WHERE run_id=? ORDER BY rowid", (run_id,)
            ).fetchall()
            items = self._conn.execute(
                "SELECT * FROM items WHERE run_id=? ORDER BY priority DESC, rowid", (run_id,)
            ).fetchall()
            attempts = self._conn.execute(
                "SELECT a.* FROM attempts a JOIN items i ON i.id=a.item_id "
                "WHERE i.run_id=? ORDER BY a.id",
                (run_id,),
            ).fetchall()
            deps = self._conn.execute(
                "SELECT d.item_id, d.depends_on_id FROM item_deps d "
                "JOIN items i ON i.id=d.item_id WHERE i.run_id=?",
                (run_id,),
            ).fetchall()
        by_item = {}
        for row in deps:
            by_item.setdefault(row[0], []).append(row[1])
        projected = []
        for item in items:
            value = dict(item, depends_on=by_item.get(item["id"], []))
            try:
                value["scope"] = json.loads(value.pop("scope_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                value["scope"] = {}
            projected.append(value)
        goal_view = []
        for goal in goals:
            value = dict(goal)
            for key in ("acceptance", "evidence"):
                if value.get(key):
                    try:
                        value[key] = json.loads(value[key])
                    except (TypeError, json.JSONDecodeError):
                        pass
            goal_view.append(value)
        return {
            "run": dict(run),
            "goals": goal_view,
            "items": projected,
            "attempts": [dict(attempt) for attempt in attempts],
            "events": self.events(run_id),
        }

    def item_dependencies(self, item_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.* FROM item_deps d JOIN items p ON p.id=d.depends_on_id "
                "WHERE d.item_id=? ORDER BY p.rowid",
                (item_id,),
            ).fetchall()
        return [dict(row) for row in rows]
