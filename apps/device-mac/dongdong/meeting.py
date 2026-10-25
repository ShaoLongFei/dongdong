"""一个显式会议上下文；生成和实施分别复用现有后台 run。"""

import asyncio
import json
from pathlib import Path

from . import birdview, builtin_tools, work_gate
from .meeting_store import Meetings
from .meeting_tools import (
    GENERATION_PROMPT,
    READ_ONLY,
    RESOURCE,
    SUBMIT,
    MeetingTools,
)
from .store import now


class Meeting:
    def __init__(self, host):
        self.host = host
        self.db = Meetings(host.store)
        self.pending = set()
        self.notice = ""

    def view(self, sid: str) -> dict | None:
        meeting = self.db.get(sid)
        if not meeting:
            return None
        proposal = self.db.proposal(meeting["current"]) if meeting["current"] else None
        return {
            **meeting,
            "proposal": proposal,
            "history": self.db.history(sid),
            "discussion": self.db.discussion(sid),
            "controls": self.db.controls(sid),
            "dirty": bool(proposal and proposal["cutoff"] != meeting["seq"]),
            "notice": self.notice,
        }

    def open(self, sid: str, directory: str) -> dict:
        active = self.db.active()
        if (
            self.host.busy
            or work_gate.current()
            or (active and active["phase"] in {"generating", "executing"})
        ):
            raise ValueError("请先结束活动任务")
        if not self.host.store.get_session(sid):
            raise ValueError("会话不存在")
        path = builtin_tools.resolve_path(self.host.workspace, directory)
        if not path.is_dir():
            raise ValueError("项目目录不存在")
        if not (path / ".git").exists():
            raise ValueError("请选择 Git 仓库根目录（支持 .git 文件的 worktree）")
        self.db.open(sid, str(path))
        self.host.task_session = sid
        return self.view(sid)

    def close(self, sid: str) -> None:
        if (
            self.host.busy
            or self.pending
            or (self.db.get(sid) or {}).get("phase") in {"generating", "executing"}
        ):
            raise ValueError("请先停止或结束活动任务")
        self.db.close(sid)

    def voice(self, text: str) -> bool:
        active = self.db.active()
        if not active:
            return False
        sid = active["sid"]
        action = birdview.control(text)
        if not action:
            self.db.discuss(sid, text, "voice")
            return True
        # 在收到原话时固定目标；不能等异步任务开始后才选择方案。
        current = self.db.proposal(active["current"]) if active["current"] else None
        expected = (current["id"], current["hash"]) if current else ("", "")

        async def run():
            try:
                result = await self.action(sid, action, "voice", text, *expected)
                self.notice = result.get("message", "操作已提交")
            except Exception as exc:
                self.notice = str(exc)
                self.db.control(sid, action, text, "voice", self.notice)

        task = asyncio.create_task(run())
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        return True

    async def message(self, sid: str, text: str) -> dict:
        action = birdview.control(text)
        if action:
            current = self.view(sid)["proposal"]
            return await self.action(
                sid,
                action,
                "text",
                text,
                current["id"] if current else "",
                current["hash"] if current else "",
            )
        self.db.discuss(sid, text, "text")
        return {"message": "已记入讨论；准备好后说“咚咚，整理一下方案”", "run_id": ""}

    async def action(
        self, sid: str, action: str, source: str, text: str, pid: str = "", expected: str = ""
    ) -> dict:
        meeting = self.db.get(sid)
        if not meeting or not meeting["enabled"]:
            raise ValueError("代码修改模式未开启")
        if action == "generate":
            if meeting["phase"] == "generating":
                return {"run_id": meeting["run_id"], "message": "当前方案正在整理，新意见已保留"}
            result = await self.generate(meeting)
        elif action in {"confirm", "resume"}:
            result = await self.confirm(meeting, pid, expected, source, text, action == "resume")
        else:
            raise ValueError("未知会议动作")
        self.db.control(sid, action, text, source, result.get("message", "已提交"))
        return result

    async def generate(self, meeting: dict) -> dict:
        if meeting["phase"] == "executing":
            raise ValueError("方案正在领取或实施，请结束后再整理")
        if self.host.busy or work_gate.current():
            raise ValueError("当前有活动任务，请结束后再整理")
        if not self.db.discussion(meeting["sid"]):
            raise ValueError("请先讨论或输入希望修改的内容")
        sid, cutoff = meeting["sid"], meeting["seq"]
        workspace = Path(meeting["workspace"])
        self.db.update(sid, phase="generating")
        pid = birdview.new_id()
        try:
            folder = birdview.directory(pid)
            folder.mkdir(parents=True, mode=0o700)
            baseline = await birdview.fingerprint(workspace)
            sources = await birdview.catalog(workspace, workspace.name, folder / "catalog.json")
            previous = self.db.proposal(meeting["current"]) if meeting["current"] else None
            published = []

            async def submit(payload, record):
                if published:
                    raise ValueError("本轮方案已发布，请结束回复")
                current = await birdview.fingerprint(workspace, payload["files"])
                # 将不存在的新文件也纳入 fingerprint 时重新计算初始基线。
                if await birdview.fingerprint(workspace) != baseline:
                    raise ValueError("生成期间源码变化，请重新整理")
                payload["baseline"] = current
                events = [birdview.event(payload, sid, pid, "planned", payload["solution"])]
                receipt = await birdview.render(pid, payload, workspace, events)
                payload["receipt"] = receipt
                proposal = self.db.publish(sid, cutoff, payload, pid)
                published.append(proposal)
                record(
                    "proposal",
                    {"id": pid, "version": proposal["version"], "hash": proposal["hash"]},
                )
                return f"方案 v{proposal['version']} 已发布，等待人确认；不要修改产品代码。"

            adapter = MeetingTools(
                workspace,
                sources,
                {
                    key: value
                    for key, value in previous["payload"].items()
                    if key not in {"catalog", "receipt"}
                }
                if previous
                else None,
                submit=submit,
            )
            instruction = f"会议讨论（固定至序号 {cutoff}）：\n{json.dumps(self.db.discussion(sid, cutoff), ensure_ascii=False)}"

            async def finish(status, rid, error):
                phase = "ready" if published and status == "done" else "discussion"
                self.db.update(
                    sid,
                    phase=phase,
                    run_id=rid,
                    error=error or ("" if published else "未生成方案，请补充需求后重新整理"),
                )

            options = dict(
                toolset=[s for s in builtin_tools.TOOL_SPECS if s["function"]["name"] in READ_ONLY]
                + [RESOURCE, SUBMIT],
                tool_handler=adapter.call,
                instruction=GENERATION_PROMPT,
                isolated=True,
            )
            rid = await self.host.start(
                sid, instruction, str(workspace), meeting_options=options, finished=finish
            )
            if self.host.busy:
                self.db.update(sid, phase="generating", run_id=rid)
            return {"run_id": rid, "message": "正在整理方案与改动地图"}
        except BaseException:
            self.db.update(sid, phase="discussion", error="方案生成未启动，请检查依赖与项目目录")
            raise

    async def confirm(self, meeting, pid, expected, source, text, resume=False) -> dict:
        proposal = self.db.proposal(pid)
        if not proposal or proposal["sid"] != meeting["sid"] or pid != meeting["current"]:
            raise ValueError("没有可确认的当前方案，请先整理并展示")
        if proposal["status"] != "ready" and not resume:
            return {
                "run_id": proposal["run_id"],
                "message": "本方案已领取，不会重复启动；中断时可显式恢复",
            }
        if self.host.busy or work_gate.current():
            raise ValueError("当前有活动任务，请结束后再实施")
        if resume:
            if source != "button":
                raise ValueError("恢复请使用工作台明确操作")
            if meeting["phase"] != "interrupted" or proposal["status"] not in {
                "claimed",
                "running",
            }:
                raise ValueError("此方案不需要恢复；失败或取消后请重新整理")
            if (
                not proposal["shown"]
                or proposal["hash"] != expected
                or proposal["cutoff"] != meeting["seq"]
            ):
                raise ValueError("恢复前请重新展示当前版本，且不能有新意见")
        payload = proposal["payload"]
        if payload["open_questions"]:
            raise ValueError("方案还有待决问题，请先解决并重新整理")
        workspace = Path(meeting["workspace"])
        # 先领取，再异步计算基线；第二个并发确认无法越过唯一领取。
        if not resume:
            confirmation = {
                "source": source,
                "text": text,
                "at": now(),
                "hash": expected,
                "shown": proposal["shown"],
            }
            self.db.claim(meeting["sid"], pid, expected, confirmation)
        self.db.update(meeting["sid"], phase="executing")
        try:
            if await birdview.fingerprint(workspace, payload["files"]) != payload["baseline"]:
                self.db.execution(pid, "stale")
                raise ValueError("源码或工作区已变化，请重新整理方案")
            from .meeting_execution import execute

            return await execute(self, meeting, proposal)
        except BaseException as exc:
            self.db.update(meeting["sid"], phase="interrupted", error=str(exc))
            raise

    def mcp_status(self) -> str | None:
        meeting = self.db.active()
        if not meeting:
            return None
        view = self.view(meeting["sid"])
        version = view["proposal"]["version"] if view["proposal"] else "无"
        return json.dumps(
            {
                "mode": "代码修改",
                "phase": meeting["phase"],
                "proposal": version,
                "notice": self.notice or meeting["error"],
                "run_id": meeting["run_id"],
                "instruction": "会议语音已本地保存；仅整理指令生成地图，实施须本地明确确认。",
            },
            ensure_ascii=False,
        )
