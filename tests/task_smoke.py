from __future__ import annotations

# ruff: noqa: I001 -- 先导入 smoke，初始化临时 HOME 和设备端模块路径。

from smoke import asyncio, config, contextlib, tempfile, tools, Path
from dongdong.store import Store
from dongdong.task_model import ItemSpec, RunSpec


def test_task_store_claims_dag_atomically_and_recovers_lease():
    with tempfile.TemporaryDirectory(prefix="dongdong-task-") as tmp:
        path = Path(tmp) / "tasks.db"
        first = Store(path)
        sid = first.create_session("task")
        run_id = first.create_task_run(sid, RunSpec(goal_text="完成 DAG"))
        first.add_items(
            run_id,
            [
                ItemSpec(id="a", instruction="a"),
                ItemSpec(id="b", instruction="b", depends_on=("a",)),
            ],
        )
        second = Store(path)
        assert first.claim_item(run_id, "a", "one", lease_seconds=1)
        assert not second.claim_item(run_id, "a", "two", lease_seconds=1)
        first.requeue_expired(run_id, now_value="2999-01-01T00:00:00+00:00")
        assert second.claim_item(run_id, "a", "two", lease_seconds=1)
        second.complete_item("a", "two", "ok")
        assert second.claim_item(run_id, "b", "two", lease_seconds=1)
        second.fail_item("b", "two", "blocked", error_kind="blocked")
        view = second.task_view(run_id)
        assert view["items"][0]["status"] == "done"
        assert view["items"][1]["status"] == "blocked"
        with contextlib.suppress(ValueError):
            first.add_items(run_id, [ItemSpec(id="c", instruction="c", depends_on=("missing",))])
            raise AssertionError("缺失依赖必须拒绝")
        with contextlib.suppress(ValueError):
            first.add_items(
                run_id,
                [
                    ItemSpec(id="c", instruction="c", depends_on=("d",)),
                    ItemSpec(id="d", instruction="d", depends_on=("c",)),
                ],
            )
            raise AssertionError("依赖环必须拒绝")
        first.finish_run(run_id, "done")
        assert not second.claim_item(run_id, "b", "late", lease_seconds=1)
        first.close()
        second.close()


def test_task_manager_runs_items_with_retry_and_stop():
    from dongdong.task_model import AttemptResult
    from dongdong.task_scheduler import TaskManager

    with tempfile.TemporaryDirectory(prefix="dongdong-manager-") as tmp:
        path = Path(tmp) / "tasks.db"
        store = Store(path)
        sid = store.create_session("manager")
        calls = []

        def runner(item, run, cancel_event):
            calls.append(item["id"])
            if item["instruction"] == "later":
                assert cancel_event.wait(2), "stop 必须取消正在执行的任务"
                return AttemptResult("stopped")
            if item["instruction"] == "retry" and calls.count(item["id"]) < 2:
                return AttemptResult("retryable_error", error="temporary")
            return AttemptResult("done", output=item["instruction"])

        manager = TaskManager(store, runner=runner, max_workers=2, poll_seconds=0.01)
        run_id = manager.submit(
            sid,
            "完成",
            items=[ItemSpec(id="ok", instruction="ok"), ItemSpec(id="retry", instruction="retry")],
        )
        assert manager.wait(run_id, 3)["run"]["status"] == "done"
        assert calls.count("retry") == 2
        stopped = manager.submit(sid, "停止", items=[ItemSpec(id="later", instruction="later")])
        assert manager.stop(stopped)["run"]["status"] == "canceled"
        assert not store.claim_item(stopped, "later", "late")
        manager.close()
        store.close()


def test_task_tools_and_web_projection_use_task_manager():
    assert {"self.task.status", "self.task.stop", "self.task.resume"} <= set(tools.TOOLS)
    from dongdong.web_runtime import WebState
    from dongdong.task_model import AttemptResult

    async def check():
        cfg = config.load()
        cfg["workspace"], cfg["model"]["api_key"] = tempfile.gettempdir(), "boundary-only"
        state = WebState(cfg)
        state.manager.task_executor.runner = lambda *_: AttemptResult("done", output="ok")
        sid = state.store.create_session("task web")
        rid = await state.start(sid, "hello")
        view = state.manager.status(rid)
        assert view["items"] and view["run"]["id"] == rid
        assert state.manager.status(rid)["run"]["status"] in {"running", "done", "failed"}
        await state.close()

    asyncio.run(check())


def test_task_manager_pause_resume_and_timeout_recovery():
    from dongdong.task_model import AttemptResult
    from dongdong.task_scheduler import TaskManager

    with tempfile.TemporaryDirectory(prefix="dongdong-recover-") as tmp:
        path = Path(tmp) / "tasks.db"
        store = Store(path)
        sid = store.create_session("recover")
        run_id = store.create_task_run(
            sid, RunSpec(goal_text="recover", deadline_at="2000-01-01T00:00:00+00:00")
        )
        store.add_items(run_id, [ItemSpec(id="recover-item", instruction="x")])
        assert store.claim_item(run_id, "recover-item", "worker", lease_seconds=60)
        restored = Store(path)
        assert restored.requeue_expired(run_id, now_value="2999-01-01T00:00:00+00:00") == 1
        manager = TaskManager(restored, runner=lambda *_: AttemptResult("done"), max_workers=1)
        assert manager.monitor_once(run_id)["run"]["status"] == "timeout"
        assert not restored.claim_item(run_id, "recover-item", "late")
        manager.close()
        store.close()
        restored.close()


if __name__ == "__main__":
    for name, test in sorted(globals().copy().items()):
        if name.startswith("test_"):
            test()
            print(f"ok   {name}")
