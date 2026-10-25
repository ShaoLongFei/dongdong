from __future__ import annotations

# ruff: noqa: I001 -- 先导入 smoke，初始化临时 HOME 和设备端模块路径。

from smoke import asyncio, config, contextlib, tempfile, tools, Path
from dongdong.store import Store
from dongdong.task_model import ItemSpec, RunSpec
from dongdong.task_planner import TaskPlanner


def test_structured_planner_boundaries():
    with tempfile.TemporaryDirectory(prefix="dongdong-planner-") as tmp:
        root = Path(tmp).resolve()
        seen = []

        async def stream(_model, _messages, tools):
            seen.append(tools)
            yield type("Event", (), {"text": '{"items":[{"id":"a","instruction":"a"}]}'})()

        result = TaskPlanner("model", {"workspace": str(root)}, stream_fn=stream).plan(
            "目标", str(root)
        )
        assert result.items[0].id == "a" and seen == [[]]

        def bad(*_a, **_k):
            return '{"items":[{"id":"a","instruction":"a","depends_on":["x"]}]}'

        result = TaskPlanner("model", {"workspace": str(root)}, stream_fn=bad).plan(
            "原目标", str(root)
        )
        assert result.used_fallback and result.items[0].instruction == "原目标"


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
        output = "完整输出" * 4000
        second.complete_item("a", "two", output)
        assert second.claim_item(run_id, "b", "two", lease_seconds=1)
        second.fail_item("b", "two", "blocked", error_kind="blocked")
        view = second.task_view(run_id)
        assert view["items"][0]["status"] == "done"
        assert view["items"][0]["result"] == output
        assert next(a for a in view["attempts"] if a["worker_id"] == "two")["output"] == output
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
            items=[
                ItemSpec(id="ok", instruction="ok"),
                ItemSpec(id="retry", instruction="retry"),
            ],
        )
        assert manager.wait(run_id, 3)["run"]["status"] == "done"
        assert calls.count("retry") == 2
        stopped = manager.submit(sid, "停止", items=[ItemSpec(id="later", instruction="later")])
        assert manager.stop(stopped)["run"]["status"] == "canceled"
        stopped_view = store.task_view(stopped)
        assert stopped_view["items"][0]["status"] == "stopped"
        assert all(attempt["status"] == "stopped" for attempt in stopped_view["attempts"])
        assert not store.claim_item(stopped, "later", "late")
        manager.close()
        store.close()


def test_automatic_planning_populates_events_and_runs_dependencies():
    from dongdong.task_model import AttemptResult
    from dongdong.task_scheduler import TaskManager

    with tempfile.TemporaryDirectory(prefix="dongdong-plan-run-") as tmp:
        store = Store(Path(tmp) / "plan.db")
        sid = store.create_session("planner")
        manager = TaskManager(store, max_workers=2, poll_seconds=0.01)
        manager.planner.plan = lambda *_: type(
            "Plan",
            (),
            {
                "items": [
                    ItemSpec(id="a", instruction="a"),
                    ItemSpec(id="b", instruction="b"),
                    ItemSpec(id="c", instruction="c", depends_on=("a", "b")),
                ],
                "error": "",
                "used_fallback": False,
            },
        )()
        manager.task_executor.runner = lambda item, *_: AttemptResult(
            "done", output=item["instruction"]
        )
        run_id = manager.submit(sid, "拆解目标")
        view = manager.wait(run_id, 3)
        assert view["run"]["status"] == "done"
        assert {item["instruction"] for item in view["items"]} == {"a", "b", "c"}
        assert {event["type"] for event in view["events"]} >= {
            "planning_started",
            "planning_done",
            "final_verification_started",
            "final_verification_done",
            "goal_verified",
        }
        assert [attempt["item_id"] for attempt in view["attempts"]][-1].endswith("-c")
        manager.close()
        store.close()


