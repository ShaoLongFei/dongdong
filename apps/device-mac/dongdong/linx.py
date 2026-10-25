"""灵矽设备协议：OTA 拿 token，连 WebSocket，收发音频，分发 MCP 工具调用。"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import opuslib
from websockets.asyncio.client import connect

from . import audio, config, mcp, state

log = logging.getLogger("dongdong.linx")

CONSOLE_URL = "https://xrobo.qiniu.com/"
MAX_TTS_FRAME = 5760  # 60ms @48k 的上限，给解码器留够


class Device:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ws = None
        self.session_id = ""
        self.ready = asyncio.Event()
        self.player = audio.Player()
        self.recorder = audio.Recorder()
        self.encoder = opuslib.Encoder(audio.MIC_RATE, 1, opuslib.APPLICATION_VOIP)
        self.decoder: opuslib.Decoder | None = None
        self.reconnects = 0

    async def run(self) -> None:
        log.info("咚咚启动，Device-Id %s", self.cfg["device_id"])
        state.set_(connection="connecting")
        while True:
            try:
                await self._session()
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as exc:
                self.reconnects += 1
                state.set_(connection="disconnected", reconnects=self.reconnects)
                log.warning("会话断了: %s，3 秒后重连", exc)
                await asyncio.sleep(3)
            finally:
                self.ready.clear()
                self.recorder.close()
                self.player.close()

    async def _session(self) -> None:
        url, token = await self.ensure_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Device-Id": self.cfg["device_id"],
            "Client-Id": self.cfg["client_id"],
            "Protocol-Version": "1",
            "User-Agent": f"dongdong-mac/{self.cfg['version']}",
        }
        async with connect(url, additional_headers=headers, max_size=None, ping_interval=20) as ws:
            self.ws = ws
            await self.send(_hello())
            recv = asyncio.create_task(self._recv(ws))
            mic = asyncio.create_task(self._mic(ws))
            try:
                done, _ = await asyncio.wait({recv, mic}, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()  # 把异常抛给上层去重连
            finally:
                # 父任务退出或重连前必须等子任务清理完，避免旧录音协程继续使用设备。
                recv.cancel()
                mic.cancel()
                await asyncio.gather(recv, mic, return_exceptions=True)
                self.ws = None

    async def send(self, obj: dict) -> None:
        if self.ws:
            await self.ws.send(json.dumps(obj, ensure_ascii=False))

    # ------------------------------------------------------------ 收

    async def _recv(self, ws) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                self._on_audio(raw)
                continue
            try:
                await self._on_message(json.loads(raw))
            except json.JSONDecodeError:
                log.debug("收到非 JSON 文本: %r", raw[:120])

    def _on_audio(self, packet: bytes) -> None:
        if not self.decoder:
            return
        try:
            self.player.push(self.decoder.decode(packet, MAX_TTS_FRAME))
        except Exception:
            log.debug("opus 解码失败，丢一帧")

    async def _on_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "hello":
            self.session_id = msg.get("session_id", "")
            params = msg.get("audio_params") or {}
            rate = int(params.get("sample_rate") or 24000)
            channels = int(params.get("channels") or 1)
            self.decoder = opuslib.Decoder(rate, channels)
            self.player.open(rate)
            log.info("连上了，会话 %s，TTS %d Hz", self.session_id, rate)
            state.set_(connection="connected", session_id=self.session_id, connected_at=time.time())
            self.ready.set()
            await self.send(
                {
                    "session_id": self.session_id,
                    "type": "listen",
                    "state": "start",
                    "mode": "realtime",
                }
            )
        elif kind == "stt":
            log.info("你: %s", msg.get("text"))
            state.set_(last_stt=msg.get("text", ""))
        elif kind == "tts":
            tts_state = msg.get("state")
            if tts_state == "sentence_start":
                log.info("咚咚: %s", msg.get("text"))
                state.set_(last_tts=msg.get("text", ""))
            elif tts_state == "start":
                log.info("TTS 开始")
            elif tts_state == "stop" and msg.get("is_aborted"):
                self.player.clear()
        elif kind == "mcp":
            payload = msg.get("payload") or {}
            log.info("MCP ← %s", payload.get("method") or f"响应 id={payload.get('id')}")
            # 工具里有子进程和 osascript，扔线程里跑，别堵音频
            response = await asyncio.to_thread(mcp.handle, payload)
            if response:
                await self.send({"type": "mcp", "payload": response})

    # ------------------------------------------------------------ 发

    async def _mic(self, ws) -> None:
        self.recorder.start()
        while True:
            await self.ready.wait()
            frame = self.recorder.read()
            if frame is None:
                await asyncio.sleep(0.01)
                continue
            if self.cfg.get("echo_guard") and self.player.playing():
                continue  # 自己说话时闭麦，避免外放回声把对话打成死循环
            await ws.send(self.encoder.encode(frame, audio.FRAME_SAMPLES))

    # ------------------------------------------------------------ 绑定

    async def ensure_token(self) -> tuple[str, str]:
        cfg = self.cfg
        if cfg["ws_url"] and cfg["token"]:
            return cfg["ws_url"], cfg["token"]
        data = await self._ota()
        activation = data.get("activation")
        if activation:
            print(
                f"\n绑定这台 Mac（只需一次）\n"
                f"  Device-Id : {cfg['device_id']}\n"
                f"  激活码    : {activation['code']}\n"
                f"  控制台    : {CONSOLE_URL}\n"
                f"绑定完会自动继续，不用重启。\n",
                flush=True,
            )
            while not await self._activated():
                await asyncio.sleep(3)
            data = await self._ota()
        socket = data.get("websocket") or {}
        if not socket.get("token"):
            raise RuntimeError("拿不到 token：去智控台确认设备已绑定、并已分配智能体")
        cfg["ws_url"], cfg["token"] = socket["url"], socket["token"]
        config.save(cfg)
        print(f"绑定完成，token 已存到 {config.PATH}", flush=True)
        return cfg["ws_url"], cfg["token"]

    async def _ota(self) -> dict:
        cfg = self.cfg
        headers = {
            "Activation-Version": "1",
            "Device-Id": cfg["device_id"],
            "Client-Id": cfg["client_id"],
            "User-Agent": f"dongdong-mac/{cfg['version']}",
            "Accept-Language": "zh-CN",
        }
        body = {
            "version": 0,
            "uuid": cfg["client_id"],
            "application": {"name": "dongdong", "version": cfg["version"]},
            "board": {"type": "dongdong-mac", "ssid": "", "rssi": 0},
            "mac_address": cfg["device_id"],
            "chip_model_name": "mac",
            "flash_size": 0,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(cfg["ota_url"], json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"OTA 失败: {data['error']}（Device-Id 和 mac_address 必须一致）")
        return data

    async def _activated(self) -> bool:
        url = self.cfg["ota_url"].rstrip("/") + "/activate"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers={"Device-Id": self.cfg["device_id"]})
        return resp.status_code == 200 and resp.text.strip() == "success"


def _hello() -> dict:
    return {
        "type": "hello",
        "version": 1,
        "features": {"mcp": True},
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": audio.MIC_RATE,
            "channels": 1,
            "frame_duration": 60,
            "play_buffer_duration": 1000,
        },
    }
