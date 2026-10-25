"""用项目自带 FFmpeg 生成低延迟 HLS，并交给 AirPlay 接收端播放。"""

from __future__ import annotations

import asyncio
import functools
import http.server
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("dongdong.airplay_stream")
_lock = threading.Lock()
_session: _Session | None = None


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass


class _Session:
    def __init__(self, cfg: dict, target: dict) -> None:
        self.cfg = cfg
        self.target = target
        self.root = Path(tempfile.mkdtemp(prefix="dongdong-airplay-", dir="/tmp"))
        self.httpd: http.server.ThreadingHTTPServer | None = None
        self.http_thread: threading.Thread | None = None
        self.http_host = ""
        self.ffmpeg: subprocess.Popen | None = None
        self.ffmpeg_log = self.root / "ffmpeg.log"
        self._ffmpeg_log_file = None
        self.atv_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.error = ""

    def start(self) -> None:
        self._start_http()
        self._ffmpeg_log_file = self.ffmpeg_log.open("wb")
        self.ffmpeg = subprocess.Popen(
            self._ffmpeg_cmd(), stdout=subprocess.DEVNULL, stderr=self._ffmpeg_log_file
        )
        self.atv_thread = threading.Thread(target=self._connect, name="airplay", daemon=True)
        self.atv_thread.start()

    def _start_http(self) -> None:
        handler = functools.partial(_QuietHandler, directory=str(self.root))
        # 绑定连接 Apple TV 的本机网卡，避免把临时流暴露到所有接口。
        self.http_host = _local_ip(self.target["address"])
        self.httpd = http.server.ThreadingHTTPServer((self.http_host, 0), handler)
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.http_thread.start()

    def _ffmpeg_cmd(self) -> list[str]:
        ffmpeg = _ffmpeg_path()
        playlist = self.root / "screen.m3u8"
        return [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "avfoundation",
            "-framerate",
            "30",
            "-pixel_format",
            "bgr0",
            "-capture_cursor",
            "1",
            "-i",
            "1:none",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "scale=1280:-2:flags=fast_bilinear",
            "-an",
            "-c:v",
            "h264_videotoolbox",
            "-b:v",
            "5M",
            "-maxrate",
            "6M",
            "-bufsize",
            "2M",
            "-g",
            "30",
            "-keyint_min",
            "30",
            "-f",
            "hls",
            "-hls_time",
            "1",
            "-hls_list_size",
            "3",
            "-hls_flags",
            "delete_segments+append_list+independent_segments",
            "-hls_segment_type",
            "mpegts",
            str(playlist),
        ]

    def _connect(self) -> None:
        try:
            deadline = time.monotonic() + 15
            playlist = self.root / "screen.m3u8"
            while not playlist.exists() and time.monotonic() < deadline:
                if self.stop_event.wait(0.2):
                    return
            if not playlist.exists():
                self.error = self._capture_error()
                return
            url = f"http://{self.http_host}:{self.httpd.server_port}/screen.m3u8"
            asyncio.run(self._play(url))
        except Exception:
            self.error = self._error_message()
            log.exception("AirPlay 流会话失败")

    def _capture_error(self) -> str:
        detail = ""
        try:
            lines = self.ffmpeg_log.read_text(errors="replace").strip().splitlines()
            for line in reversed(lines):
                lowered = line.lower()
                if "objc[" in lowered:
                    continue
                if any(
                    marker in lowered
                    for marker in (
                        "permission",
                        "not authorized",
                        "input/output error",
                        "failed",
                        "error",
                    )
                ):
                    if "configuration of video device failed" in lowered:
                        continue
                    detail = line
                    break
        except (OSError, IndexError):
            pass
        if detail:
            return (
                f"FFmpeg 屏幕采集失败：{detail}。请在系统设置 → 隐私与安全性 → 屏幕录制中允许咚咚"
            )
        return "FFmpeg 没有生成屏幕流，请在系统设置 → 隐私与安全性 → 屏幕录制中允许咚咚"

    def _error_message(self) -> str:
        if self.ffmpeg is None:
            return "AirPlay 播放会话失败，请确认接收端支持视频播放且允许当前 Mac 连接"
        if self.ffmpeg.poll() is not None:
            return self._capture_error()
        return "AirPlay 播放会话失败，请确认接收端支持视频播放且允许当前 Mac 连接"

    async def _play(self, url: str) -> None:
        import pyatv
        from pyatv.const import Protocol

        loop = asyncio.get_running_loop()
        configs = await pyatv.scan(
            loop, timeout=5, hosts=[self.target["address"]], protocol=Protocol.AirPlay
        )
        if not configs:
            raise RuntimeError(f"AirPlay 设备不可连接：{self.target['address']}")
        atv = await pyatv.connect(configs[0], loop, protocol=Protocol.AirPlay)
        try:
            await atv.stream.play_url(url)
        finally:
            atv.close()

    def stop(self) -> None:
        self.stop_event.set()
        if self.ffmpeg and self.ffmpeg.poll() is None:
            self.ffmpeg.terminate()
            try:
                self.ffmpeg.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.ffmpeg.kill()
        if self._ffmpeg_log_file:
            self._ffmpeg_log_file.close()
            self._ffmpeg_log_file = None
        if self.httpd:
            shutdown = getattr(self.httpd, "shutdown", None)
            if shutdown:
                shutdown()
            close = getattr(self.httpd, "server_close", None)
            if close:
                close()
        current = threading.current_thread()
        for thread in (self.http_thread, self.atv_thread):
            if thread and thread is not current and thread.is_alive():
                thread.join(timeout=3)
        shutil.rmtree(self.root, ignore_errors=True)


def start(cfg: dict, target: dict) -> str:
    global _session
    failure = ""
    with _lock:
        if _session:
            _session.stop()
        try:
            session = _Session(cfg, target)
            session.start()
            _session = session
        except Exception as exc:
            if "session" in locals():
                session.stop()
            return f"投屏启动失败：{exc}"
    time.sleep(0.8)
    if session.error:
        failure = session.error
    elif not (session.root / "screen.m3u8").exists():
        failure = session._capture_error()
    if failure:
        stop()
        return f"投屏启动失败：{failure}"
    return f"已开始投屏到 {target['name']}（实时流，低延迟模式）"


def stop() -> str:
    global _session
    with _lock:
        session, _session = _session, None
    if not session:
        return "当前没有投屏。"
    session.stop()
    return "已停止投屏。"


def _ffmpeg_path() -> str:
    arch = "arm64" if os.uname().machine == "arm64" else "x86_64"
    bundled = Path(__file__).resolve().parents[3] / "tools" / "ffmpeg" / f"darwin-{arch}" / "ffmpeg"
    if not bundled.is_file() or not os.access(bundled, os.X_OK):
        raise RuntimeError(f"项目内 FFmpeg 不存在或不可执行：{bundled}")
    return str(bundled)


def _local_ip(remote: str) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((remote, 9))
        return sock.getsockname()[0]
    finally:
        sock.close()
