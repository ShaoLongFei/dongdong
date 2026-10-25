"""会议 HTTP 操作与隔离产物读取。"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import birdview


class ModeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)
    workspace: str = "."


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(generate|confirm|resume)$")
    proposal_id: str = ""
    hash: str = ""


class ShownIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hash: str


def router(controller) -> APIRouter:
    routes = APIRouter(prefix="/api/meetings")

    def writable(sid):
        session = controller.host.store.get_session(sid)
        if not session or session["archived_at"]:
            raise HTTPException(409, "会话不存在或已归档，请先恢复")

    @routes.get("/{sid}")
    async def view(sid: str):
        return controller.view(sid)

    @routes.put("/{sid}")
    async def mode(sid: str, body: ModeIn):
        writable(sid)
        try:
            if body.enabled:
                return controller.open(sid, body.workspace)
            controller.close(sid)
            return controller.view(sid)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @routes.post("/{sid}/actions")
    async def action(sid: str, body: ActionIn):
        writable(sid)
        try:
            return await controller.action(
                sid, body.action, "button", body.action, body.proposal_id, body.hash
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from None

    @routes.post("/{sid}/proposals/{pid}/shown")
    async def shown(sid: str, pid: str, body: ShownIn):
        writable(sid)
        try:
            root = birdview.directory(pid)
            artifact = (root / "map.html").resolve()
            if not artifact.is_relative_to(root) or not artifact.is_file():
                raise ValueError("地图产物不存在，不能确认展示")
            return {"receipt": controller.db.shown(sid, pid, body.hash)}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @routes.get("/{sid}/proposals/{pid}")
    async def proposal(sid: str, pid: str):
        item = controller.db.proposal(pid)
        if not item or item["sid"] != sid:
            raise HTTPException(404, "方案不存在")
        return item

    @routes.get("/{sid}/artifacts/{pid}/{name}")
    async def artifact(sid: str, pid: str, name: str):
        if name not in {"map.html", "diff.txt"}:
            raise HTTPException(404, "产物不存在")
        proposal = controller.db.proposal(pid)
        allowed = bool(proposal and proposal["sid"] == sid)
        if not allowed:
            for run in controller.host.store.list_runs(sid, limit=-1):
                allowed = any(
                    e["type"] in {"meeting_result", "meeting_activity"}
                    and e["data"].get("artifact") == pid
                    for e in controller.host.store.events(run["id"])
                )
                if allowed:
                    break
        if not allowed:
            raise HTTPException(404, "产物不存在")
        try:
            root = birdview.directory(pid)
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("产物不存在")
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        return FileResponse(
            path,
            media_type="text/html" if name.endswith("html") else "text/plain",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'",
            },
        )

    return routes
