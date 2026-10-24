"""咚咚命令行。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from . import bridge, config, doctor, panel, screen, tools


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dongdong", description="咚咚 · 灵矽语音控制 Mac")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bind", help="首次绑定：打印激活码，绑完自动继续")
    sub.add_parser("run", help="开始语音控制")
    sub.add_parser("doctor", help="环境体检")
    sub.add_parser("tools", help="打印 MCP 工具定义")
    call = sub.add_parser("call", help="本地调用工具，不经过语音")
    call.add_argument("name")
    call.add_argument("arguments", nargs="?", default="{}", help="JSON 参数")
    serve = sub.add_parser("serve", help="启动本地 HTTP 转发（给 ESP32）")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    chat = sub.add_parser("chat", help="终端里和内置 agent 对话")
    chat.add_argument("--message", help="只跑一句")
    airplay = sub.add_parser("airplay", help="投屏调试")
    airplay.add_argument(
        "action", nargs="?", default="list", choices=["list", "mirror", "stop", "debug"]
    )
    airplay.add_argument("target", nargs="?")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = config.ensure()

    if args.cmd == "doctor":
        sys.exit(doctor.run())
    if args.cmd == "tools":
        print(
            json.dumps([t.describe() for t in tools.TOOLS.values()], ensure_ascii=False, indent=2)
        )
        return
    if args.cmd == "call":
        print(
            json.dumps(
                tools.call(args.name, json.loads(args.arguments)), ensure_ascii=False, indent=2
            )
        )
        return
    if args.cmd == "airplay":
        if args.action == "list":
            found = screen.targets(3.0)
            print(
                "\n".join(f"{t['name']}\t{t['address']}\t{t['model']}" for t in found) or "没扫到"
            )
        elif args.action == "mirror":
            if not args.target:
                sys.exit("用法: dongdong airplay mirror <目标名>")
            print(screen.mirror(cfg, args.target))
        elif args.action == "stop":
            print(screen.stop(cfg))
        else:
            print(screen.dump())
        return
    if args.cmd == "chat":
        from . import chat_cli

        asyncio.run(chat_cli.main(cfg, args.message))
        return
    if args.cmd == "serve":
        if args.host:
            cfg["bridge"]["host"] = args.host
        if args.port:
            cfg["bridge"]["port"] = args.port
        config.save(cfg)
        bridge.serve(cfg)
        return

    from .linx import Device

    device = Device(cfg)
    try:
        if args.cmd == "bind":
            asyncio.run(device.ensure_token())
        else:
            panel.start(cfg)
            asyncio.run(device.run())
    except KeyboardInterrupt:
        print("\n再见")


if __name__ == "__main__":
    main()
