"""终端里的 agent 对话入口。"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from . import config
from .llm import ModelConfig
from .runtime import Runtime
from .store import Store


def _model(cfg: dict) -> ModelConfig:
    model = cfg["model"]
    api_key = config.expand(model["api_key"])
    if not api_key:
        raise SystemExit(
            "没有模型 key：在 ~/.dongdong/config.json 设置 model.api_key 或导出 DEEPSEEK_API_KEY"
        )
    return ModelConfig(
        base_url=model["base_url"],
        api_key=api_key,
        model=model["model"],
        temperature=float(model["temperature"]),
        max_tokens=int(model["max_tokens"]),
    )


def _print_event(kind: str, data: dict) -> None:
    if kind == "text":
        print(data["delta"], end="", flush=True)
    elif kind == "tool_call":
        print(f"\n[工具] {data['name']} {data['arguments'][:600]}\n", flush=True)
    elif kind == "tool_result":
        print(f"[结果] {data['result'][:600]}\n", flush=True)
    elif kind == "error":
        print(f"\n[错误] {data['message']}\n", flush=True)
    elif kind == "done":
        print("\n", flush=True)


async def main(cfg: dict, message: str | None = None) -> None:
    # 每个模型请求都打一行 INFO 太吵；错误仍然会显示
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # 先做不碰数据库的检查：key 或工作区不对就直接退出，别白开一个库
    model = _model(cfg)
    workspace = Path(cfg["workspace"]).expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit(f"工作区不存在: {workspace}")
    store = Store(config.HOME / "dongdong.db")
    try:
        store.stop_orphan_runs()
        runtime = Runtime(
            store,
            model,
            workspace,
            max_steps=int(cfg["runtime"]["max_steps"]),
            history_messages=int(cfg["runtime"]["history_messages"]),
        )
        sid = store.create_session("终端会话", "cli")
        # asyncio.run 把 SIGINT 换成了「取消主任务」，可主任务卡在 input() 时取消送不进来，
        # Ctrl-C 会挂住进程。所以读输入时用默认处理器（直接抛 KeyboardInterrupt），跑对话时换回去。
        loop_sigint = signal.getsignal(signal.SIGINT)
        if message:
            await runtime.run(sid, message, on_event=_print_event)
            return

        print("咚咚 agent。输入 /new 新会话，/sessions 看会话，/quit 退出。")
        while True:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            try:
                # 阻塞读 stdin 即可：此时没有别的协程在跑
                text = input("你> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text == "/quit":
                break
            if text == "/new":
                sid = store.create_session("终端会话", "cli")
                print(f"新会话 {sid}")
                continue
            if text == "/sessions":
                for item in store.list_sessions()[:10]:
                    print(f"{item['id']}  {item['channel']}  {item['title']}")
                continue
            signal.signal(signal.SIGINT, loop_sigint)
            try:
                await runtime.run(sid, text, on_event=_print_event)
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\n已停止")
                break
            except Exception:
                # _print_event 已经打印了错误；网络/模型失败不应该结束整个终端会话
                continue
    finally:
        store.close()
