"""任务管理器的生命周期辅助函数。"""

import json
from datetime import UTC, datetime


def monitor(manager, run_id: str) -> dict:
    manager.store.requeue_expired(run_id)
    view = manager.status(run_id)
    run = view.get("run") or {}
    if run.get("status") == "running":
        items = view.get("items", [])
        if not items:
            manager.store.add_event(run_id, "stall_detected", {"count": 1, "limit": 1})
            manager._finish(run_id, "failed", "任务停摆：没有可执行的 Item")
            return manager.status(run_id)
        active = {item.get("status") for item in items}
        if not active & {"open", "running"} and not manager._items_resolved(items):
            counts = getattr(manager, "_stall_counts", {})
            count = counts.get(run_id, 0) + 1
            counts[run_id] = count
            manager._stall_counts = counts
            limit = max(
                1,
                min(int((manager.cfg.get("task_scheduler") or {}).get("stall_limit", 3)), 100),
            )
            if count >= limit:
                manager.store.add_event(
                    run_id,
                    "stall_detected",
                    {"count": count, "limit": limit},
                )
                manager._finish(run_id, "failed", "任务停摆：没有可执行或运行中的 Item")
                view = manager.status(run_id)
                run = view.get("run") or {}
        elif hasattr(manager, "_stall_counts"):
            manager._stall_counts.pop(run_id, None)
        # 截止时间与完成竞态时，已解析的 Item 优先走 Goal 验收。
        if manager._items_resolved(items):
            manager._finish(run_id, "done")
            return manager.status(run_id)
    if (
        run.get("status") == "running"
        and run.get("deadline_at")
        and run["deadline_at"] <= datetime.now(UTC).isoformat(timespec="seconds")
    ):
        cancel = manager._runs.get(run_id)
        if cancel:
            cancel.set()
        manager._request_stop(run_id, "timeout", "超过任务截止时间")
        thread = manager._threads.get(run_id)
        if not thread or not thread.is_alive():
            manager._finalize_stop(run_id)
        view = manager.status(run_id)
    return view


def recover(manager) -> int:
    manager._last_recovered_runs = []
    # 先回收已经失效的租约；这些 Item 可以在显式 resume 时重新领取。
    with manager.store._lock:
        run_ids = [
            row[0]
            for row in manager.store._conn.execute(
                "SELECT id FROM runs WHERE status IN ('running','planning')"
            ).fetchall()
        ]
    for run_id in run_ids:
        manager.store.requeue_expired(run_id)

    recovered = 0
    with manager.store._lock, manager.store._conn:
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        rows = manager.store._conn.execute(
            "SELECT id FROM runs WHERE status IN ('running','planning')"
        ).fetchall()
        for row in rows:
            run_id = row[0]
            active_lease = manager.store._conn.execute(
                "SELECT COUNT(*) FROM items WHERE run_id=? AND status='running' "
                "AND lease_until IS NOT NULL AND lease_until > ?",
                (run_id, stamp),
            ).fetchone()[0]
            if active_lease:
                manager.store._conn.execute(
                    "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                    (
                        run_id,
                        "recovery_skipped_active_lease",
                        json.dumps(
                            {
                                "message": "检测到其他进程的有效 Item 租约，跳过恢复接管",
                                "active_items": active_lease,
                            },
                            ensure_ascii=False,
                        ),
                        stamp,
                    ),
                )
                continue
            stopped = manager.store._conn.execute(
                "SELECT COUNT(*) FROM items WHERE run_id=? AND status='running'", (run_id,)
            ).fetchone()[0]
            manager.store._conn.execute(
                "UPDATE runs SET status='paused', error='进程重启，请确认后恢复' WHERE id=?",
                (run_id,),
            )
            manager.store._conn.execute(
                "UPDATE items SET status='stopped', lease_owner=NULL, lease_until=NULL "
                "WHERE run_id=? AND status='running'",
                (run_id,),
            )
            manager.store._conn.execute(
                "UPDATE attempts SET status='stopped', ended_at=?, error_kind='recovered',"
                " error='进程重启，任务暂停' WHERE item_id IN (SELECT id FROM items WHERE run_id=?)"
                " AND status='running'",
                (manager.store._conn.execute("SELECT datetime('now')").fetchone()[0], run_id),
            )
            manager.store._conn.execute(
                "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,datetime('now'))",
                (
                    run_id,
                    "run_recovered",
                    json.dumps(
                        {
                            "message": "进程重启，任务暂停；过期租约已回收",
                            "stopped_items": stopped,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            manager._last_recovered_runs.append(run_id)
            recovered += 1
        return recovered


def close(manager) -> None:
    manager._closed = True
    for event in manager._runs.values():
        event.set()
    for thread in manager._threads.values():
        thread.join()
    manager.executor.shutdown(wait=True, cancel_futures=True)
