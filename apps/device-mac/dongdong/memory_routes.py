"""按明确项目范围管理记忆，写入期间阻止任务和项目切换。"""

import os
import uuid

from fastapi import APIRouter, Body, HTTPException

from . import memory, work_gate
from .memory_tools import threaded


def router(state) -> APIRouter:
    routes = APIRouter(prefix="/api/memories")

    def target(project_id):
        if project_id == "":
            project_id = None
        if project_id is not None:
            if not isinstance(project_id, str):
                raise HTTPException(400, "project_id 必须为字符串或 null")
            if not state.store.get_project(project_id):
                raise HTTPException(404, "项目不存在")
        return memory.namespace(state.cfg["device_id"], project_id)

    def enabled():
        return bool(os.environ.get("OPENAI_API_KEY"))

    async def mutate(body, deleting=False):
        owner = f"memory-{uuid.uuid4().hex}"
        if not work_gate.acquire(owner):
            raise HTTPException(409, "任务或设置操作进行中")
        try:
            user_id = target(body.get("project_id"))
            if not enabled():
                raise HTTPException(503, "记忆未启用")
            operation = memory.delete if deleting else memory.save
            value = body.get("memory_ids" if deleting else "text")
            result = await threaded(operation, value, user_id)
            return {"deleted": result} if deleting else {"result": result}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except RuntimeError:
            raise HTTPException(502, "记忆操作失败，请刷新确认结果") from None
        finally:
            work_gate.release(owner)

    @routes.get("")
    async def listing(project_id: str = ""):
        user_id = target(project_id)
        if not enabled():
            return {"enabled": False, "memories": []}
        try:
            items = await threaded(memory.list_, user_id)
        except RuntimeError:
            raise HTTPException(502, "记忆列表读取失败") from None
        return {
            "enabled": True,
            "memories": [
                {
                    "id": item["id"],
                    "memory": item.get("memory", ""),
                    "scope": "project" if project_id else "global",
                }
                for item in items
            ],
        }

    @routes.post("")
    async def save(body: dict = Body(...)):
        return await mutate(body)

    @routes.delete("")
    async def delete(body: dict = Body(...)):
        return await mutate(body, deleting=True)

    return routes
