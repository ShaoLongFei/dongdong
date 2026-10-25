"""会议快照与方案领取；复用现有 SQLite 连接和互斥。"""

import hashlib
import json
import uuid

from .store import now


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class Meetings:
    def __init__(self, store):
        self.store = store
        with store._lock, store._conn:
            store._conn.executescript("""
                CREATE TABLE IF NOT EXISTS meetings (
                  sid TEXT PRIMARY KEY REFERENCES sessions(id), workspace TEXT NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 1, phase TEXT NOT NULL DEFAULT 'discussion',
                  seq INTEGER NOT NULL DEFAULT 0, current TEXT NOT NULL DEFAULT '',
                  run_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
                );
                DROP INDEX IF EXISTS one_active_meeting;
                CREATE TABLE IF NOT EXISTS meeting_discussion (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT NOT NULL REFERENCES sessions(id),
                  seq INTEGER NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proposals (
                  id TEXT PRIMARY KEY, sid TEXT NOT NULL REFERENCES sessions(id), version INTEGER NOT NULL,
                  cutoff INTEGER NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL,
                  shown TEXT NOT NULL DEFAULT '', confirmation TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'ready', run_id TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL, UNIQUE(sid, version)
                );
                CREATE TABLE IF NOT EXISTS meeting_controls (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT NOT NULL, action TEXT NOT NULL,
                  text TEXT NOT NULL, source TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
            store._conn.execute(
                "UPDATE meetings SET phase='interrupted', error='进程重启，请重新整理或显式恢复' "
                "WHERE phase IN ('generating','executing')"
            )
            store._conn.execute("UPDATE proposals SET shown=''")

    def active(self, sid: str) -> dict | None:
        with self.store._lock:
            row = self.store._conn.execute(
                "SELECT * FROM meetings WHERE enabled=1 AND sid=?", (sid,)
            ).fetchone()
        return dict(row) if row else None

    def get(self, sid: str) -> dict | None:
        with self.store._lock:
            row = self.store._conn.execute("SELECT * FROM meetings WHERE sid=?", (sid,)).fetchone()
        return dict(row) if row else None

    def open(self, sid: str, workspace: str) -> None:
        with self.store._lock, self.store._conn:
            existing = self.store._conn.execute(
                "SELECT workspace FROM meetings WHERE sid=?", (sid,)
            ).fetchone()
            if existing and existing[0] != workspace:
                raise ValueError("会议目录固定，请为其他项目开启新会话")
            self.store._conn.execute(
                "INSERT INTO meetings(sid,workspace) VALUES (?,?) ON CONFLICT(sid) "
                "DO UPDATE SET enabled=1",
                (sid, workspace),
            )

    def update(self, sid: str, *, phase: str, run_id: str = "", error: str = "") -> None:
        with self.store._lock, self.store._conn:
            self.store._conn.execute(
                "UPDATE meetings SET phase=?,run_id=?,error=? WHERE sid=?",
                (phase, run_id, error[:4000], sid),
            )

    def close(self, sid: str) -> None:
        with self.store._lock, self.store._conn:
            self.store._conn.execute("UPDATE meetings SET enabled=0 WHERE sid=?", (sid,))
            self.store._conn.execute("UPDATE proposals SET shown='' WHERE sid=?", (sid,))

    def discuss(self, sid: str, text: str, source: str) -> int:
        if not text.strip() or len(text) > 32000:
            raise ValueError("讨论文字需在 1–32000 字符内")
        with self.store._lock, self.store._conn:
            self.store._conn.execute(
                "UPDATE meetings SET seq=seq+1 WHERE sid=? AND enabled=1", (sid,)
            )
            row = self.store._conn.execute(
                "SELECT seq,enabled FROM meetings WHERE sid=?", (sid,)
            ).fetchone()
            if not row or not row[1]:
                raise ValueError("代码修改模式未开启")
            self.store._conn.execute(
                "INSERT INTO meeting_discussion(sid,seq,text,source,created_at) VALUES (?,?,?,?,?)",
                (sid, row[0], text, source, now()),
            )
            return row[0]

    def discussion(self, sid: str, cutoff: int | None = None) -> list[dict]:
        with self.store._lock:
            rows = self.store._conn.execute(
                "SELECT * FROM meeting_discussion WHERE sid=? AND seq<=? ORDER BY seq",
                (sid, cutoff if cutoff is not None else 2**63 - 1),
            ).fetchall()
        return [dict(row) for row in rows]

    def proposal(self, pid: str) -> dict | None:
        with self.store._lock:
            row = self.store._conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        result = dict(row)
        for key in ("payload", "confirmation"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    def history(self, sid: str) -> list[dict]:
        with self.store._lock:
            rows = self.store._conn.execute(
                "SELECT id,version,status,run_id,created_at FROM proposals WHERE sid=? ORDER BY version DESC",
                (sid,),
            ).fetchall()
        return [dict(row) for row in rows]

    def publish(self, sid: str, cutoff: int, payload: dict, pid: str) -> dict:
        with self.store._lock, self.store._conn:
            version = self.store._conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM proposals WHERE sid=?", (sid,)
            ).fetchone()[0]
            self.store._conn.execute(
                "INSERT INTO proposals(id,sid,version,cutoff,payload,hash,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    pid,
                    sid,
                    version,
                    cutoff,
                    json.dumps(payload, ensure_ascii=False),
                    digest(payload),
                    now(),
                ),
            )
            self.store._conn.execute("UPDATE meetings SET current=? WHERE sid=?", (pid, sid))
        return self.proposal(pid)

    def shown(self, sid: str, pid: str, expected: str) -> str:
        token = uuid.uuid4().hex
        with self.store._lock, self.store._conn:
            cur = self.store._conn.execute(
                "UPDATE proposals SET shown=? WHERE id=? AND sid=? AND hash=? AND id="
                "(SELECT current FROM meetings WHERE sid=? AND enabled=1)",
                (token, pid, sid, expected, sid),
            )
            if cur.rowcount != 1:
                raise ValueError("方案已更新或会议已退出")
        return token

    def claim(self, sid: str, pid: str, expected: str, confirmation: dict) -> dict:
        with self.store._lock, self.store._conn:
            cur = self.store._conn.execute(
                "UPDATE proposals SET status='claimed',confirmation=? WHERE id=? AND sid=? "
                "AND hash=? AND status='ready' AND shown!='' AND EXISTS "
                "(SELECT 1 FROM meetings m WHERE m.sid=? AND m.enabled=1 AND m.current=proposals.id "
                "AND m.seq=proposals.cutoff AND m.phase='ready')",
                (json.dumps(confirmation, ensure_ascii=False), pid, sid, expected, sid),
            )
            if cur.rowcount != 1:
                raise ValueError("方案未展示、已有新意见、版本过期或已被执行；请检查当前方案")
        return self.proposal(pid)

    def execution(self, pid: str, status: str, run_id: str = "") -> None:
        with self.store._lock, self.store._conn:
            self.store._conn.execute(
                "UPDATE proposals SET status=?,run_id=CASE WHEN ?!='' THEN ? ELSE run_id END WHERE id=?",
                (status, run_id, run_id, pid),
            )

    def control(self, sid: str, action: str, text: str, source: str, result: str) -> None:
        with self.store._lock, self.store._conn:
            self.store._conn.execute(
                "INSERT INTO meeting_controls(sid,action,text,source,result,created_at) VALUES (?,?,?,?,?,?)",
                (sid, action, text, source, result, now()),
            )

    def controls(self, sid: str) -> list[dict]:
        with self.store._lock:
            rows = self.store._conn.execute(
                "SELECT * FROM meeting_controls WHERE sid=? ORDER BY id DESC LIMIT 30", (sid,)
            ).fetchall()
        return [dict(row) for row in rows]