def test_task_tools_and_web_projection_use_task_manager():
    assert {
        "self.task.status",
        "self.task.stop",
        "self.task.resume",
        "self.task.goal_preview",
    } <= set(tools.TOOLS)
    from dongdong.web_runtime import WebState
    from dongdong.task_model import AttemptResult

    async def check():
        cfg = config.load()
        cfg["workspace"], cfg["model"]["api_key"] = (
            tempfile.gettempdir(),
            "boundary-only",
        )
        state = WebState(cfg)
        state.manager.task_executor.runner = lambda *_: AttemptResult("done", output="ok")
        state.manager.runner = state.manager.task_executor.runner
        sid = state.store.create_session("task web")
        rid = await state.start(sid, "hello")
        for _ in range(20):
            view = state.manager.status(rid)
            if view["items"]:
                break
            await asyncio.sleep(0.01)
        view = state.manager.status(rid)
        assert view["items"] and view["run"]["id"] == rid
        assert state.manager.status(rid)["run"]["status"] in {
            "planning",
            "running",
            "done",
            "failed",
        }
        await state.close()

    asyncio.run(check())


def test_goal_preview_is_read_only_and_handles_missing_acceptance():
    with tempfile.TemporaryDirectory(prefix="dongdong-goal-preview-") as tmp:
        store = Store(Path(tmp) / "goals.db")
        sid = store.create_session("preview")
        run_id = store.create_task_run(sid, RunSpec(goal_text="验收"))
        store.add_items(run_id, [ItemSpec(id="item", instruction="完成", acceptance="通过")])
        assert store.claim_item(run_id, "item", "smoke")
        store.complete_item("item", "smoke", "完成输出")
        before = store.task_view(run_id)
        result = store.preview_goal_verification(
            run_id, lambda _goal, _items, _evidence: {"status": "met", "summary": "可见结论"}
        )
        after = store.task_view(run_id)
        assert result["status"] == "met" and result["summary"] == "可见结论"
        assert (len(before["goals"]), len(before["items"]), len(before["events"])) == (
            len(after["goals"]),
            len(after["items"]),
            len(after["events"]),
        )
        assert before["goals"][0]["status"] == after["goals"][0]["status"] == "open"
        assert store.preview_goal_verification("missing")["summary"] == "缺少 Goal"
        assert store.preview_goal_verification(run_id)["status"] == "needs_review"
        no_acceptance = store.create_task_run(sid, RunSpec(goal_text="无约束"))
        assert store.preview_goal_verification(no_acceptance)["status"] == "met"
        store.close()


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


def test_task_scheduler_config_and_startup_recovery():
    from dongdong.task_scheduler import TaskManager

    with tempfile.TemporaryDirectory(prefix="dongdong-scheduler-config-") as tmp:
        path = Path(tmp) / "tasks.db"
        workspace = Path(tmp).resolve()
        first = Store(path)
        sid = first.create_session("recover")
        run_id = first.create_task_run(sid, RunSpec(goal_text="recover"))
        first.add_items(
            run_id,
            [
                ItemSpec(id="expired", instruction="expired", scope={"write": ["expired.txt"]}),
                ItemSpec(id="active", instruction="active", scope={"write": ["active.txt"]}),
            ],
        )
        assert first.claim_item(run_id, "expired", "old", lease_seconds=1)
        assert first.claim_item(run_id, "active", "new", lease_seconds=60)
        with first._lock, first._conn:
            first._conn.execute(
                "UPDATE items SET lease_until=? WHERE id=?",
                ("2000-01-01T00:00:00+00:00", "expired"),
            )
        first.close()

        cfg = {
            "workspace": str(workspace),
            "task_scheduler": {
                "max_workers": 3,
                "poll_seconds": 0.02,
                "lease_seconds": 7,
            },
        }
        restored = Store(path)
        manager = TaskManager(restored, cfg=cfg)
        assert (manager.max_workers, manager.poll_seconds, manager.lease_seconds) == (
            3,
            0.02,
            7,
        )
        explicit = TaskManager(
            restored,
            cfg=cfg,
            max_workers=2,
            poll_seconds=0.03,
            lease_seconds=9,
        )
        assert (
            explicit.max_workers,
            explicit.poll_seconds,
            explicit.lease_seconds,
        ) == (
            2,
            0.03,
            9,
        )
        assert manager.recover_runs() == 1
        view = restored.task_view(run_id)
        statuses = {item["id"]: item["status"] for item in view["items"]}
        assert view["run"]["status"] == "paused"
        assert statuses == {"expired": "open", "active": "stopped"}
        assert any(event["type"] == "lease_expired" for event in view["events"])
        assert any(event["type"] == "run_recovered" for event in view["events"])
        explicit.close()
        manager.close()
        restored.close()


