"""SQLite 持久化：会话、消息、run、事件、工具调用。"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_session(self, title: str = "新会话", channel: str = "web") -> str:
        sid, ts = uuid.uuid4().hex[:12], now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sessions(id, title, channel, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (sid, title.strip() or "新会话", channel, ts, ts),
            )
        return sid

    def list_sessions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get_session(self, sid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str = "",
        tool_calls: list | None = None,
        tool_call_id: str = "",
    ) -> int:
        ts = now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO messages(session_id, role, content, tool_calls, tool_call_id,"
                " created_at) VALUES (?,?,?,?,?,?)",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(tool_calls or [], ensure_ascii=False),
                    tool_call_id,
                    ts,
                ),
            )
            self._conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (ts, session_id))
            return int(cur.lastrowid or 0)

    def messages(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["tool_calls"] = json.loads(item["tool_calls"] or "[]")
            out.append(item)
        return out

    def create_run(self, session_id: str) -> str:
        rid = uuid.uuid4().hex[:12]
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO runs(id, session_id, status, started_at) VALUES (?,?,?,?)",
                (rid, session_id, "running", now()),
            )
        return rid

    def finish_run(
        self, run_id: str, status: str, error: str = "", usage: dict | None = None
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET status=?, error=?, usage=?, ended_at=? WHERE id=?",
                (
                    status,
                    error[:4000],
                    json.dumps(usage or {}, ensure_ascii=False),
                    now(),
                    run_id,
                ),
            )

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["usage"] = json.loads(item["usage"] or "{}")
        return item

    def stop_orphan_runs(self) -> int:
        """进程重启后，把上次没来得及收尾的 running 标成 stopped。"""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE runs SET status='stopped', error='进程重启', ended_at=?"
                " WHERE status='running'",
                (now(),),
            )
            return int(cur.rowcount)

    def add_event(self, run_id: str, kind: str, data: dict) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                (run_id, kind, json.dumps(data, ensure_ascii=False), now()),
            )
            return int(cur.lastrowid or 0)

    def events(self, run_id: str, since: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id", (run_id, since)
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item["data"] or "{}")
            out.append(item)
        return out

    def record_tool_call(
        self, run_id: str, name: str, args: dict, result: str, ok: bool, ms: int
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO tool_calls(run_id, name, args, result, ok, ms, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    name,
                    json.dumps(args, ensure_ascii=False),
                    result[:12000],
                    int(ok),
                    ms,
                    now(),
                ),
            )
            return int(cur.lastrowid or 0)
