"""麦克风和扬声器。只做两件事：录 16k 单声道、按服务器给的采样率放音。"""

from __future__ import annotations

import logging
import queue

import sounddevice as sd

log = logging.getLogger("dongdong.audio")

MIC_RATE = 16000
FRAME_SAMPLES = 960  # 60ms @16k，和 hello 里声明的一致


class Recorder:
    """按 60ms 一帧录音，异步侧用 read() 取。"""

    def __init__(self) -> None:
        self.queue: queue.Queue[bytes] = queue.Queue(maxsize=32)
        self.stream: sd.RawInputStream | None = None

    def start(self) -> None:
        if self.stream:
            return
        self.stream = sd.RawInputStream(
            samplerate=MIC_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            callback=self._callback,
        )
        self.stream.start()
        log.info("麦克风已开")

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("录音状态: %s", status)
        try:
            self.queue.put_nowait(bytes(indata))
        except queue.Full:
            pass  # 网络慢就丢帧，别把录音线程堵死

    def read(self) -> bytes | None:
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
            log.info("麦克风已关")


class Player:
    """播放服务器下发的 PCM。帧长和 block 长度不匹配时自己拼。"""

    def __init__(self) -> None:
        self.queue: queue.Queue[bytes] = queue.Queue()
        self.buffer = bytearray()
        self.stream: sd.RawOutputStream | None = None
        self.rate = 0

    def open(self, rate: int) -> None:
        if self.stream and rate == self.rate:
            return
        self.close()
        self.rate = rate
        self.stream = sd.RawOutputStream(
            samplerate=rate,
            channels=1,
            dtype="int16",
            blocksize=max(160, int(rate * 0.02)),
            callback=self._callback,
        )
        self.stream.start()
        log.info("扬声器已开，%d Hz", rate)

    def _callback(self, outdata, frames, time_info, status) -> None:
        need = frames * 2
        while len(self.buffer) < need:
            try:
                self.buffer += self.queue.get_nowait()
            except queue.Empty:
                break
        if len(self.buffer) >= need:
            outdata[:] = self.buffer[:need]
            del self.buffer[:need]
        else:
            outdata[:] = bytes(need)  # 不够就静音，不卡播放

    def push(self, pcm: bytes) -> None:
        if self.stream:
            self.queue.put(pcm)

    def playing(self) -> bool:
        return not self.queue.empty() or bool(self.buffer)

    def clear(self) -> None:
        self.buffer.clear()
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return

    def close(self) -> None:
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
