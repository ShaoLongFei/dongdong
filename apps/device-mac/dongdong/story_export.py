"""故事导出任务；只使用项目内的 FFmpeg，不回退到系统 PATH。"""

from __future__ import annotations

import hashlib
import os
import platform
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def bundled_ffmpeg() -> Path:
    arch = "darwin-arm64" if platform.machine() == "arm64" else "darwin-x86_64"
    path = ROOT / "tools" / "ffmpeg" / arch / "ffmpeg"
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError(f"项目内 FFmpeg 不可用: {path}；请按文档放置并赋予执行权限")
    expected = path.with_name("SHA256")
    if (
        expected.exists()
        and hashlib.sha256(path.read_bytes()).hexdigest()
        != expected.read_text(encoding="utf-8").strip()
    ):
        raise RuntimeError("项目内 FFmpeg SHA-256 校验失败")
    return path


class ExportManager:
    def __init__(self, store):
        self.store = store
        self.jobs = {}
        self.lock = threading.Lock()

    def submit(self, story_id: str, fmt: str) -> str:
        if fmt not in {"png", "pdf", "mp4"}:
            raise ValueError("导出格式必须是 png、pdf 或 mp4")
        self.store._validate(story_id)
        job_id = uuid.uuid4().hex[:12]
        with self.lock:
            self.jobs[job_id] = {
                "id": job_id,
                "storyId": story_id,
                "format": fmt,
                "status": "running",
            }
        threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
        return job_id

    def _run(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
        try:
            if job["format"] != "mp4":
                raise RuntimeError("PNG/PDF 导出器尚未安装；源 SVG 已安全保存")
            raise RuntimeError(f"MP4 渲染管线待接入；项目内 FFmpeg: {bundled_ffmpeg()}")
        except Exception as exc:
            with self.lock:
                job.update(status="error", error=str(exc))

    def status(self, job_id: str) -> dict:
        with self.lock:
            return dict(self.jobs.get(job_id) or {"id": job_id, "status": "missing"})
