#!/usr/bin/env python3
"""环境体检：缺什么、权限有没有、能不能投屏。doctor 说 OK 再往下走。"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

from . import agent, config, screen

log = logging.getLogger("dongdong.doctor")

PASS, WARN, FAIL = "✅", "⚠️ ", "❌"


def _line(status: str, text: str) -> None:
    print(f"{status} {text}")


def run() -> int:
    cfg = config.ensure()
    bad = 0

    _line(PASS, f"配置 {config.PATH}")
    _line(PASS, f"Device-Id {cfg['device_id']}  Client-Id {cfg['client_id'][:8]}…")

    # 音频
    try:
        import opuslib  # noqa: F401
        import sounddevice as sd

        devices = sd.query_devices()
        ins = [d for d in devices if d["max_input_channels"] > 0]
        outs = [d for d in devices if d["max_output_channels"] > 0]
        _line(PASS if ins else WARN, f"麦克风 {len(ins)} 个，扬声器 {len(outs)} 个")
        _line(PASS, "Opus 编解码可用")
    except Exception as e:  # 装不上就直接说
        bad += 1
        _line(FAIL, f"音频栈不可用: {e}")

    # agent
    for name, preset in cfg["agents"].items():
        exe = preset["cmd"][0]
        found = shutil.which(exe)
        mark = PASS if found else WARN
        _line(
            mark,
            f"agent {name}: {' '.join(preset['cmd'][:2])} {'→ ' + found if found else '未安装'}",
        )
        if not found and name == cfg["agent"]:
            bad += 1

    # 工作目录
    try:
        _line(
            PASS, f"允许的目录: {', '.join(cfg['projects'])}（默认 {agent.resolve_dir(cfg, None)}）"
        )
    except Exception as e:
        bad += 1
        _line(FAIL, str(e))

    # 辅助功能权限（投屏要用）
    ok, detail = _accessibility()
    _line(PASS if ok else WARN, detail)
    if not ok:
        print(
            "   系统设置 → 隐私与安全性 → 辅助功能，把运行本程序的终端（或 DeepSeek Harness）勾上"
        )

    # 投屏目标 + 自动化可行性
    if screen._no_menu_bar():
        _line(WARN, "投屏自动化不可用：当前 macOS 的控制中心没有可脚本点击的菜单栏项")
        print("   自动投屏待替换方案（ffmpeg 推流 / 自定义 mirror_command），见 docs/decisions.md")
    found = screen.targets(5.0)
    if found:
        _line(PASS, f"局域网 AirPlay 目标: {', '.join(t['name'] for t in found)}")
    else:
        _line(WARN, "局域网没扫到 AirPlay 目标（电视没开 / 不在同一网段）")

    # 依赖
    missing = [
        m for m in ("websockets", "httpx", "zeroconf", "sounddevice", "opuslib") if not _has(m)
    ]
    if missing:
        bad += 1
        _line(FAIL, f"缺依赖: {', '.join(missing)} —— 跑 ./scripts/setup.sh")

    print()
    _line(PASS if bad == 0 else WARN, "可以开工" if bad == 0 else f"有 {bad} 项需要处理")
    return 0 if bad == 0 else 1


def _has(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def _accessibility() -> tuple[bool, str]:
    """用 osascript 探一下辅助功能权限，返回 (能用, 说明)。"""
    script = 'tell application "System Events" to get name of first process'
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    except Exception as e:
        return False, f"辅助功能检查失败: {e}"
    if r.returncode == 0:
        return True, "辅助功能权限已开启（可用于投屏）"
    if "-1719" in (r.stderr or "") or "辅助访问" in (r.stderr or ""):
        return False, "辅助功能权限未开启，投屏自动化会失败"
    return False, f"辅助功能检查异常: {(r.stderr or '').strip()[:160]}"


if __name__ == "__main__":
    sys.exit(run())
