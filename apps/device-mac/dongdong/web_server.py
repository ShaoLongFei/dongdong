"""本地 Web 控制台：会话、SSE 事件流、停止和设置。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (
    approvals,
    config,
    mcp_routes,
    meeting_routes,
    memory_routes,
    projects,
    skills,
    tools,
    work_gate,
)
from . import state as device_state
from .voice import Voice
from .web_runtime import WebAgent, WebState

STATIC = Path(__file__).with_name("static")


class MessageIn(BaseModel):
    text: str


class VoiceIn(BaseModel):
    enabled: bool = Field(strict=True)


class SettingsIn(BaseModel):
    code_backend: str | None = Field(default=None, pattern="^(builtin|cli)$")
    workspace: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=65536)
    tool_policy: str | None = Field(default=None, pattern="^(auto|ask)$")


def create_app(cfg: dict) -> FastAPI:
    state = WebState(cfg)
    voice = Voice(cfg, state.voice_text)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tools.bind_service(WebAgent(state))
        try:
            yield
        finally:
            await voice.set_enabled(False)
            await asyncio.to_thread(tools.close_service)
            await state.close()

    app = FastAPI(title="咚咚", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    app.include_router(meeting_routes.router(state.meeting))
    app.state.web_state = state
    app.include_router(skills.router(cfg))
    app.include_router(mcp_routes.router(cfg))
    app.include_router(approvals.router(state.approvals))
    app.include_router(projects.router(state))
    app.include_router(memory_routes.router(state))

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "只接受同源请求"}, status_code=403)
        return await call_next(request)

    @app.get("/api/state")
    async def api_state():
        active = state.store.get_run(state._run_id)
        if active and active["status"] != "running" and not state.busy:
            active = None
        return {
            "version": cfg.get("version", ""),
            "meeting_session": (state.meeting.db.active() or {}).get("sid", ""),
            "workspace": str(state.project_workspace(state.current_project)),
            "current_project": state.current_project,
            "model_ready": state.model is not None,
            "error": state.error,
            "backend": cfg.get("code_backend", "cli"),
            "active_run": state._run_id if active else "",
            "task_session": active["session_id"] if active else state.task_session,
            "busy": bool(work_gate.current()),
            "voice": device_state.snapshot(cfg),
            "voice_session": state.voice_session,
            "voice_enabled": voice.enabled,
            "voice_error": voice.error,
        }

    @app.put("/api/voice")
    async def api_voice(body: VoiceIn):
        await voice.set_enabled(body.enabled)
        return {"enabled": voice.enabled}

    @app.get("/api/sessions/{sid}")
    async def api_session(sid: str):
        session = state.store.get_session(sid)
        if not session:
            raise HTTPException(404, "会话不存在")
        runs = []
        for run in state.store.list_runs(sid, limit=-1):
            runs.append({**run, **state.manager.status(run["id"])})
        return {
            **session,
            "meeting": state.meeting.db.get(sid),
            "project_path": session["project_path"] or str(state.workspace),
            "messages": state.store.messages(sid),
            "runs": runs,
        }

    @app.post("/api/sessions/{sid}/messages")
    async def api_message(sid: str, body: MessageIn):
        session = state.store.get_session(sid)
        if not session:
            raise HTTPException(404, "会话不存在")
        if session["archived_at"]:
            raise HTTPException(409, "会话已归档，请先恢复")
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "消息不能为空")
        try:
            meeting = state.meeting.db.get(sid)
            if meeting and meeting["enabled"]:
                return await state.meeting.message(sid, text)
            run_id = await state.start(sid, text)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"run_id": run_id}

    @app.get("/api/runs/{rid}")
    async def api_run(rid: str, include_events: bool = False):
        run = state.store.get_run(rid)
        if not run:
            raise HTTPException(404, "run 不存在")
        view = state.manager.status(rid)
        return {**view, "events": state.store.events(rid)} if include_events else {**run, **view}

    @app.get("/api/runs/{rid}/events")
    async def api_events(rid: str, request: Request, since: int = 0):
        if not state.store.get_run(rid):
            raise HTTPException(404, "run 不存在")

        async def stream():
            cursor = since
            while True:
                events = state.store.events(rid, cursor)
                for event in events:
                    cursor = event["id"]
                    payload = {"id": cursor, "type": event["type"], "data": event["data"]}
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if await request.is_disconnected():
                    return
                run = state.store.get_run(rid)
                if run and run["status"] != "running" and not events and state._run_id != rid:
                    yield "event: end\ndata: {}\n\n"
                    return
                await asyncio.sleep(0.2)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/runs/{rid}/cancel")
    async def api_cancel(rid: str):
        if not state.cancel(rid):
            raise HTTPException(409, "任务不在运行")
        return {"ok": True}

    @app.get("/api/settings")
    async def api_settings():
        model = cfg["model"]
        return {
            "workspace": cfg["workspace"],
            "base_url": model["base_url"],
            "model": model["model"],
            "temperature": model["temperature"],
            "max_tokens": model["max_tokens"],
            "has_key": bool(config.expand(model["api_key"])),
            "code_backend": cfg.get("code_backend", "cli"),
            "tool_policy": cfg.get("tool_policy", "auto"),
        }

    @app.put("/api/settings")
    async def api_save_settings(body: SettingsIn):
        if not work_gate.acquire("settings"):
            raise HTTPException(409, "任务在跑，先停止再改设置")
        try:
            return save_settings(body)
        finally:
            work_gate.release("settings")

    def save_settings(body: SettingsIn):
        if state.meeting.db.active():
            raise HTTPException(409, "请先退出代码修改模式再更改设置")
        if body.workspace is not None:
            workspace = Path(body.workspace).expanduser().resolve()
            if not workspace.is_dir():
                raise HTTPException(400, f"工作区不存在: {workspace}")
            cfg["workspace"] = str(workspace)
            state.workspace = workspace
        if body.code_backend is not None:
            cfg["code_backend"] = body.code_backend
        if body.tool_policy is not None:
            cfg["tool_policy"] = body.tool_policy
        model = cfg["model"]
        if body.base_url:
            model["base_url"] = body.base_url.rstrip("/")
        if body.model:
            model["model"] = body.model
        if body.api_key:
            model["api_key"] = body.api_key
        if body.temperature is not None:
            model["temperature"] = body.temperature
        if body.max_tokens is not None:
            model["max_tokens"] = body.max_tokens
        config.save(cfg)
        state.reload()
        return {"ok": True, "error": state.error}

    if (STATIC / "index.html").exists():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    else:

        @app.get("/")
        async def build_hint():
            return JSONResponse(
                {"detail": "请先在 apps/web 运行 npm ci && npm run build"}, status_code=503
            )

    return app


def serve(cfg: dict, host: str | None = None, port: int | None = None) -> None:
    host = host or cfg["web"]["host"]
    port = port or int(cfg["web"]["port"])
    _local_host(host)
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="info")


def _local_host(host: str) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Web 控制台当前只允许本机访问，请使用 127.0.0.1")