def test_cross_store_recovery_requires_explicit_resume():
    from dongdong.task_model import AttemptResult
    from dongdong.task_scheduler import TaskManager

    with tempfile.TemporaryDirectory(prefix="dongdong-cross-store-") as tmp:
        root = Path(tmp).resolve()
        db = root / "tasks.db"
        first = Store(db)
        sid = first.create_session("cross store")
        run_id = first.create_task_run(sid, RunSpec(goal_text="resume", workspace=str(root)))
        first.add_items(run_id, [ItemSpec(id="write", instruction="write")])
        assert first.claim_item(run_id, "write", "old-worker", lease_seconds=60)
        first.close()

        restored = Store(db)
        manager = TaskManager(
            restored,
            cfg={"workspace": str(root), "task_scheduler": {"poll_seconds": 0.01}},
            runner=lambda *_: AttemptResult("done", output="resumed"),
        )
        assert manager.recover_runs() == 1
        paused = manager.status(run_id)
        assert paused["run"]["status"] == "paused"
        assert paused["items"][0]["status"] == "stopped"
        assert not restored.claim_item(run_id, "write", "unexpected-replay")

        manager.resume(run_id)
        finished = manager.wait(run_id, 3)
        assert finished["run"]["status"] == "done"
        assert finished["items"][0]["result"] == "resumed"
        manager.close()
        restored.close()


def test_task_scope_conflicts_and_planner_boundaries():
    from dongdong.task_model import normalize_scope
    from dongdong.task_planner import TaskPlanner

    assert normalize_scope({"read": ["src/a.txt"]}) == {"read": ["src/a.txt"]}
    for value in (
        {"write": ["/tmp/out"]},
        {"write": ["../out"]},
        {"write": ["bad\\path"]},
        {"other": ["out"]},
        {"write": ["bad\x00path"]},
    ):
        with contextlib.suppress(ValueError):
            normalize_scope(value)
            raise AssertionError("非法 scope 必须拒绝")

    with tempfile.TemporaryDirectory(prefix="dongdong-scope-") as tmp:
        root = Path(tmp).resolve()
        store = Store(root / "scope.db")
        sid = store.create_session("scope")

        def run_with_items(items, workspace=str(root)):
            Path(workspace).mkdir(parents=True, exist_ok=True)
            run = store.create_task_run(sid, RunSpec(goal_text="scope", workspace=workspace))
            store.add_items(run, items)
            return run

        parallel = run_with_items(
            [
                ItemSpec(id="write-a", instruction="a", scope={"write": ["a.txt"]}),
                ItemSpec(id="write-b", instruction="b", scope={"write": ["b.txt"]}),
                ItemSpec(id="read-a", instruction="r", scope={"read": ["a.txt"]}),
            ]
        )
        assert store.claim_item(parallel, "write-a", "one")
        assert store.claim_item(parallel, "write-b", "two")
        assert not store.claim_item(parallel, "read-a", "three")
        assert store.complete_item("write-a", "one")
        assert store.complete_item("write-b", "two")
        conflict = run_with_items(
            [
                ItemSpec(id="first", instruction="first", scope={"write": ["same.txt"]}),
                ItemSpec(id="second", instruction="second", scope={"write": ["same.txt"]}),
            ]
        )
        assert store.claim_item(conflict, "first", "one")
        assert not store.claim_item(conflict, "second", "two")
        assert any(event["type"] == "scope_conflict" for event in store.events(conflict))
        assert store.complete_item("first", "one")
        readers_workspace = root / "readers"
        readers_workspace.mkdir()
        readers = run_with_items(
            [
                ItemSpec(id="reader-a", instruction="a", scope={"read": ["same.txt"]}),
                ItemSpec(id="reader-b", instruction="b", scope={"read": ["same.txt"]}),
            ],
            workspace=str(readers_workspace),
        )
        assert store.claim_item(readers, "reader-a", "one")
        assert store.claim_item(readers, "reader-b", "two")
        other_workspace = root / "other"
        other_workspace.mkdir()
        other = run_with_items(
            [ItemSpec(id="other", instruction="other", scope={"write": ["same.txt"]})],
            workspace=str(other_workspace),
        )
        assert store.claim_item(other, "other", "three")
        nested_workspace = other_workspace / "nested"
        nested_workspace.mkdir()
        nested = run_with_items(
            [ItemSpec(id="nested", instruction="nested", cwd=str(nested_workspace))],
            workspace=str(nested_workspace),
        )
        assert not store.claim_item(nested, "nested", "nested-worker")
        store.close()

    planner = TaskPlanner("model", {"workspace": str(root)})
    result = planner._validate(
        {"items": [{"id": "x", "instruction": "x", "scope": {"write": ["x.py"]}}]},
        str(root),
    )
    assert result[0].scope == {"write": ["x.py"]}


