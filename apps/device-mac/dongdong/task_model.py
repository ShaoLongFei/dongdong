"""任务调度领域对象；这里只保存跨模块共享的边界数据。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RunSpec:
    goal_text: str = ""
    channel: str = "web"
    workspace: str = ""
    max_workers: int = 1
    deadline_at: str | None = None
    # 结构化验收可以是 criterion 列表或包含 criteria 的对象；保留字符串兼容旧调用方。
    acceptance: object = None


@dataclass(frozen=True)
class ItemSpec:
    instruction: str
    id: str = ""
    goal_id: str = ""
    parent_id: str = ""
    acceptance: str = ""
    agent: str = "builtin"
    cwd: str = ""
    priority: int = 0
    max_attempts: int = 2
    depends_on: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AttemptResult:
    status: str
    output: str = ""
    error_kind: str = ""
    error: str = ""
    exit_code: int | None = None


@dataclass(frozen=True)
class TaskView:
    run: dict
    goals: list[dict]
    items: list[dict]
    attempts: list[dict]
    events: list[dict]


def acceptance_value(value: object) -> object:
    """返回可持久化的验收值；空字符串和空集合表示没有验收约束。"""
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, dict)):
        return value
    raise ValueError("acceptance 必须是字符串、对象或数组")
