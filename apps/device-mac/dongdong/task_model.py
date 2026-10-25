"""任务调度领域对象；这里只保存跨模块共享的边界数据。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath

MAX_SCOPE_PATHS = 32
MAX_SCOPE_PATH_LENGTH = 512


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
    # None 表示旧调用方未声明范围；空 tuple 表示明确的空集合（只读 Item 可仅声明 read_paths）。
    read_paths: tuple[str, ...] | None = None
    write_paths: tuple[str, ...] | None = None
    scope: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.scope:
            return
        if self.read_paths is None and self.write_paths is None:
            return
        value = {
            "read": list(self.read_paths or ()),
            "write": list(self.write_paths or ()),
        }
        object.__setattr__(self, "scope", normalize_scope(value))


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


def normalize_scope(value: object) -> dict[str, list[str]]:
    """只接受工作区内的相对文件路径，空 scope 表示独占。"""
    if value is None or value == "":
        return {}
    if not isinstance(value, dict):
        raise ValueError("scope 必须是包含 read/write 的对象")
    unknown = set(value) - {"read", "write"}
    if unknown:
        raise ValueError("scope 只能包含 read 和 write")
    result: dict[str, list[str]] = {}
    total = 0
    for mode in ("read", "write"):
        paths = value.get(mode, [])
        if paths is None:
            paths = []
        if not isinstance(paths, list):
            raise ValueError(f"scope.{mode} 必须是路径数组")
        normalized: list[str] = []
        for raw in paths:
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("scope 路径必须是非空字符串")
            path = raw.strip()
            if "\x00" in path or len(path) > MAX_SCOPE_PATH_LENGTH:
                raise ValueError("scope 路径无效")
            if PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute():
                raise ValueError("scope 路径必须是相对路径")
            if "\\" in path:
                raise ValueError("scope 路径必须使用相对 POSIX 路径")
            parts = PurePosixPath(path).parts
            if not parts or any(part in {"", ".", ".."} for part in parts):
                raise ValueError("scope 路径不能包含 . 或 ..")
            canonical = PurePosixPath(*parts).as_posix()
            if canonical not in normalized:
                normalized.append(canonical)
        total += len(normalized)
        if normalized:
            result[mode] = normalized
    if total > MAX_SCOPE_PATHS:
        raise ValueError(f"scope 路径数量超过上限 {MAX_SCOPE_PATHS}")
    return result