def test_task_monitor_detects_stall_without_killing_dependency_wait():
    from dongdong.task_scheduler import TaskManager

    with tempfile.TemporaryDirectory(prefix="dongdong-stall-") as tmp:
        store = Store(Path(tmp) / "stall.db")
        sid = store.create_session("stall")
        waiting = store.create_task_run(sid, RunSpec(goal_text="waiting"))
        store.add_items(
            waiting,
            [
                ItemSpec(id="parent", instruction="parent"),
                ItemSpec(id="child", instruction="child", depends_on=("parent",)),
            ],
        )
        manager = TaskManager(
            store,
            cfg={"workspace": tmp, "task_scheduler": {"stall_limit": 2}},
        )
        assert manager.monitor_once(waiting)["run"]["status"] == "running"
        assert not any(event["type"] == "stall_detected" for event in store.events(waiting))

        stalled = store.create_task_run(sid, RunSpec(goal_text="stalled"))
        store.add_items(stalled, [ItemSpec(id="blocked", instruction="blocked")])
        with store._lock, store._conn:
            store._conn.execute("UPDATE items SET status='blocked' WHERE id=?", ("blocked",))
        assert manager.monitor_once(stalled)["run"]["status"] == "running"
        assert manager.monitor_once(stalled)["run"]["status"] == "failed"
        assert any(event["type"] == "stall_detected" for event in store.events(stalled))
        empty = store.create_task_run(sid, RunSpec(goal_text="empty"))
        assert manager.monitor_once(empty)["run"]["status"] == "failed"
        manager.close()
        store.close()


def test_task_lifecycle_releases_leases_and_preserves_blocked_items():
    from dongdong.task_scheduler import TaskManager

    with tempfile.TemporaryDirectory(prefix="dongdong-lifecycle-") as tmp:
        store = Store(Path(tmp) / "lifecycle.db")
        sid = store.create_session("lifecycle")
        run_id = store.create_task_run(
            sid, RunSpec(goal_text="lifecycle", deadline_at="2000-01-01T00:00:00+00:00")
        )
        store.add_items(
            run_id,
            [
                ItemSpec(id="running", instruction="running"),
                ItemSpec(id="blocked", instruction="blocked"),
            ],
        )
        assert store.claim_item(run_id, "running", "worker")
        with store._lock, store._conn:
            store._conn.execute("UPDATE items SET status='blocked' WHERE id=?", ("blocked",))
            store._conn.execute("UPDATE items SET lease_until=NULL WHERE id=?", ("running",))
        manager = TaskManager(store, cfg={"workspace": tmp, "task_scheduler": {"drain_seconds": 0}})
        # NULL lease 的旧 running Item 能回收，deadline 收口同时结束 Attempt。
        assert store.requeue_expired(run_id, now_value="2999-01-01T00:00:00+00:00") == 1
        assert manager.monitor_once(run_id)["run"]["status"] == "timeout"
        view = store.task_view(run_id)
        assert all(
            item["status"] != "running" and item["lease_until"] is None for item in view["items"]
        )
        assert not any(attempt["status"] == "running" for attempt in view["attempts"])

        resume_id = store.create_task_run(sid, RunSpec(goal_text="resume"))
        store.add_items(resume_id, [ItemSpec(id="failed", instruction="failed")])
        with store._lock, store._conn:
            store._conn.execute("UPDATE runs SET status='failed' WHERE id=?", (resume_id,))
            store._conn.execute("UPDATE items SET status='blocked' WHERE id=?", ("failed",))
        assert manager.resume(resume_id)["items"][0]["status"] == "blocked"
        manager.close()
        store.close()


