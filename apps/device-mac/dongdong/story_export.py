"""故事导出任务；只使用项目内的 FFmpeg，不回退到系统 PATH。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FORMATS = {"png", "pdf", "mp4"}


def bundled_ffmpeg() -> Path:
    arch = "darwin-arm64" if platform.machine() == "arm64" else "darwin-x86_64"
    path = ROOT / "tools" / "ffmpeg" / arch / "ffmpeg"
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError(f"项目内 FFmpeg 不可用: {path}；未找到可执行文件，不能导出 MP4")
    expected = path.with_name("SHA256")
    if (
        expected.exists()
        and hashlib.sha256(path.read_bytes()).hexdigest()
        != expected.read_text(encoding="utf-8").strip()
    ):
        raise RuntimeError("项目内 FFmpeg SHA-256 校验失败")
    return path


def _render(svg_path: Path, output: Path, kind: str) -> None:
    try:
        import cairosvg

        converter = cairosvg.svg2png if kind == "png" else cairosvg.svg2pdf
        converter(url=str(svg_path), write_to=str(output), output_width=1280, output_height=720)
        return
    except ModuleNotFoundError:
        pass
    except Exception as exc:
        raise RuntimeError(f"SVG 转 {kind.upper()} 失败: {exc}") from exc
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
            page.set_content(svg_path.read_text(encoding="utf-8"), wait_until="load")
            if kind == "png":
                page.screenshot(path=str(output), type="png")
            else:
                page.pdf(path=str(output), width="1280px", height="720px", print_background=True)
            browser.close()
    except Exception as exc:
        raise RuntimeError(
            f"缺少 SVG 转 {kind.upper()} 能力：请安装 cairosvg 或 Playwright 浏览器"
        ) from exc


def _scene_paths(story_dir: Path) -> list[Path]:
    try:
        manifest = json.loads((story_dir / "manifest.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError("故事 manifest 不存在或已损坏") from exc
    paths = []
    for scene_id in manifest.get("scenes", []):
        if not isinstance(scene_id, str) or "/" in scene_id or "\\" in scene_id or ".." in scene_id:
            continue
        path = story_dir / f"{scene_id}.svg"
        if path.is_file() and not path.is_symlink():
            paths.append(path)
    if not paths:
        raise RuntimeError("故事没有可导出的 SVG 场景")
    return paths


def _export_mp4(story_dir: Path, output: Path, scenes: list[Path]) -> None:
    ffmpeg = bundled_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="dongdong-export-") as temp:
        temp_dir = Path(temp)
        frames = []
        for index, scene in enumerate(scenes):
            frame = temp_dir / f"scene-{index:04d}.png"
            _render(scene, frame, "png")
            frames.append(frame)
        concat = temp_dir / "frames.txt"
        concat.write_text(
            "".join(f"file '{frame}'\nduration 2\n" for frame in frames) + f"file '{frames[-1]}'\n",
            encoding="utf-8",
        )
        command = [
            str(ffmpeg),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
            raise RuntimeError(f"项目内 FFmpeg 导出 MP4 失败: {' '.join(detail)}")


def export_story(story_dir: Path, fmt: str) -> Path:
    if fmt not in FORMATS:
        raise ValueError("导出格式必须是 png、pdf 或 mp4")
    story_dir = Path(story_dir).resolve()
    if not story_dir.is_dir() or story_dir.is_symlink():
        raise ValueError("故事目录不存在")
    scenes = _scene_paths(story_dir)
    output_dir = story_dir / "exports"
    output_dir.mkdir(mode=0o700, exist_ok=True)
    output = output_dir / f"story.{fmt}"
    if fmt == "mp4":
        _export_mp4(story_dir, output, scenes)
    else:
        _render(scenes[0], output, fmt)
    output.chmod(0o600)
    return output


class ExportManager:
    def __init__(self, store):
        self.store = store
        self.jobs = {}
        self.lock = threading.Lock()

    def submit(self, story_id: str, fmt: str) -> str:
        if fmt not in FORMATS:
            raise ValueError("导出格式必须是 png、pdf 或 mp4")
        story_dir = self.store._validate(story_id)
        if not story_dir.is_dir():
            raise ValueError("故事不存在")
        job_id = uuid.uuid4().hex[:12]
        with self.lock:
            self.jobs[job_id] = {
                "id": job_id,
                "storyId": story_id,
                "format": fmt,
                "status": "running",
                "progress": 0,
            }
        threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
        return job_id

    def _run(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job["progress"] = 10
        try:
            story_dir = self.store._validate(job["storyId"])
            with self.lock:
                job["progress"] = 25
            output = export_story(story_dir, job["format"])
            with self.lock:
                job.update(
                    status="done",
                    progress=100,
                    outputPath=str(output),
                    downloadUrl=f"/api/stories/{job['storyId']}/exports/{job['id']}/download",
                )
        except Exception as exc:
            with self.lock:
                job.update(status="error", progress=100, error=str(exc))

    def status(self, job_id: str) -> dict:
        with self.lock:
            return dict(self.jobs.get(job_id) or {"id": job_id, "status": "missing"})
