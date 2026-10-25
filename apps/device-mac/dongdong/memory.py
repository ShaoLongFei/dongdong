import os
from pathlib import Path
from threading import RLock

DATA_DIR = Path(__file__).resolve().parents[3] / ".memory"
_memory = None
_lock = RLock()


def namespace(device_id: str, project_id: str | None = None) -> str:
    return f"{device_id}:project:{project_id}" if project_id else device_id


def _get_memory():
    global _memory
    with _lock:
        if _memory is None:
            if not os.environ.get("OPENAI_API_KEY"):
                raise RuntimeError("记忆未启用：缺少 OPENAI_API_KEY")
            from mem0 import Memory

            DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            DATA_DIR.chmod(0o700)
            _memory = Memory.from_config(
                {
                    "vector_store": {
                        "provider": "qdrant",
                        "config": {"path": str(DATA_DIR / "qdrant"), "collection_name": "dongdong"},
                    },
                    "history_db_path": str(DATA_DIR / "history.db"),
                    "llm": {"provider": "openai", "config": {"model": "gpt-5-mini"}},
                    "embedder": {
                        "provider": "openai",
                        "config": {"model": "text-embedding-3-small"},
                    },
                }
            )
        return _memory


def search(query: str, user_id: str) -> list[dict]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be empty")
    with _lock:
        try:
            return _get_memory().search(query, top_k=5, filters={"user_id": user_id})["results"]
        except Exception:
            raise RuntimeError("记忆检索失败") from None


def save(text: str, user_id: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must not be empty")
    with _lock:
        try:
            return _get_memory().add(text, user_id=user_id, infer=True)
        except Exception:
            raise RuntimeError("记忆保存失败") from None


def list_(user_id: str) -> list[dict]:
    with _lock:
        try:
            return _get_memory().get_all(filters={"user_id": user_id}, top_k=100)["results"]
        except Exception:
            raise RuntimeError("记忆列表读取失败") from None


def delete(memory_ids: list[str], user_id: str) -> list[str]:
    if (
        not isinstance(memory_ids, list)
        or not memory_ids
        or any(not isinstance(memory_id, str) or not memory_id.strip() for memory_id in memory_ids)
    ):
        raise ValueError("memory_ids must contain non-empty IDs")
    with _lock:
        try:
            memory = _get_memory()
            records = [memory.get(memory_id=memory_id) for memory_id in memory_ids]
        except Exception:
            raise RuntimeError("记忆归属检查失败") from None
        if any(not record or record.get("user_id") != user_id for record in records):
            raise ValueError("one or more memory IDs are missing or do not belong to this user")
        try:
            for memory_id in dict.fromkeys(memory_ids):
                memory.delete(memory_id=memory_id)
        except Exception:
            raise RuntimeError("记忆删除失败，请刷新列表确认结果") from None
    return memory_ids
