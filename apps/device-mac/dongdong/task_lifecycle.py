"""任务管理器的生命周期辅助函数。"""

from datetime import UTC, datetime


def monitor(manager, run_id: str) -> dict:
    manager.store.requeue_expired(run_id)
    view = manager.status(run_id)
    run = view.get("run") or {}
    if (
        run.get("status") == "running"
        and run.get("deadline_at")
        and run["deadline_at"] <= datetime.now(UTC).isoformat(timespec="seconds")
    ):
        manager._finish(run_id, "timeout", "超过任务截止时间")
        view = manager.status(run_id)
    return view


def recover(manager) -> int:
    with manager.store._lock, manager.store._conn:
        rows = manager.store._conn.execute(
            "SELECT id FROM runs WHERE status IN ('running','planning')"
        ).fetchall()
        for row in rows:
            manager.store._conn.execute(
                "UPDATE runs SET status='paused', error='进程重启，请确认后恢复' WHERE id=?", row
            )
            manager.store._conn.execute(
                "UPDATE items SET status='stopped', lease_owner=NULL, lease_until=NULL "
                "WHERE run_id=? AND status='running'",
                row,
            )
        return len(rows)


def close(manager) -> None:
    manager._closed = True
    for event in manager._runs.values():
        event.set()
    for thread in manager._threads.values():
        thread.join()
    manager.executor.shutdown(wait=True, cancel_futures=True)