def test_conversations_isolate_modes_runs_voice_and_cancellation():
    import httpx
    from smoke import ROOT, patch, work_gate
    from dongdong.projects import use_session
    from dongdong.task_model import AttemptResult
    from dongdong.web_server import create_app

    class WaitingRuntime:
        def __init__(self, store, *args, **kwargs):
            self.store = store

        async def run(self, sid, text, on_event):
            rid = self.store.create_run(sid)
            on_event("run_start", {"run_id": rid})
            try:
                await asyncio.Event().wait()
            finally:
                self.store.finish_run(rid, "stopped")

    async def check():
        cfg = config.load()
        cfg["workspace"], cfg["model"]["api_key"] = str(ROOT), "boundary-only"
        app = create_app(cfg)
        host = app.state.web_state

        def wait_for_cancel(item, run, cancel):
            assert cancel.wait(4), "停止应传递给目标 worker"
            return AttemptResult("stopped")

        host.manager.task_executor.runner = wait_for_cancel
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:

                async def new():
                    response = await client.post("/api/sessions", json={})
                    assert response.status_code == 200, response.text
                    return response.json()["id"]

                a = await new()
                assert (
                    await client.put(f"/api/meetings/{a}", json={"enabled": True})
                ).status_code == 200
                with patch("dongdong.web_runtime.Runtime", WaitingRuntime):
                    arun = await host.start(a, "整理 A", meeting_options={})
                    host.meeting.db.update(a, phase="generating", run_id=arun)
                    b = await new()
                    health = (await client.get("/api/state")).json()
                    assert health["task_session"] == b and not health["busy"]
                    assert not health["meeting_session"] and health["active_runs"][a] == arun
                    host.voice_text("mic", "user", "B 的语音")
                    assert host.meeting.db.get(a)["seq"] == 0
                    assert host.store.messages(b)[-1]["content"] == "B 的语音"
                    reply = await client.post(
                        f"/api/sessions/{b}/messages", json={"text": "B 任务"}
                    )
                    assert reply.status_code == 200, reply.text
                    brun = reply.json()["run_id"]
                    duplicate = await client.post(
                        f"/api/sessions/{b}/messages", json={"text": "重入"}
                    )
                    assert duplicate.status_code == 409 and len(host.store.list_runs(b)) == 1
                    c = await new()
                    assert (
                        await client.put(f"/api/meetings/{c}", json={"enabled": True})
                    ).status_code == 200
                    assert host.meeting.db.active(a) and host.meeting.db.active(c)
                    assert host.task_session == c
                    crun = await host.start(c, "整理 C", meeting_options={})
                    assert host.task_session == c and len(host._runs) == 3
                    assert (await client.put("/api/settings", json={})).status_code == 409
                    host.meeting.notices[a] = "只属于 A"
                    assert host.meeting.view(c)["notice"] == ""
                    # 异步语音确认固定收到时的对话与方案，即使紧接着切换也不串台。
                    seen = []

                    async def action(sid, *args):
                        seen.append(sid)
                        return {"message": "已收到"}

                    with patch.object(host.meeting, "action", action):
                        host.voice_text("mic", "user", "按这个方案实施")
                        use_session(host, a)
                        await asyncio.gather(*host.meeting.pending)
                    assert seen == [c] and host.meeting.view(a)["notice"] == "只属于 A"
                    ctask = host._tasks[c]
                    assert (await client.post(f"/api/runs/{crun}/cancel")).status_code == 200
                    await asyncio.gather(ctask, return_exceptions=True)
                    assert host._runs[a] == arun and host._runs[b] == brun
                    assert (
                        work_gate.current(a) and work_gate.current(b) and not work_gate.current(c)
                    )
                    assert host.task_session == a
                    stream = await client.get(f"/api/runs/{crun}/events")
                    assert "event: end" in stream.text, "其他对话运行不能阻止历史流结束"
                    # 切换/归档空闲对话不会碰运行中的其他对话。
                    d = await new()
                    if hasattr(host, "stories"):
                        await host.story_mode(True)
                        dstory = host.story
                        e = await new()
                        assert host.story is not dstory and dstory.status == "recording"
                        erun = await host.start(e, "故事对话外的任务")
                        await host.stop_active(erun)
                        assert dstory.status == "recording"
                        await host.story_mode(False, d)
                    assert (
                        await client.patch(f"/api/sessions/{d}", json={"archived": True})
                    ).status_code == 200
                    assert (
                        await client.patch(f"/api/sessions/{a}", json={"archived": True})
                    ).status_code == 409
                    await host.stop_active(brun)
                    assert host._runs.get(a) == arun and not work_gate.current(b)
                    await host.stop_active(arun)
        assert not work_gate.current()

    asyncio.run(check())


