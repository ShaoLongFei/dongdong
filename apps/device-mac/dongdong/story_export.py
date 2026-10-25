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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from re import search

ROOT = Path(__file__).resolve().parents[3]
FORMATS = {"png", "pdf", "mp4"}


def bundled_ffmpeg() -> Path:
    arch = "darwin-arm64" if platform.machine() == "arm64" else "darwin-x86_64"
    path = ROOT / "tools" / "ffmpeg" / arch / "ffmpeg"
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError(f"项目内 FFmpeg 不可用: {path}；未找到可执行文件，不能导出 MP4")
    expected = path.with_name("SHA256")
    if expected.exists():
        fields = expected.read_text(encoding="utf-8").split()
        expected_digest = fields[0] if fields else ""
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest:
            raise RuntimeError("项目内 FFmpeg SHA-256 校验失败")
    return path


def _svg_dimensions(svg_path: Path) -> tuple[int, int]:
    """读取 SVG 画布尺寸，长图导出需要保留原始高度。"""
    source = svg_path.read_text(encoding="utf-8")
    root = search(r"<svg\b([^>]*)>", source)
    if not root:
        raise RuntimeError(f"SVG 根元素不存在: {svg_path.name}")
    attributes = root.group(1)

    def dimension(name: str) -> float | None:
        match = search(rf'\b{name}="([0-9]+(?:\.[0-9]+)?)', attributes)
        return float(match.group(1)) if match else None

    width = dimension("width")
    height = dimension("height")
    if width is None or height is None:
        view_box = search(r'\bviewBox="([^\"]+)"', attributes)
        if view_box:
            values = view_box.group(1).replace(",", " ").split()
            if len(values) == 4:
                width = width or float(values[2])
                height = height or float(values[3])
    if not width or not height:
        raise RuntimeError(f"SVG 画布尺寸无效: {svg_path.name}")
    return max(1, round(width)), max(1, round(height))


