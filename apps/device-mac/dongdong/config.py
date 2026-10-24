"""配置与设备身份。一个 JSON 文件，没有配置层套配置层。"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

HOME = Path(os.environ.get("DONGDONG_HOME") or Path.home() / ".dongdong")
PATH = HOME / "config.json"

DEFAULTS: dict = {
    "device_id": "",
    "client_id": "",
    "version": "0.1.0",
    "ota_url": "https://xrobo.qiniuapi.com/v1/ota/",
    "ws_url": "",
    "token": "",
    "agent": "codex",
    "cwd": "",
    "projects": ["~/OtherProjects"],
    "echo_guard": True,
    # 想加 agent 就往 agents 里加一项：cmd 是参数模板，env 支持 ${环境变量}。
    # 平台侧工具名不变，语音说「用 xxx 做」即可，不用改代码。
    "agents": {
        "codex": {
            "cmd": [
                "codex",
                "exec",
                "-C",
                "{cwd}",
                "-s",
                "workspace-write",
                "--skip-git-repo-check",
                "{prompt}",
            ],
            "env": {},
            "timeout_s": 120,
        },
        "claude": {
            "cmd": ["claude", "-p", "{prompt}", "--permission-mode", "acceptEdits"],
            "env": {},
            "timeout_s": 120,
        },
        # DeepSeek 走 Claude Code 的 Anthropic 兼容端点，体验和 claude 一致
        "deepseek": {
            "cmd": ["claude", "-p", "{prompt}", "--permission-mode", "acceptEdits"],
            "env": {
                "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": "${DEEPSEEK_API_KEY}",
                "ANTHROPIC_MODEL": "deepseek-chat",
            },
            "timeout_s": 120,
        },
    },
    "airplay": {"mirror_command": ""},
    "bridge": {"host": "127.0.0.1", "port": 8765, "token": ""},
    "panel": {"host": "127.0.0.1", "port": 8766, "token": ""},
}


def load() -> dict:
    cfg = _merge(DEFAULTS, _read())
    return cfg


def save(cfg: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    PATH.chmod(0o600)


def ensure() -> dict:
    """加载配置；第一次运行时生成设备身份并落盘。"""
    cfg = load()
    if not cfg["device_id"] or not cfg["client_id"]:
        cfg["device_id"] = _mac_like()
        cfg["client_id"] = str(uuid.uuid4())
        save(cfg)
    return cfg


def reset_identity() -> dict:
    cfg = load()
    cfg["device_id"] = _mac_like()
    cfg["client_id"] = str(uuid.uuid4())
    cfg["ws_url"] = cfg["token"] = ""
    save(cfg)
    return cfg


def _read() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise SystemExit(f"配置文件坏了: {PATH} ({e})，删掉重跑即可") from e


def _merge(base: dict, over: dict) -> dict:
    """逐层合并，保留没写到的默认值。"""
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def _mac_like() -> str:
    """用本机 node id 生成稳定的伪 MAC，删配置才会变。"""
    node = uuid.getnode()
    return ":".join(f"{(node >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))
