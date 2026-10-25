"""把 Runtime 包成后台 job，供灵矽 MCP 的同步 tools/call 使用。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from . import config, work_gate
from .llm import ModelConfig, model_from_config, stream_chat
from .runtime import Runtime
from .store import Store

MAX_OUTPUT = 12_000


class Job:
    def __init__(self, instruction: str, workspace: Path, session_id: str):
        self.id = uuid.uuid4().hex[:6]
        self.instruction = instruction
        self.workspace = workspace
        self.session_id = session_id
        self.status = "running"
        self.output = ""
        self.answer = ""
        self.error = ""
        self.started = time.monotonic()
        self.future: concurrent.futures.Future | None = None
        self.task: asyncio.Task | None = None
        self.finished = threading.Event()
        self._lock = threading.Lock()

    def event(self, kind: str, data: dict) -> None:
        with self._lock:
            if kind == "text":
                self.output += data.get("delta", "")
            elif kind == "tool_call":
                self.output += f"\n[工具] {data.get('name')} {str(data.get('arguments'))[:300]}\n"
            elif kind == "tool_result":
                self.output += f"\n[结果] {str(data.get('result'))[:500]}\n"
            elif kind == "error":
                self.error = str(data.get("message") or "")
            elif kind == "done":
                self.output += "\n"
            if len(self.output) > MAX_OUTPUT:
                self.output = self.output[-MAX_OUTPUT:]

    def summary(self) -> str:
        with self._lock:
            output = self.output.strip()
        if self.status == "running":
            preview = output[-1200:] or "(还没有输出)"
            return f"任务 {self.id} 还在跑（{int(time.monotonic() - self.started)}s）。预览：\n{preview}"
        if self.status == "stopped":
            return f"任务 {self.id} 已停止。"
        if self.status == "error":
            return f"任务 {self.id} 失败：{self.error or '未知错误'}"
        answer = (self.answer.strip() or output)[-2000:] or "(无输出)"
        return f"任务 {self.id} 完成：\n{answer}"


class AgentService:
    """一个进程内单任务队列；Runtime 跑在专用事件循环线程里。"""

    def __init__(self, cfg: dict, *, stream_fn: Callable = stream_chat):
        self.model: ModelConfig = model_from_config(cfg)
        self.workspace = Path(cfg["workspace"]).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError(f"工作区不存在: {self.workspace}")
        self.max_steps = int(cfg["runtime"]["max_steps"])
        self.history_messages = int(cfg["runtime"]["history_messages"])
        self.stream_fn = stream_fn
        self.store = Store(config.HOME / "dongdong.db")
        if not work_gate.current():
            self.store.stop_orphan_runs()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name="dongdong-agent", daemon=True)
        self._thread.start()
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._last = ""
        self._active: Job | None = None
        self._closed = False

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _workdir(self, cwd: str | None) -> Path:
        if not cwd:
            return self.workspace
        path = Path(str(cwd)).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        path = path.resolve()
        if not path.is_dir():
            raise ValueError(f"目录不存在: {path}")
        if not path.is_relative_to(self.workspace):
            raise ValueError(f"目录必须在工作区内: {self.workspace}")
        return path

    async def _run(self, job: Job, instruction: str) -> None:
        try:
            job.task = asyncio.current_task()
            if job.status == "stopped":
                raise asyncio.CancelledError
            runtime = Runtime(
                self.store,
                self.model,
                job.workspace,
                max_steps=self.max_steps,
                history_messages=self.history_messages,
                stream_fn=self.stream_fn,
            )
            job.answer = await runtime.run(job.session_id, instruction, on_event=job.event)
            job.status = "done"
        except asyncio.CancelledError:
            job.status = "stopped"
            raise
        except Exception as exc:
            job.status = "error"
            job.error = str(exc)
            raise
        finally:
            work_gate.release(job.id)
            job.finished.set()

    def start(self, instruction: str, cwd: str | None = None, wait_seconds: int = 8) -> str:
        instruction = (instruction or "").strip()
        if not instruction:
            raise ValueError("instruction 不能为空")
        workdir = self._workdir(cwd)
        wait = max(1, min(int(wait_seconds or 8), 30))
        with self._lock:
            if self._closed:
                raise RuntimeError("agent service 已关闭")
            if self._active and not self._active.finished.is_set():
                return f"上一个任务 {self._active.id} 还在跑，先等它结束或说「停」。"
            job = Job(instruction, workdir, "")
            if not work_gate.acquire(job.id):
                return "另一个任务还在跑，先等它结束或说「停」。"
            try:
                job.session_id = self.store.create_session("语音任务", "linx")
            except Exception:
                work_gate.release(job.id)
                raise
            self._jobs[job.id] = job
            self._last = job.id
            self._active = job
            job.future = asyncio.run_coroutine_threadsafe(self._run(job, instruction), self._loop)
        concurrent.futures.wait([job.future], timeout=wait)
        return job.summary()

    def result(self, job_id: str = "") -> str:
        job = self._find(job_id)
        if job is None:
            if job_id:
                return f"找不到任务 {job_id}（只记本次运行）。"
            return "还没有跑过任务。"
        return job.summary()

    def stop(self, job_id: str = "") -> str:
        job = self._find(job_id)
        if job is None:
            return "没有可停的任务。"
        if job.status != "running":
            return f"任务 {job.id} 已经结束。"
        job.status = "stopped"
        self._loop.call_soon_threadsafe(lambda: job.task.cancel() if job.task else None)
        if not job.finished.wait(2):
            return f"已请求停止任务 {job.id}（还在收尾）。"
        return f"已停止任务 {job.id}。"

    def snapshot(self) -> dict | None:
        with self._lock:
            job = self._active
        if job is None:
            return None
        with job._lock:
            output = job.output[-4000:]
        return {
            "id": job.id,
            "agent": "builtin",
            "cwd": str(job.workspace),
            "running": job.status == "running",
            "elapsed": int(time.monotonic() - job.started),
            "exit_code": None,
            "status": job.status,
            "output": output,
        }

    def current(self) -> Job | None:
        with self._lock:
            job = self._active
        return job if job and job.status == "running" else None

    def _find(self, job_id: str) -> Job | None:
        with self._lock:
            jid = job_id or self._last
            return self._jobs.get(jid)

    async def _shutdown(self) -> None:
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loop.stop()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for job in self._jobs.values():
                if job.status == "running":
                    job.status = "stopped"
        self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._shutdown()))
        self._thread.join(timeout=3)
        if not self._loop.is_running() and not self._loop.is_closed():
            self._loop.close()
        self.store.close()
