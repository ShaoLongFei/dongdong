"""按用户选择启停语音；关闭完成后不再采音或播放。"""

from __future__ import annotations

import asyncio
import logging

from . import state

log = logging.getLogger("dongdong.voice")


class Voice:
    def __init__(self, cfg: dict, on_text):
        self.cfg = cfg
        self.on_text = on_text
        self.task: asyncio.Task | None = None
        self.error = ""
        self.lock = asyncio.Lock()
        state.set_(connection="disabled")

    @property
    def enabled(self) -> bool:
        return self.task is not None and not self.task.done()

    async def set_enabled(self, enabled: bool) -> None:
        async with self.lock:
            if enabled:
                if self.enabled:
                    return
                self.error = ""
                state.set_(connection="connecting")
                self.task = asyncio.create_task(self._run())
            else:
                if self.task:
                    self.task.cancel()
                    await asyncio.gather(self.task, return_exceptions=True)
                    self.task = None
                self.error = ""
                state.set_(connection="disabled")

    async def _run(self) -> None:
        try:
            from .linx import Device

            await Device(self.cfg, on_text=self.on_text).run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = str(exc)
            state.set_(connection="error")
            log.exception("语音启动失败，Web 仍可使用")
        else:
            state.set_(connection="disabled")