def test_optional_auto_recovery_across_manager_restart():
    from dongdong.task_model import AttemptResult
    from dongdong.task_scheduler import TaskManager

    with tempfile.TemporaryDirectory(prefix="dongdong-auto-recover-") as tmp:
        root = Path(tmp).resolve()
        db = root / "tasks.db"
        (root / "explicit").mkdir()
        (root / "automatic").mkdir()
        first = Store(db)
        explicit_sid = first.create_session("explicit recover")
        automatic_sid = first.create_session("automatic recover")
        explicit_id = first.create_task_run(
            explicit_sid,
            RunSpec(goal_text="explicit", workspace=str(root / "explicit")),
        )
        automatic_id = first.create_task_run(
            automatic_sid,
            RunSpec(goal_text="automatic", workspace=str(root / "automatic")),
        )
        for run_id in (explicit_id, automatic_id):
            first.add_items(run_id, [ItemSpec(id=f"work-{run_id}", instruction="work")])
            assert first.claim_item(run_id, f"work-{run_id}", "old-process", lease_seconds=60)
        first.close()

        restored = Store(db)
        manager = TaskManager(
            restored,
            cfg={"workspace": str(root), "task_scheduler": {"auto_recover": "true"}},
            runner=lambda *_: AttemptResult("done", output="continued"),
            poll_seconds=0.01,
        )
        assert manager.auto_recover is False
        assert manager.recover_runs() == 2
        assert manager.status(explicit_id)["run"]["status"] == "paused"
        manager.resume(explicit_id)
        assert manager.wait(explicit_id, 3)["run"]["status"] == "done"
        manager.close()
        restored.close()

        restored = Store(db)
        manager = TaskManager(
            restored,
            cfg={"workspace": str(root), "task_scheduler": {"auto_recover": True}},
            runner=lambda *_: AttemptResult("done", output="continued"),
            poll_seconds=0.01,
        )
        assert manager.auto_recover is True
        with restored._lock, restored._conn:
            restored._conn.execute(
                "UPDATE runs SET status='running', error='' WHERE id=?", (automatic_id,)
            )
            restored._conn.execute(
                "UPDATE items SET status='running', lease_owner='old-process', lease_until=NULL "
                "WHERE run_id=?",
                (automatic_id,),
            )
        assert manager.recover_runs() == 1
        finished = manager.wait(automatic_id, 3)
        assert finished["run"]["status"] == "done"
        assert finished["items"][0]["result"] == "continued"
        assert "run_recovered" in [event["type"] for event in finished["events"]]
        assert manager.recover_runs() == 0
        manager.close()
        restored.close()


def test_model_repair_instruction_falls_back_safely():
    from dongdong.task_model import AttemptResult
    from dongdong.task_scheduler import TaskManager

    with tempfile.TemporaryDirectory(prefix="dongdong-model-repair-") as tmp:
        store = Store(Path(tmp) / "repair.db")
        sid = store.create_session("repair")
        rid = store.create_task_run(sid, RunSpec(goal_text="repair", workspace=tmp))
        store.add_items(rid, [ItemSpec(id="failed", instruction="原任务", cwd=tmp)])
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE items SET status='failed', error='temporary' WHERE id='failed'"
            )
            store._conn.execute("UPDATE runs SET status='failed' WHERE id=?", (rid,))

        def stream(*_args, **_kwargs):
            return '{"instruction":"模型建议的修复"}'

        manager = TaskManager(
            store,
            cfg={"workspace": tmp},
            model="model",
            runner=lambda item, *_: AttemptResult("done", output=item["instruction"]),
        )
        manager.planner.stream_fn = stream
        result = manager.repair(rid)
        finished = manager.wait(rid, 3)
        assert finished["run"]["status"] == "done"
        assert any("模型建议的修复" in item["result"] for item in finished["items"])
        assert any(event["type"] == "repair_planning_done" for event in finished["events"])
        manager.close()
        store.close()



if __name__ == "__main__":
    for name, test in sorted(globals().copy().items()):
        if name.startswith("test_"):
            test()
            print(f"ok   {name}")
