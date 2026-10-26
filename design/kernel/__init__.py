"""任务调度内核（设计骨架，非生产实现）。设计说明见 ../kernel-design.md。

跑法：
    cd design && python3 -m kernel.demo
"""

from .model import (
    Decision,
    Event,
    Goal,
    GoalState,
    Item,
    ItemState,
    NewGoal,
    NewItem,
    Result,
    ResultKind,
    RunState,
)
from .planner import DemoPlanner, Planner, View
from .run import Run
from .runner import DemoRunner, RunContext, Runner, SubprocessRunner
from .scheduler import Kernel
from .store import InMemoryStore, WorkStore

__all__ = [
    "Decision",
    "DemoPlanner",
    "DemoRunner",
    "Event",
    "Goal",
    "GoalState",
    "InMemoryStore",
    "Item",
    "ItemState",
    "Kernel",
    "NewGoal",
    "NewItem",
    "Planner",
    "Result",
    "ResultKind",
    "Run",
    "RunContext",
    "Runner",
    "RunState",
    "SubprocessRunner",
    "View",
    "WorkStore",
]
