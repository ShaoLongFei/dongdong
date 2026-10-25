"""固定上游渲染器；所有参数由宿主构造，不开放 shell。"""

import asyncio
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

from . import builtin_tools, config

ROOT = Path(__file__).with_name("vendor") / "birdview"


def control(text: str) -> str:
    if "?" in text or "？" in text:
        return ""
    normalized = re.sub(r"[\s，,。.!！?？]", "", text)
    normalized = re.sub(r"^咚咚", "", normalized)
    return {"整理一下方案": "generate", "按这个方案实施": "confirm"}.get(normalized, "")


async def command(*args: str, cwd: Path, timeout: int = 30) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    reader = asyncio.create_task(builtin_tools._drain(proc.stdout, 1_000_000))
    try:
        error, output = await builtin_tools._collect(proc, reader, timeout)
        if error or proc.returncode:
            raise ValueError(error or output[-6000:])
        if len(output.encode()) >= 999000:
            raise ValueError("命令输出超过上限，请缩小项目范围")
        return output
    except asyncio.CancelledError:
        builtin_tools._kill_group(proc)
        await proc.wait()
        reader.cancel()
        raise


async def git(workspace: Path, *args: str) -> str:
    return await command(
        "git", "--literal-pathspecs", "-c", "core.fsmonitor=false", *args, cwd=workspace
    )


async def fingerprint(workspace: Path, extra: list[str] = ()) -> str:
    head = await git(workspace, "rev-parse", "HEAD")
    names = set((await git(workspace, "ls-files", "-co", "--exclude-standard", "-z")).split("\0"))
    names.update(extra)
    names.discard("")
    if len(names) > 20000:
        raise ValueError("项目超过 20000 个文件，请选择更小的项目目录")

    def compute():
        result = hashlib.sha256(head.encode())
        total = 0
        for name in sorted(names):
            path = workspace / name
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("基线路径越界")
            # Git 中的符号链接只记录链接文本，不读取工作区之外的目标。
            if not path.parent.resolve().is_relative_to(workspace):
                raise ValueError("基线路径通过链接越界")
            result.update(name.encode() + b"\0")
            if not path.exists() and not path.is_symlink():
                result.update(b"missing")
                continue
            mode = path.lstat().st_mode
            result.update(str(mode).encode())
            if path.is_symlink():
                result.update(os.readlink(path).encode())
            elif stat.S_ISREG(mode):
                total += path.stat().st_size
                if total > 200_000_000:
                    raise ValueError("源码超过 200MB，请缩小项目范围")
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(65536), b""):
                        result.update(chunk)
            else:
                raise ValueError(f"基线包含非普通文件: {name}")
        return result.hexdigest()

    return await asyncio.to_thread(compute)


def directory(pid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", pid):
        raise ValueError("产物 ID 无效")
    root = (config.HOME / "meetings").resolve()
    target = (root / pid).resolve()
    if not target.is_relative_to(root):
        raise ValueError("产物目录越界")
    return target


async def node(script: str, *args: str, timeout: int = 30) -> str:
    try:
        return await command(
            "node", str(ROOT / "scripts" / script), *args, cwd=ROOT, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise ValueError("缺少 Node.js；安装 Node 18+ 后运行 ./scripts/setup-birdview.sh") from exc


async def catalog(workspace: Path, name: str, target: Path) -> dict:
    await node("discover-constraints.mjs", str(workspace), str(target), name)
    return json.loads(target.read_text())


async def render(pid: str, payload: dict, workspace: Path, events: list[dict]) -> dict:
    folder = directory(pid)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    architecture = folder / "architecture.json"
    activity = folder / "activity.jsonl"
    architecture.write_text(json.dumps(payload["architecture"], ensure_ascii=False))
    activity.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")
    args = [str(architecture), str(folder / "map.html"), str(activity)]
    if payload.get("rules") and payload.get("catalog"):
        rules = folder / "rules.json"
        sources = folder / "catalog.json"
        rules.write_text(json.dumps(payload["rules"], ensure_ascii=False))
        sources.write_text(json.dumps(payload["catalog"], ensure_ascii=False))
        args += ["--catalog", str(sources), "--rules", str(rules), "--repo", str(workspace)]
    else:
        if not payload.get("limitations"):
            raise ValueError("无已审查规则时，必须说明约束覆盖缺口")
        args += ["--architecture-only"]
    receipt = json.loads(await node("birdview.mjs", "deliver", *args, timeout=60))
    if not receipt.get("ok"):
        raise ValueError(json.dumps(receipt, ensure_ascii=False)[-6000:])
    return receipt


def event(payload: dict, sid: str, pid: str, phase: str, reason: str, checks=()) -> dict:
    architecture = payload["architecture"]
    scope = payload["scope"]
    files = payload["files"]
    owned = set()
    for module in architecture["modules"]:
        for item in module["ownership"]:
            for name in files:
                if name == item["path"] or (
                    item["kind"] == "directory" and name.startswith(item["path"].rstrip("/") + "/")
                ):
                    owned.add(name)
    return {
        "schemaVersion": 1,
        "projectId": architecture["project"]["id"],
        "mapId": architecture["mapId"],
        "mapRevision": architecture["revision"],
        "sessionId": "meeting-" + sid,
        "taskId": "proposal-" + pid,
        "sequence": 1,
        "phase": phase,
        "scope": scope,
        "targets": scope if phase not in {"completed", "failed", "cancelled"} else [],
        "reason": reason[:2000],
        "files": files,
        "unmappedFiles": sorted(set(files) - owned),
        "evidenceKind": "agent-declared",
        "checks": list(checks),
    }


def new_id() -> str:
    return uuid.uuid4().hex
