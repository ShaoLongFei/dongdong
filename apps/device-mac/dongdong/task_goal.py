"""Goal 验收证据的持久化与结构化判定。"""

import json


class GoalVerification:
    def _ensure_goal_columns(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(goals)")}
        for name, definition in (
            ("acceptance", "TEXT NOT NULL DEFAULT ''"),
            ("summary", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE goals ADD COLUMN {name} {definition}")

    def record_goal_verification(
        self, run_id: str, status: str, evidence: dict, summary: str = ""
    ) -> bool:
        if status not in {"met", "failed", "needs_review", "abandoned"}:
            raise ValueError("无效的 Goal 验收状态")
        ts = self._goal_now()
        with self._lock, self._conn:
            self._ensure_goal_columns()
            goal = self._conn.execute(
                "SELECT id FROM goals WHERE run_id=? ORDER BY rowid LIMIT 1", (run_id,)
            ).fetchone()
            if not goal:
                return False
            self._conn.execute(
                "UPDATE goals SET status=?, evidence=?, summary=?, met_at=? WHERE id=?",
                (
                    status,
                    json.dumps(evidence or {}, ensure_ascii=False),
                    summary[:4000],
                    ts if status == "met" else None,
                    goal[0],
                ),
            )
            self._conn.execute(
                "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                (
                    run_id,
                    "goal_verified",
                    json.dumps(
                        {
                            "goal_id": goal[0],
                            "status": status,
                            "evidence": evidence or {},
                            "summary": summary,
                        },
                        ensure_ascii=False,
                    ),
                    ts,
                ),
            )
        return True

    def verify_goal(self, run_id: str, verifier=None) -> dict:
        with self._lock, self._conn:
            self._ensure_goal_columns()
        view = self.task_view(run_id)
        if not view or not view["goals"]:
            return {"status": "failed", "summary": "缺少 Goal", "evidence": {}}
        goal, items = view["goals"][0], view["items"]
        evidence_items = [
            {
                "item_id": item["id"],
                "attempt": item.get("attempts", 0),
                "status": item.get("status"),
                "output": (item.get("result") or "")[-2000:],
            }
            for item in items
        ]
        summary = "\n\n".join(item["output"] for item in evidence_items if item["output"])[:4000]
        evidence = {"items": evidence_items, "summary": summary}
        acceptance = goal.get("acceptance")
        if not acceptance:
            status, reason = "met", "没有声明验收约束，使用全部 Item 完成结果"
        elif verifier is None:
            status, reason = "needs_review", "存在 acceptance，但没有可追溯的验收 verifier"
        else:
            try:
                result = verifier(goal, items, evidence)
                if isinstance(result, bool):
                    status, reason = ("met" if result else "failed"), "verifier 返回布尔结论"
                elif isinstance(result, dict) and result.get("status") in {
                    "met",
                    "failed",
                    "needs_review",
                }:
                    status, reason = (
                        result["status"],
                        str(result.get("summary") or "verifier 返回结构化结论"),
                    )
                    evidence.update(result.get("evidence") or {})
                    summary = str(result.get("summary") or summary)[:4000]
                else:
                    status, reason = "needs_review", "verifier 未返回可靠的结构化结论"
            except Exception as exc:  # noqa: BLE001
                status, reason = "needs_review", f"验收 verifier 异常: {exc}"
        evidence["reason"] = reason
        self.record_goal_verification(run_id, status, evidence, summary)
        return {"status": status, "summary": summary, "evidence": evidence, "reason": reason}

    @staticmethod
    def _goal_now() -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat(timespec="seconds")
