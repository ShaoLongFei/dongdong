"""AirPlay 投屏：zeroconf 发现目标，AppleScript 操作控制中心的「屏幕镜像」。

shortcut: 控制中心的 UI 层级随 macOS 版本变，脚本点不中时用 airplay.mirror_command
覆盖成你自己录的脚本；升级时机 = 换 macOS 大版本或换电视。
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

log = logging.getLogger("dongdong.screen")

AIRPLAY_SERVICE = "_airplay._tcp.local."
logging.getLogger("zeroconf").setLevel(logging.ERROR)  # 多网卡环境它会刷一堆无法路由的告警


class _Collector(ServiceListener):
    def __init__(self) -> None:
        self.found: list[dict] = []

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if not info:
            return
        addrs = info.parsed_addresses()
        self.found.append(
            {
                "name": name.removesuffix("." + type_).removesuffix("." + AIRPLAY_SERVICE),
                "address": addrs[0] if addrs else "",
                "model": (info.properties or {}).get(b"model", b"").decode(errors="ignore"),
            }
        )

    def update_service(self, *args) -> None:  # 不需要
        pass

    def remove_service(self, *args) -> None:
        pass


def targets(timeout: float = 5.0) -> list[dict]:
    """扫局域网里的 AirPlay 目标（电视、Apple TV、HomePod）。"""
    zc = Zeroconf()
    collector = _Collector()
    ServiceBrowser(zc, AIRPLAY_SERVICE, collector)
    try:
        time.sleep(timeout)  # mDNS 只能等，没有更优雅的办法
    finally:
        zc.close()
    return sorted(collector.found, key=lambda t: t["name"])


def mirror(cfg: dict, target: str) -> str:
    return _dispatch(cfg, target, "mirror")


def stop(cfg: dict) -> str:
    return _dispatch(cfg, "", "stop")


def _dispatch(cfg: dict, target: str, action: str) -> str:
    override = (cfg.get("airplay") or {}).get("mirror_command", "").strip()
    if override:
        cmd = override.replace("{target}", shlex.quote(target))
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return (r.stdout or r.stderr).strip() or (
            "投屏指令已执行" if r.returncode == 0 else "投屏指令失败"
        )

    if action == "mirror":
        from . import airplay_stream

        found = _find_target(target)
        if not found:
            return f"没找到 AirPlay 设备：{target}。先运行 self.screen.list。"
        return airplay_stream.start(cfg, found)
    if action == "stop":
        from . import airplay_stream

        return airplay_stream.stop()

    if _no_menu_bar():
        return "当前系统控制中心没有可脚本点击的屏幕镜像入口。"

    script = _stop_script() if action == "stop" else _mirror_script(target)
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    err = (r.stderr or "").strip()
    if r.returncode == 0:
        return f"已{'停止投屏' if action == 'stop' else '投屏到 ' + target}"
    if "-1719" in err or "辅助访问" in err:
        return (
            "没有辅助功能权限，投屏点不动。去 系统设置 → 隐私与安全性 → 辅助功能，"
            "把运行咚咚的终端勾上，然后重新运行。"
        )
    return f"投屏失败：{err or '未知错误'}。可以用 dongdong airplay debug 看菜单结构。"


def _find_target(target: str) -> dict | None:
    """按名称、名称片段或地址选择目标，避免要求用户重复输入 mDNS 细节。"""
    wanted = target.casefold()
    found = targets(3.0)
    exact = [
        item for item in found if wanted in {item["name"].casefold(), item["address"].casefold()}
    ]
    if len(exact) == 1:
        return exact[0]
    partial = [item for item in found if wanted and wanted in item["name"].casefold()]
    return partial[0] if len(partial) == 1 else None


def _no_menu_bar() -> bool:
    """macOS 26+ 的状态栏项目不在 ControlCenter 里，提前判断，别让用户看一堆 AX 报错。"""
    script = (
        'tell application "System Events" to tell process "ControlCenter" to get count of menu bars'
    )
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0 and r.stdout.strip() == "0"


def dump() -> str:
    """打印控制中心的 UI 结构，脚本失效时用来改选择器。"""
    script = """
tell application "System Events"
	tell (first process whose name is "ControlCenter")
		set out to ""
		repeat with mb in menu bars
			repeat with mi in menu bar items of mb
				set out to out & "menubar-item: " & (name of mi) & " / " & (description of mi) & linefeed
			end repeat
		end repeat
		repeat with w in windows
			set out to out & "window: " & (name of w) & linefeed
			try
				with timeout of 8 seconds
					repeat with e in (entire contents of w)
						set out to out & "  " & (class of e as text) & ": " & (name of e) & linefeed
					end repeat
				end timeout
			end try
		end repeat
		return out
	end tell
end tell
"""
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=40)
    return (r.stdout or r.stderr).strip()


def _mirror_script(target: str) -> str:
    return _SCRIPT.replace("{TARGET}", _quote(target)).replace("{MODE}", "mirror")


def _stop_script() -> str:
    return _SCRIPT.replace("{TARGET}", "").replace("{MODE}", "stop")


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


# 先找独立的「屏幕镜像」菜单栏项，点开后在窗口/菜单里找名字包含目标的那一项。
# 找不到就报错退出，不静默失败。
_SCRIPT = r"""
set mode to "{MODE}"
set targetName to "{TARGET}"
tell application "System Events"
	tell (first process whose name is "ControlCenter")
		set mirrorItem to missing value
		repeat with mb in menu bars
			repeat with mi in menu bar items of mb
				try
					set d to (description of mi as text)
					set n to (name of mi as text)
					if d contains "Screen" or d contains "镜像" or n contains "Screen" or n contains "镜像" then
						set mirrorItem to mi
						exit repeat
					end if
				end try
			end repeat
			if mirrorItem is not missing value then exit repeat
		end repeat
		if mirrorItem is missing value then error "没找到屏幕镜像菜单栏项：控制中心 → 屏幕镜像 → 在菜单栏显示"

		click mirrorItem
		delay 1.2

		if mode is "stop" then
			set wanted to {"停止镜像", "Stop Mirroring", "断开连接", "Disconnect"}
		else
			set wanted to {targetName}
		end if

		set hit to missing value
		with timeout of 10 seconds
			repeat with container in (windows & menus)
				try
					repeat with e in (entire contents of container)
						try
							set en to (name of e as text)
							repeat with w in wanted
								if w is not "" and en contains w then
									set hit to e
									exit repeat
								end if
							end repeat
						end try
						if hit is not missing value then exit repeat
					end repeat
				end try
				if hit is not missing value then exit repeat
			end repeat
		end timeout

		if hit is missing value then error "菜单里没找到：" & targetName
		click hit
		delay 0.5
	end tell
end tell
return "ok"
"""
