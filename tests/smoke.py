#!/usr/bin/env python3
"""唯一的测试。只覆盖会静默出错的边界，不测胶水。

跑法：.venv/bin/python tests/smoke.py   （或 ./scripts/check.sh）
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))
os.environ["DONGDONG_HOME"] = tempfile.mkdtemp(prefix="dongdong-smoke-")
logging.disable(logging.CRITICAL)  # 预期内的报错别刷屏

from dongdong import agent, config, mcp, screen, tools  # noqa: E402
from dongdong.linx import _hello  # noqa: E402


def test_config_merge_keeps_defaults():
    merged = config._merge({"a": 1, "b": {"c": 2, "d": 3}}, {"b": {"c": 9}})
    assert merged == {"a": 1, "b": {"c": 9, "d": 3}}, merged


def test_device_id_shape():
    ident = config._mac_like()
    parts = ident.split(":")
    assert len(parts) == 6 and all(len(p) == 2 for p in parts), ident
    assert config._mac_like() == ident, "同一台机器必须稳定"


def test_hello_declares_mcp_and_opus():
    hello = _hello()
    assert hello["features"]["mcp"] is True
    params = hello["audio_params"]
    assert params["format"] == "opus" and params["sample_rate"] == 16000
    assert params["frame_duration"] == 60


def test_tool_registry_shape():
    assert tools.TOOLS, "一个工具都没有"
    for name, tool in tools.TOOLS.items():
        assert name.startswith("self."), name
        described = tool.describe()
        assert described["inputSchema"]["type"] == "object"
        assert described["type"] == 0
        assert set(described["inputSchema"]["required"]) <= set(
            described["inputSchema"]["properties"]
        ), f"{name} 的 required 里有没声明的参数"


def test_mcp_dispatch():
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["protocolVersion"] == "2024-11-05"

    listed = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert {t["name"] for t in listed["result"]["tools"]} == set(tools.TOOLS)

    missing = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "self.nope"},
        }
    )
    assert missing["error"]["code"] == -32601

    bad = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "self.code.run", "arguments": {"instruction": "  "}},
        }
    )
    assert bad["result"]["isError"] is True, bad


def test_resolve_dir_blocks_escape():
    work = Path(tempfile.mkdtemp(prefix="dongdong-work-")).resolve()  # macOS 上 /var → /private/var
    cfg = {"projects": [str(work)], "cwd": ""}
    assert agent.resolve_dir(cfg, None) == work
    assert agent.resolve_dir(cfg, str(work)) == work
    for outside in ("/tmp", "/etc", str(work / "..")):
        try:
            agent.resolve_dir(cfg, outside)
        except ValueError:
            pass
        else:
            raise AssertionError(f"越界目录被放行了: {outside}")


def test_resolve_dir_relative_name():
    root = Path(tempfile.mkdtemp(prefix="dongdong-rel-")).resolve()
    (root / "proj").mkdir()
    cfg = {"projects": [str(root)], "cwd": ""}
    assert agent.resolve_dir(cfg, "proj") == root / "proj", "相对目录要能在项目根下找到"
    assert agent.resolve_dir(cfg, str(root / "proj")) == root / "proj"
    for bad in ("nope", "../etc", "/etc"):
        try:
            agent.resolve_dir(cfg, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"不该放行: {bad}")


def test_agent_env_contract():
    assert agent.required_env({"cmd": ["x"]}) == []
    assert "DEEPSEEK_API_KEY" in agent.required_env(config.load()["agents"]["deepseek"])
    os.environ["SMOKE_KEY"] = "k123"
    assert agent._expand({"K": "${SMOKE_KEY}"}) == {"K": "k123"}


def test_custom_agent_from_config_file():
    cfg = config.load()
    cfg["agents"]["myagent"] = {"cmd": ["echo", "{prompt}"], "env": {}, "timeout_s": 5}
    config.save(cfg)
    reloaded = config.load()
    assert reloaded["agents"]["myagent"]["timeout_s"] == 5
    assert reloaded["agent"] == "codex", "加 agent 不能把默认值冲掉"


def test_agent_argv_and_env():
    cfg = config.load()
    argv = [
        p.replace("{cwd}", "/w").replace("{prompt}", "hi") for p in cfg["agents"]["codex"]["cmd"]
    ]
    assert "/w" in argv and "hi" in argv
    os.environ["SMOKE_KEY"] = "k123"
    assert agent._expand({"K": "${SMOKE_KEY}"}) == {"K": "k123"}
    assert agent._expand({"K": "${NOT_SET_AT_ALL}"}) == {"K": ""}


def test_opus_roundtrip():
    import opuslib

    enc = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
    dec = opuslib.Decoder(16000, 1)
    packet = enc.encode(b"\x00\x01" * 960, 960)
    assert 0 < len(packet) < 200
    assert len(dec.decode(packet, 5760)) == 1920


def test_airplay_script_escapes_target():
    script = screen._mirror_script('卧室"电视')
    assert '\\"电视' in script
    assert "{TARGET}" not in script and "{MODE}" not in script


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001 测试跑挂了要打印出来
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("smoke 通过" if not failures else f"{failures} 个失败")
    sys.exit(1 if failures else 0)
