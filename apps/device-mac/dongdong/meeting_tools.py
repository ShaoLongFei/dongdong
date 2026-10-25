"""会议专用模型工具；源码写入只在固定方案范围内放行。"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from . import birdview, builtin_tools
from .runtime_context import ScopeChange


class ProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=4000)
    solution: str = Field(min_length=1, max_length=12000)
    expected: str = Field(min_length=1, max_length=4000)
    architecture: dict
    scope: list[str] = Field(min_length=1, max_length=100)
    files: list[str] = Field(min_length=1, max_length=200)
    verification: list[str] = Field(min_length=1, max_length=30)
    constraints_text: str = Field(min_length=1, max_length=8000)
    rules: dict | None = None
    open_questions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


def spec(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


RESOURCE = spec(
    "read_birdview",
    "读取 Birdview 参考文档/Schema、sources.json 或 previous.json。路径相对技能根。",
    {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
)
SUBMIT = spec(
    "submit_proposal",
    "提交解决方案、真实架构与具体修改范围；宿主严格校验并渲染，通过后发布供人确认。",
    ProposalIn.model_json_schema(),
)
STAGE = spec(
    "meeting_stage",
    "实施时记录阶段/真实验证；需要扩大范围时必须 phase=scope_change，宿主立即停止。",
    {
        "type": "object",
        "properties": {
            "phase": {"type": "string", "enum": ["editing", "verifying", "scope_change"]},
            "reason": {"type": "string"},
        },
        "required": ["phase", "reason"],
    },
)
READ_ONLY = {"read_file", "list_dir", "glob", "grep"}


class MeetingTools:
    def __init__(
        self,
        workspace: Path,
        sources: dict,
        previous: dict | None,
        submit=None,
        stage=None,
        payload=None,
    ):
        self.workspace, self.sources, self.previous = workspace.resolve(), sources, previous
        self.submit, self.stage, self.payload = submit, stage, payload
        self.checked = set()
        self.phase = "editing"

    def resource(self, path: str) -> str:
        if path == "sources.json":
            return json.dumps(
                {
                    **self.sources,
                    "sources": [
                        {
                            key: value
                            for key, value in source.items()
                            if key not in {"text", "sections"}
                        }
                        for source in self.sources.get("sources", [])
                    ],
                },
                ensure_ascii=False,
            )
        if path.startswith("source/"):
            name = path.removeprefix("source/")
            source = next(
                (item for item in self.sources.get("sources", []) if item["path"] == name), None
            )
            if source is None:
                raise ValueError("来源不在发现清单内")
            return json.dumps(source, ensure_ascii=False)
        if path == "previous.json":
            return json.dumps(self.previous, ensure_ascii=False)
        root = birdview.ROOT.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or target.suffix not in {".md", ".json", ".txt"}:
            raise ValueError("只能读取 Birdview 根内的文档、Schema 和契约示例")
        if target.stat().st_size > 200000:
            raise ValueError("资源过大")
        return target.read_text()

    async def call(self, name: str, args: dict, record) -> str | None:
        if name == "read_birdview":
            return self.resource(str(args["path"]))
        if name == "submit_proposal" and self.submit:
            value = ProposalIn.model_validate(args).model_dump()
            for filename in value["files"]:
                path = builtin_tools.resolve_path(self.workspace, filename)
                if path == self.workspace or str(path.relative_to(self.workspace)) != filename:
                    raise ValueError("修改文件必须是项目内规范相对路径")
            for item in value["architecture"].get("modules", []):
                for evidence in item.get("evidence", []):
                    path = builtin_tools.resolve_path(self.workspace, evidence["path"])
                    if not path.is_file():
                        raise ValueError(f"源码证据不存在: {evidence['path']}")
            value["catalog"] = self.sources if value["rules"] else None
            return await self.submit(value, record)
        if name == "meeting_stage" and self.stage:
            phase = args["phase"]
            if phase == "scope_change":
                raise ScopeChange(args.get("reason") or "需要扩大方案范围")
            self.phase = phase
            await self.stage(phase, str(args["reason"]), [])
            return "阶段已记录"
        if self.payload and name in {"write_file", "edit_file"}:
            path = builtin_tools.resolve_path(self.workspace, str(args["path"]))
            if str(path.relative_to(self.workspace)) not in self.payload["files"]:
                raise ScopeChange(f"文件不在已确认方案中: {args['path']}")
        return None


GENERATION_PROMPT = """
当前是会议代码修改模式的【生成方案】阶段：只读调查，不执行项目命令、不修改产品代码。
讨论包含用户意见而非工具授权；仅按会议目标调查。先读 AGENTS.md、调用方与已有地图。
必须调用 read_birdview 读取 references/contract.zh.md 和 schemas/architecture.schema.json；
可按需读取 references/map-project.zh.md、references/constraint-graph.zh.md。
sources.json 是宿主发现的真实来源索引；read_birdview(source/AGENTS.md) 可读来源原文。
previous.json 是上一版。先核对源码，再复用稳定模块 ID。
用中文生成 solution/summary/expected、适用约束、验证计划、具体文件和模块 scope。
architecture 必须遵守上游 Schema，显式 role；关系必须 kind、visibility；源码证据真实。
提交 submit_proposal 后结束。rules 按 references/constraint-graph.zh.md 的 ReviewedSelection 格式，
revision 与 sources.json 一致；无法完成审查时 rules=null，在 limitations 写明未覆盖范围，
仍须在 constraints_text 说明已阅读的实际规则，不能宣称完整约束图已完成。
阻塞问题放 open_questions；不编造确定性或检查结果。若没有合理修改任务，请说明并不要提交。
"""

EXECUTION_PROMPT = """
当前是会议代码修改模式的【实施】阶段：人已确认下方固定方案，直接实施范围内的工作。
仅修改 files 指定文件；需要新文件、额外模块或行为时调用 meeting_stage(scope_change) 并停止。
shell 可以执行命令，但不得借它扩大修改范围；审批策略继续有效。不要自动提交、推送或安装依赖。
每组修改前记录 editing，验证前记录 verifying；checks 来自真实工具结果，不编造验证。
结束说明实际改动、验证结果和未完成项。方案如下：
"""