def _render(svg_path: Path, output: Path, kind: str) -> None:
    width, height = _svg_dimensions(svg_path)
    errors = []
    try:
        import cairosvg

        converter = cairosvg.svg2png if kind == "png" else cairosvg.svg2pdf
        converter(url=str(svg_path), write_to=str(output), output_width=width, output_height=height)
        return
    except ModuleNotFoundError as exc:
        errors.append(f"CairoSVG 不可用: {exc}")
    except Exception as exc:
        errors.append(f"CairoSVG 转换失败: {exc}")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": width, "height": min(height, 720)}, device_scale_factor=1
            )
            page.set_content(svg_path.read_text(encoding="utf-8"), wait_until="load")
            page.add_style_tag(content="html, body { margin: 0; padding: 0; }")
            if kind == "png":
                page.screenshot(path=str(output), type="png", full_page=True)
            else:
                page.pdf(
                    path=str(output),
                    width=f"{width}px",
                    height=f"{height}px",
                    print_background=True,
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
            browser.close()
            return
    except ModuleNotFoundError as exc:
        errors.append(f"Playwright 不可用: {exc}")
    except Exception as exc:
        errors.append(f"Playwright 转换失败: {exc}")

    if platform.system() != "Darwin":
        raise RuntimeError(f"缺少 SVG 转 {kind.upper()} 能力；" + "；".join(errors))
    qlmanage = Path("/usr/bin/qlmanage")
    sips = Path("/usr/bin/sips")
    native_available = True
    if not (qlmanage.is_file() and os.access(qlmanage, os.X_OK)):
        native_available = False
        errors.append(f"macOS Quick Look 不可用: {qlmanage}")
    if not (sips.is_file() and os.access(sips, os.X_OK)):
        native_available = False
        errors.append(f"macOS sips 不可用: {sips}")
    if not native_available:
        raise RuntimeError(f"缺少 SVG 转 {kind.upper()} 能力；" + "；".join(errors))

    try:
        with tempfile.TemporaryDirectory(prefix="dongdong-quicklook-") as temp:
            temp_dir = Path(temp)
            thumbnail_source = temp_dir / svg_path.name
            thumbnail_source.write_bytes(svg_path.read_bytes())
            result = subprocess.run(
                [
                    str(qlmanage),
                    "-t",
                    "-s",
                    str(max(width, height)),
                    "-o",
                    str(temp_dir),
                    str(thumbnail_source),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            thumbnail = temp_dir / f"{thumbnail_source.name}.png"
            if result.returncode or not thumbnail.is_file():
                detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
                raise RuntimeError(f"Quick Look 生成缩略图失败: {' '.join(detail) or '未生成 PNG'}")
            png_path = output if kind == "png" else temp_dir / "scene.png"
            result = subprocess.run(
                [
                    str(sips),
                    "-z",
                    str(height),
                    str(width),
                    str(thumbnail),
                    "--out",
                    str(png_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode or not png_path.is_file():
                detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
                raise RuntimeError(f"sips 调整 PNG 尺寸失败: {' '.join(detail) or '未生成 PNG'}")
            if kind == "pdf":
                result = subprocess.run(
                    [str(sips), "-s", "format", "pdf", str(png_path), "--out", str(output)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if result.returncode or not output.is_file():
                    detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
                    raise RuntimeError(f"sips 转 PDF 失败: {' '.join(detail) or '未生成 PDF'}")
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        raise RuntimeError(
            f"SVG 转 {kind.upper()} 失败；" + "；".join(errors) + f"；原生回退: {exc}"
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


def _scene_duration_ms(story_dir: Path, scene: Path) -> int:
    """读取场景时长；优先使用 scene.durationMs，否则取元素结束时间。"""
    try:
        data = json.loads((story_dir / f"{scene.stem}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    if isinstance(data, dict):
        try:
            duration = int(data.get("durationMs", 0))
        except (TypeError, ValueError):
            duration = 0
        if duration > 0:
            return duration
        elements = data.get("elements", [])
        if isinstance(elements, list):
            end_ms = 0
            for element in elements:
                if not isinstance(element, dict):
                    continue
                try:
                    start_ms = max(0, int(element.get("startMs", 0)))
                    element_duration = max(0, int(element.get("durationMs", 0)))
                except (TypeError, ValueError):
                    continue
                end_ms = max(end_ms, start_ms + element_duration)
            if end_ms > 0:
                return end_ms
    raise RuntimeError(f"场景缺少有效时长: {scene.name}")


def _storyboard_svg(scenes: list[Path], output: Path) -> None:
    """把所有幕拼成纵向长图，供 PNG/PDF 共用同一份完整内容。"""
    panel_height = 776
    total_height = panel_height * len(scenes)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 {total_height}" '
        f'width="1280" height="{total_height}">',
        f'<rect width="1280" height="{total_height}" fill="#F8F5EF"/>',
    ]
    for index, scene in enumerate(scenes, start=1):
        source = scene.read_text(encoding="utf-8")
        start = source.find(">")
        end = source.rfind("</svg>")
        if start < 0 or end <= start:
            raise RuntimeError(f"SVG 场景格式无效: {scene.name}")
        y = (index - 1) * panel_height
        parts.append(
            f'<text x="40" y="{y + 32}" fill="#5B5550" font-family="sans-serif" '
            f'font-size="22">Scene {index}</text>'
        )
        parts.append(
            f'<svg x="0" y="{y + 56}" width="1280" height="720" viewBox="0 0 1280 720">'
            f"{source[start + 1 : end]}</svg>"
        )
        if index < len(scenes):
            parts.append(f'<path d="M40 {y + panel_height - 1} H1240" stroke="#DDD6CA"/>')
    parts.append("</svg>")
    output.write_text("".join(parts), encoding="utf-8")
    output.chmod(0o600)


def _export_mp4(story_dir: Path, output: Path, scenes: list[Path]) -> None:
    ffmpeg = bundled_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="dongdong-export-") as temp:
        temp_dir = Path(temp)
        frames = []
        durations_ms = []
        for index, scene in enumerate(scenes):
            frame = temp_dir / f"scene-{index:04d}.png"
            _render(scene, frame, "png")
            frames.append(frame)
            durations_ms.append(_scene_duration_ms(story_dir, scene))
        total_duration = sum(durations_ms) / 1000
        command = [
            str(ffmpeg),
            "-y",
        ]
        for frame, duration_ms in zip(frames, durations_ms):
            command.extend(
                [
                    "-loop",
                    "1",
                    "-framerate",
                    "30",
                    "-t",
                    f"{duration_ms / 1000:.6f}",
                    "-i",
                    str(frame),
                ]
            )
        video_inputs = "".join(f"[{index}:v]" for index in range(len(frames)))
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-filter_complex",
                f"{video_inputs}concat=n={len(frames)}:v=1:a=0[v0];"
                "[v0]scale=1280:720:force_original_aspect_ratio=disable[v]",
                "-map",
                "[v]",
                "-map",
                f"{len(frames)}:a:0",
                "-t",
                f"{total_duration:.6f}",
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                str(output),
            ]
        )
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
        with tempfile.TemporaryDirectory(prefix="dongdong-storyboard-") as temp:
            storyboard = Path(temp) / "storyboard.svg"
            _storyboard_svg(scenes, storyboard)
            _render(storyboard, output, fmt)
    output.chmod(0o600)
    return output


class ExportManager:
    def __init__(self, store):
        self.store = store
        self.jobs = {}
        self.lock = threading.Lock()
        # 导出可能涉及 SVG 渲染和 FFmpeg，限制并发避免每次请求创建无限线程。
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dongdong-export")

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
        self.executor.submit(self._run, job_id)
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
