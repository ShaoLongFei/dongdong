"""把一句话交给 codex / claude / deepseek，收输出。

不实现 agent loop —— CLI 自带。这里只管：拼参数、限目录、管超时、留输出。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger("dongdong.agent")

_JOBS: dict[str, Job] = {}
_LAST = ""


class Job:
    def __init__(self, agent: str, cwd: Path, argv: list[str], env: dict):
        self.id = uuid.uuid4().hex[:6]
        self.agent = agent
        self.cwd = cwd
        self.started = time.monotonic()
        self.output = ""
        self.done = threading.Event()
        log.info("任务 %s 启动: %s (cwd=%s)", self.id, " ".join(argv[:5]), cwd)
        self.proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.output += line
        self.proc.wait()
        self.done.set()
        log.info("任务 %s 结束，退出码 %s", self.id, self.proc.returncode)

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        if self.running:
            self.proc.kill()

    def tail(self, n: int = 1500) -> str:
        return self.output.strip()[-n:] or "(无输出)"


def run(
    cfg: dict,
    instruction: str,
    name: str | None = None,
    cwd: str | None = None,
    wait_seconds: int | None = None,
) -> str:
    global _LAST
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("instruction 不能为空")
    name = name or cfg["agent"]
    preset = cfg["agents"].get(name)
    if not preset:
        raise ValueError(f"未知 agent: {name}，可选 {', '.join(cfg['agents'])}")
    missing = [var for var in required_env(preset) if not os.environ.get(var)]
    if missing:
        raise ValueError(f"agent {name} 缺少环境变量: {', '.join(missing)}")
    wait_seconds = wait_seconds or int(preset.get("timeout_s") or 120)
    busy = current()
    if busy:
        return f"上一个任务 {busy.id}（{busy.agent}）还在跑，先说「停」或者等它结束。"
    workdir = resolve_dir(cfg, cwd)
    argv = [
        p.replace("{cwd}", str(workdir)).replace("{prompt}", instruction) for p in preset["cmd"]
    ]
    env = {**os.environ, **_expand(preset.get("env") or {})}
    job = Job(name, workdir, argv, env)
    _JOBS[job.id] = job
    _LAST = job.id
    if job.done.wait(max(5, wait_seconds)):
        return f"{name} 退出码 {job.proc.returncode}，输出：\n{job.tail()}"
    return (
        f"任务 {job.id} 还在跑（{wait_seconds}s 没等到），先不干等。"
        "做完后用 self.code.result 查结果。"
    )


def result(job_id: str = "") -> str:
    jid = job_id or _LAST
    if not jid:
        return "还没有跑过任务。"
    job = _JOBS.get(jid)
    if not job:
        return f"找不到任务 {jid}（只记本次运行的）。"
    state = "还在跑" if job.running else f"已结束，退出码 {job.proc.returncode}"
    return f"任务 {job.id} {state}，输出：\n{job.tail()}"


def stop(job_id: str = "") -> str:
    jid = job_id or _LAST
    job = _JOBS.get(jid)
    if not job:
        return f"没有可停的任务 {jid}。"
    job.stop()
    return f"已停止任务 {job.id}（{job.agent}）。"


def snapshot() -> dict | None:
    """最近一个任务的快照，给面板看。"""
    job = _JOBS.get(_LAST)
    if not job:
        return None
    return {
        "id": job.id,
        "agent": job.agent,
        "cwd": str(job.cwd),
        "running": job.running,
        "elapsed": int(time.monotonic() - job.started),
        "exit_code": job.proc.returncode,
        "output": job.tail(4000),
    }


def current() -> Job | None:
    for job in _JOBS.values():
        if job.running:
            return job
    return None


def resolve_dir(cfg: dict, cwd: str | None) -> Path:
    """工作目录必须在允许的项目根目录下，越界直接拒绝。

    语音里说「dongdong 项目」时模型多半传相对目录，所以相对路径会先在每个项目根下找。
    """
    roots = [Path(os.path.expanduser(r)).resolve() for r in cfg["projects"]]
    raw = cwd or cfg.get("cwd") or ""
    if not raw:
        if not roots:
            raise ValueError("没有配置允许的项目目录")
        return roots[0]

    raw_path = Path(os.path.expanduser(raw))
    candidates = (
        [raw_path] if raw_path.is_absolute() else [r / raw_path for r in roots] + [raw_path]
    )
    for candidate in candidates:
        path = candidate.resolve()
        if not any(path == r or path.is_relative_to(r) for r in roots):
            continue
        if path.is_dir():
            return path
    raise ValueError(f"目录不在允许范围或不存在: {raw}（允许 {', '.join(cfg['projects'])}）")


ENV_REF = re.compile(r"\$\{(\w+)\}")


def _expand(env: dict) -> dict:
    """把 ${VAR} 换成当前环境变量。"""
    return {
        k: ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), str(v)) for k, v in env.items()
    }


def required_env(preset: dict) -> list[str]:
    """这个 agent 依赖哪些环境变量，缺了直接报清楚，别让 CLI 抛一堆看不懂的。"""
    return sorted({m for v in (preset.get("env") or {}).values() for m in ENV_REF.findall(str(v))})
