"""SQLite 持久化：会话、消息、run、事件、工具调用。"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .task_store import TaskStore

SCHEMA = Path(__file__).with_name("schema.sql")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store(TaskStore):
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        with self._conn:
            self._migrate_columns()
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(sessions)")}
            for name, definition in (
                ("project_id", "TEXT REFERENCES projects(id)"),
                ("archived_at", "TEXT"),
            ):
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS sessions_project_archive"
                " ON sessions(project_id, archived_at, updated_at DESC)"
            )

    def _migrate_columns(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(runs)")}
        additions = {
            "channel": "TEXT NOT NULL DEFAULT 'web'",
            "workspace": "TEXT NOT NULL DEFAULT ''",
            "goal_text": "TEXT NOT NULL DEFAULT ''",
            "max_workers": "INTEGER NOT NULL DEFAULT 1",
            "deadline_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_session(
        self, title: str = "新会话", channel: str = "web", project_id: str | None = None
    ) -> str:
        sid, ts = uuid.uuid4().hex[:12], now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sessions(id, title, channel, project_id, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (sid, title.strip() or "新会话", channel, project_id, ts, ts),
            )
        return sid

    def list_sessions(
        self,
        project_id: str | None = None,
        archived: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions WHERE (archived_at IS NOT NULL)=?"
                " AND (? IS NULL OR COALESCE(project_id,'')=?)"
                " ORDER BY updated_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (archived, project_id, project_id, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_session(self, sid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT sessions.*, projects.name AS project_name, projects.path AS project_path"
                " FROM sessions LEFT JOIN projects ON sessions.project_id=projects.id"
                " WHERE sessions.id=?",
                (sid,),
            ).fetchone()
        return dict(row) if row else None

    def list_projects(self) -> list[dict]:
        with self._lock:
            return [
                dict(row)
                for row in self._conn.execute("SELECT * FROM projects ORDER BY created_at, rowid")
            ]

    def get_project(self, project_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return dict(row) if row else None

    def create_project(self, name: str, path: str) -> str:
        pid = uuid.uuid4().hex[:12]
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO projects VALUES (?,?,?,?)", (pid, name, path, now()))
        return pid

    def rename_project(self, project_id: str, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE projects SET name=? WHERE id=?", (name, project_id))

    def update_session(self, sid: str, values: dict) -> None:
        fields = {k: v for k, v in values.items() if k in {"project_id", "archived_at"}}
        if fields:
            with self._lock, self._conn:
                if self._conn.execute(
                    "SELECT 1 FROM runs WHERE session_id=? AND status='running'", (sid,)
                ).fetchone():
                    raise ValueError("会话仍有任务在运行")
                assignments = ",".join(f"{key}=?" for key in fields)
                self._conn.execute(
                    f"UPDATE sessions SET {assignments} WHERE id=?", (*fields.values(), sid)
                )

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
            session = self._conn.execute(
                "SELECT archived_at FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None or session[0] is not None:
                raise ValueError("会话不存在或已归档，请先恢复")
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

    def list_runs(self, session_id: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT runs.*, (SELECT json_extract(data, '$.user_message_id') FROM events"
                " WHERE run_id=runs.id AND type='run_start' ORDER BY id LIMIT 1) AS user_message_id"
                " FROM runs WHERE session_id=? ORDER BY started_at DESC, rowid DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["usage"] = json.loads(item["usage"] or "{}")
            out.append(item)
        return out

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
                " WHERE status='running' AND NOT EXISTS (SELECT 1 FROM items WHERE run_id=runs.id)",
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
                    result,
                    int(ok),
                    ms,
                    now(),
                ),
            )
            return int(cur.lastrowid or 0)
