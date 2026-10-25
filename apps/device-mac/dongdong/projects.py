"""项目、会话归属和归档 API。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from . import work_gate
from .store import now


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    path: str

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("项目名称不能为空")
        return value.strip()


class ProjectNameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    _nonblank = field_validator("name")(ProjectIn.nonblank.__func__)


class CurrentProjectIn(BaseModel):
    project_id: str | None = None


class SessionIn(BaseModel):
    title: str = Field(default="新会话", max_length=120)
    project_id: str | None = None


class SessionPatch(BaseModel):
    project_id: str | None = None
    archived: bool | None = None


def router(state) -> APIRouter:
    routes = APIRouter(prefix="/api")
    selecting = asyncio.Lock()

    @routes.post("/folders/select")
    async def select_folder():
        if sys.platform != "darwin":
            raise HTTPException(503, "系统文件夹选择目前仅支持 macOS")
        if selecting.locked():
            raise HTTPException(409, "文件夹选择窗口已经打开")
        async with selecting:
            # 固定脚本直接调用系统选择器，不将路径或请求参数拼入 AppleScript。
            script = """tell current application
activate
try
return POSIX path of (choose folder with prompt "选择项目文件夹" multiple selections allowed false)
on error number -128
return ""
end try
end tell"""
            process = await asyncio.create_subprocess_exec(
                "/usr/bin/osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(), timeout=120)
            except TimeoutError:
                raise HTTPException(408, "选择文件夹超时，请重新打开") from None
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            if process.returncode:
                raise HTTPException(502, "无法打开系统文件夹选择窗口，请重试")
            selected = output.decode().rstrip("\r\n")
            if not selected:
                return {"path": None}
            path = Path(selected).resolve()
            if not path.is_dir():
                raise HTTPException(400, "请选择已存在的文件夹")
            return {"path": str(path)}

    def gate(owner: str):
        if state.meeting.db.active():
            raise HTTPException(409, "请先退出代码修改模式再修改项目或会话")
        if not work_gate.acquire(owner):
            raise HTTPException(409, "任务运行中，请结束后再修改项目或会话")

    def project(project_id: str | None):
        if project_id is None:
            return None
        item = state.store.get_project(project_id)
        if not item:
            raise HTTPException(404, "项目不存在")
        return item

    @routes.get("/projects")
    async def list_projects():
        return {"projects": state.store.list_projects(), "current_project": state.current_project}

    @routes.post("/projects")
    async def create_project(body: ProjectIn):
        try:
            path = Path(body.path).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(400, "项目目录无效") from None
        if not path.is_dir():
            raise HTTPException(400, f"项目目录不存在: {path}")
        gate("project-settings")
        try:
            try:
                pid = state.store.create_project(body.name.strip(), str(path))
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    raise HTTPException(409, "这个目录已经绑定了项目") from None
                raise
            return state.store.get_project(pid)
        finally:
            work_gate.release("project-settings")

    @routes.patch("/projects/{project_id}")
    async def rename_project(project_id: str, body: ProjectNameIn):
        project(project_id)
        gate("project-settings")
        try:
            state.store.rename_project(project_id, body.name.strip())
            return state.store.get_project(project_id)
        finally:
            work_gate.release("project-settings")

    @routes.put("/projects/current")
    async def set_current(body: CurrentProjectIn):
        item = project(body.project_id)
        gate("project-settings")
        try:
            try:
                state.set_project(body.project_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            return {"current_project": item["id"] if item else None}
        finally:
            work_gate.release("project-settings")

    @routes.get("/sessions")
    async def list_sessions(
        project_id: str | None = Query(default=None),
        archived: bool = False,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        if project_id:
            project(project_id)
        rows = state.store.list_sessions(project_id, archived, limit, offset)
        return {
            "sessions": rows,
            "has_more": len(rows) == limit,
            "project_id": project_id,
            "archived": archived,
        }

    @routes.post("/sessions")
    async def create_session(body: SessionIn | None = Body(default=None)):
        pid = (
            body.project_id
            if body and "project_id" in body.model_fields_set
            else state.current_project
        )
        project(pid)
        gate("session-settings")
        try:
            sid = state.new_session((body.title if body else "新会话"), "web", pid)
        finally:
            work_gate.release("session-settings")
        return state.store.get_session(sid)

    @routes.patch("/sessions/{session_id}")
    async def update_session(session_id: str, body: SessionPatch):
        session = state.store.get_session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        project(body.project_id)
        gate("session-settings")
        try:
            values = {}
            if "project_id" in body.model_fields_set:
                values["project_id"] = body.project_id
            if body.archived is not None:
                values["archived_at"] = now() if body.archived else None
            try:
                state.store.update_session(session_id, values)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            state.forget_session(session_id)
            return state.store.get_session(session_id)
        finally:
            work_gate.release("session-settings")

    return routes
