"""固定方案执行和 Birdview 阶段快照；工具事件保留真实检查结果。"""

import difflib
import json
from pathlib import Path

from . import birdview, builtin_tools
from .meeting_tools import EXECUTION_PROMPT, STAGE, MeetingTools
from .runtime_context import ScopeChange


async def execute(controller, meeting: dict, proposal: dict) -> dict:
    host, db = controller.host, controller.db
    sid, pid, payload = meeting["sid"], proposal["id"], proposal["payload"]
    workspace = Path(meeting["workspace"])
    # 每次实施保留单独活动流，旧方案及旧活动快照不覆盖。
    execution_id = birdview.new_id()
    events = [birdview.event(payload, sid, execution_id, "planned", "实施已确认方案")]
    initial = await birdview.file_hashes(workspace, payload["files"])
    checks = []
    baseline = {}
    for name in payload["files"]:
        path = builtin_tools.resolve_path(workspace, name)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"实施目标必须为普通文件: {name}")
        if path.exists() and path.stat().st_size > 2_000_000:
            raise ValueError(f"实施文件超过 2MB: {name}")
        baseline[name] = (
            path.read_text(errors="replace").splitlines(keepends=True) if path.exists() else []
        )
    run = {"id": ""}

    async def stage(phase, reason, actual_checks):
        entry = birdview.event(payload, sid, execution_id, phase, reason, actual_checks)
        entry["sequence"] = len(events) + 1
        events.append(entry)
        await birdview.render(execution_id, payload, workspace, events)
        if run["id"]:
            host.store.add_event(
                run["id"],
                "meeting_activity",
                {
                    "proposal": pid,
                    "artifact": execution_id,
                    "sequence": len(events),
                    "phase": phase,
                },
            )

    adapter = MeetingTools(workspace, {}, None, stage=stage, payload=payload)
    calls = {}

    def observe(kind, data):
        if kind == "run_start":
            run["id"] = data["run_id"]
        if kind == "tool_call":
            calls[data["id"]] = data
        if kind == "tool_result" and data["name"] == "shell":
            args = json.loads(calls[data["id"]]["arguments"])
            output = data["result"]
            try:
                code = int(output.split("\n", 1)[0].removeprefix("exit_code="))
            except ValueError:
                code = -1
            checks.append(
                {
                    "command": args["command"][:2000],
                    "exitCode": code,
                    "status": "passed" if code == 0 else "failed",
                    "summary": output[-1800:] or "无输出",
                }
            )

    async def finish(status, rid, error):
        # 先持久化收尾状态，取消发生在后续渲染期间也不会遗留 executing。
        db.execution(pid, "done" if status == "done" else status, rid)
        db.update(
            sid, phase="result" if status == "done" else "discussion", run_id=rid, error=error
        )
        terminal = (
            "completed"
            if status == "done" and not any(c["status"] == "failed" for c in checks)
            else "cancelled"
            if status == "stopped"
            else "failed"
        )
        try:
            await stage("verifying", "实际工具检查记录；命令退出码不证明所有预期行为已满足", checks)
            await stage(
                terminal,
                error
                or (
                    "本轮实施结束；请核对实际 diff 和未验证项"
                    if checks
                    else "本轮结束，没有自动验证记录"
                ),
                checks,
            )
            chunks, changed = [], []
            for name, before in baseline.items():
                path = builtin_tools.resolve_path(workspace, name)
                if path.exists() and path.stat().st_size > 2_000_000:
                    changed.append(f"{name}（超过 2MB，diff 省略）")
                    continue
                after = (
                    path.read_text(errors="replace").splitlines(keepends=True)
                    if path.exists()
                    else []
                )
                if before != after:
                    changed.append(name)
                    chunks.extend(
                        difflib.unified_diff(
                            before, after, fromfile="before/" + name, tofile="after/" + name
                        )
                    )
            final = await birdview.file_hashes(workspace, initial)
            outside = sorted(
                name
                for name in set(initial) | set(final)
                if initial.get(name, "missing") != final.get(name, "missing")
                and name not in payload["files"]
            )
            changed.extend(name + "（方案范围外，需核查）" for name in outside)
            if outside:
                chunks.append("\n范围外实际变化（停止后保留，未自动回滚）：\n" + "\n".join(outside))
            changes, diff = "\n".join(changed), "".join(chunks)
            (birdview.directory(execution_id) / "diff.txt").write_text(diff)
            host.store.add_event(
                rid,
                "meeting_result",
                {
                    "proposal": pid,
                    "artifact": execution_id,
                    "checks": checks,
                    "changes": changes,
                    "verification": "实际命令结果，不等于完整验收",
                },
            )
        except Exception as exc:
            error = error or str(exc)
        db.execution(pid, "done" if status == "done" else status, rid)
        db.update(
            sid, phase="result" if status == "done" else "discussion", run_id=rid, error=error
        )

    async def handler(name, args, record):
        if name == "shell":
            before = await birdview.file_hashes(workspace, payload["files"])
            result = await builtin_tools.call_tool(name, args, builtin_tools.ToolContext(workspace))
            after = await birdview.file_hashes(workspace, before)
            outside = sorted(
                path
                for path in set(before) | set(after)
                if before.get(path, "missing") != after.get(path, "missing")
                and path not in payload["files"]
            )
            if outside:
                raise ScopeChange("命令修改了方案外文件，已停止，未回滚：" + ", ".join(outside))
            return result
        result = await adapter.call(name, args, record)
        if name in {"write_file", "edit_file"}:
            await stage("editing", f"修改已确认文件 {args['path']}", [])
        return result

    options = dict(
        toolset=[*builtin_tools.TOOL_SPECS, STAGE],
        tool_handler=handler,
        instruction=EXECUTION_PROMPT
        + json.dumps(
            {key: value for key, value in payload.items() if key not in {"catalog", "receipt"}},
            ensure_ascii=False,
        ),
        isolated=True,
    )
    rid = await host.start(
        sid,
        "实施当前已确认方案，完成后说明验证与限制。",
        str(workspace),
        meeting_options=options,
        finished=finish,
        observer=observe,
    )
    if host.busy:
        db.execution(pid, "running", rid)
        db.update(sid, phase="executing", run_id=rid)
    return {"run_id": rid, "message": f"正在实施方案 v{proposal['version']}"}
