import os
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / ".memory"
_memory = None


def _get_memory():
    global _memory
    if _memory is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required to use memory tools")

        from mem0 import Memory

        DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    if not query.strip():
        raise ValueError("query must not be empty")
    return _get_memory().search(query, top_k=5, filters={"user_id": user_id})["results"]


def save(text: str, user_id: str) -> dict:
    if not text.strip():
        raise ValueError("text must not be empty")
    return _get_memory().add(text, user_id=user_id, infer=True)


def list_(user_id: str) -> list[dict]:
    return _get_memory().get_all(filters={"user_id": user_id}, top_k=100)["results"]


def delete(memory_ids: list[str], user_id: str) -> list[str]:
    if not memory_ids or any(
        not isinstance(memory_id, str) or not memory_id.strip() for memory_id in memory_ids
    ):
        raise ValueError("memory_ids must contain non-empty IDs")

    memory = _get_memory()
    records = [memory.get(memory_id=memory_id) for memory_id in memory_ids]
    if any(not record or record.get("user_id") != user_id for record in records):
        raise ValueError("one or more memory IDs are missing or do not belong to this user")

    for memory_id in memory_ids:
        memory.delete(memory_id=memory_id)
    return memory_ids
