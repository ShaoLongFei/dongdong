import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))
os.environ["DONGDONG_HOME"] = tempfile.mkdtemp(prefix="dongdong-agent-smoke-")

from dongdong.store import Store  # noqa: E402


def test_store_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.db"
        store = Store(path)
        sid = store.create_session("测试")
        store.append_message(sid, "user", "你好")
        store.append_message(sid, "assistant", "在")
        rid = store.create_run(sid)
        store.add_event(rid, "text", {"delta": "在"})
        store.record_tool_call(rid, "shell", {"command": "pwd"}, "ok", True, 12)
        store.finish_run(rid, "done", usage={"total_tokens": 3})
        store.close()

        store = Store(path)
        assert store.get_session(sid)["title"] == "测试"
        assert [m["content"] for m in store.messages(sid)] == ["你好", "在"]
        assert store.events(rid)[0]["type"] == "text"
        assert store.get_run(rid)["status"] == "done"
        assert store.get_run(rid)["usage"]["total_tokens"] == 3
        newer = store.create_session("更新的会话")
        assert store.list_sessions()[0]["id"] == newer
        store.close()


def test_store_stops_orphan_runs():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.db"
        store = Store(path)
        sid = store.create_session("测试")
        rid = store.create_run(sid)
        store.close()

        store = Store(path)
        assert store.stop_orphan_runs() == 1
        assert store.get_run(rid)["status"] == "stopped"
        assert store.stop_orphan_runs() == 0
        store.close()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001 测试跑挂了要打印出来
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("agent smoke 通过" if not failures else f"{failures} 个失败")
    sys.exit(1 if failures else 0)
