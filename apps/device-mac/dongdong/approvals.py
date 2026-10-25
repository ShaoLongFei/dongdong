"""进程内的一次性工具审批；授权只属于当前 run 的当前调用。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


class Approvals:
    def __init__(self) -> None:
        self._waiting = {}

    async def request(
        self, run_id: str, call_id: str, name: str, args: dict, on_event: Callable
    ) -> bool:
        key = (run_id, call_id)
        if key in self._waiting:
            raise ValueError("该工具调用已在等待审批")
        future = asyncio.get_running_loop().create_future()
        self._waiting[key] = (
            future,
            {"run_id": run_id, "id": call_id, "name": name, "arguments": args},
        )
        status = "cancelled"
        try:
            on_event(
                "approval", {"id": call_id, "name": name, "arguments": args, "status": "pending"}
            )
            approved = await future
            status = "approved" if approved else "rejected"
            return approved
        finally:
            self._waiting.pop(key, None)
            on_event("approval_result", {"id": call_id, "status": status})

    def pending(self) -> list[dict]:
        return [dict(data) for future, data in self._waiting.values() if not future.done()]

    def resolve(self, run_id: str, call_id: str, approved: bool) -> bool:
        entry = self._waiting.get((run_id, call_id))
        if entry is None or entry[0].done():
            return False
        entry[0].set_result(approved)
        return True


class DecisionIn(BaseModel):
    approved: bool = Field(strict=True)


def router(manager: Approvals) -> APIRouter:
    routes = APIRouter(prefix="/api/approvals")

    @routes.get("")
    async def pending():
        return {"approvals": manager.pending()}

    @routes.post("/{run_id}/{call_id}")
    async def resolve(run_id: str, call_id: str, body: DecisionIn):
        if not manager.resolve(run_id, call_id, body.approved):
            raise HTTPException(409, "审批不存在或已处理")
        return {"ok": True}

    return routes
