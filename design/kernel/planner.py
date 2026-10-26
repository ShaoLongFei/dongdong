"""Planner：把「当前态势」变成「这一轮要做什么」。

关键取舍：规划者不直接写库，只返回 Decision（见 model.py）。ARTEX 里规划者
是拿工具直接改探索图的，副作用藏在工具调用里，难测难回放；这里把副作用收
敛到内核一处，规划者退化成 View -> Decision 的纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .model import Decision, Event, Goal, GoalState, Item, ItemState, NewGoal, NewItem


@dataclass
class View:
    """一轮规划的只读输入。规划者能看到的一切都在这里，不许回头查库。"""

    goal: str
    goals: list[Goal]
    items: list[Item]
    triggers: list[Event]
    round: int
    stalled: bool  # 有 open 目标但既没在跑的、也没排队的
    final: bool = False  # 收尾轮：只判目标，不产新工作项


class Planner(Protocol):
    def plan(self, view: View) -> Decision: ...


# --------------------------------------------------------------------------
# 确定性规划者：只为让骨架能跑，不含任何领域智能。
# 真实使用时应换成「LLM + 工具调用 -> Decision」的适配层（见内核文档 §10.2）。
# --------------------------------------------------------------------------

WAVE1 = ("盘点现有章节", "翻译正文", "校对链接")


class DemoPlanner:
    """给一句「补一份中文 README」，拆两波、分两次验收。"""

    def plan(self, view: View) -> Decision:
        d = Decision()
        by_text = {i.text: i for i in view.items}
        goals = {g.text: g for g in view.goals}

        def done(*names: str) -> bool:
            return any(
                by_text.get(n) is not None and by_text[n].state is ItemState.DONE for n in names
            )

        if not view.items:
            d.add_items = [
                NewItem("翻译正文", priority=3),
                NewItem("盘点现有章节", priority=2),
                NewItem("校对链接", priority=1),
            ]
            d.add_goals = [NewGoal("正文中文化", parent=1)]
            return d

        # 翻译失败过一次：补一个重试项。EXHAUSTED 是终态，不会自动重领。
        if by_text.get("翻译正文") and by_text["翻译正文"].state is ItemState.EXHAUSTED:
            if "翻译正文（重试）" not in by_text:
                d.add_items = [NewItem("翻译正文（重试）", priority=3)]
                return d

        sub = goals.get("正文中文化")
        if (
            sub
            and sub.state is GoalState.OPEN
            and done("盘点现有章节")
            and done("翻译正文", "翻译正文（重试）")
        ):
            d.prove = [(sub.id, "正文与章节说明已中文化")]
            return d

        if (
            "写变更说明" not in by_text
            and done("盘点现有章节", "校对链接")
            and done("翻译正文", "翻译正文（重试）")
        ):
            d.add_items = [NewItem("写变更说明", priority=1)]
            return d

        if done("写变更说明"):
            top = goals.get(view.goal)
            if top and top.state is GoalState.OPEN:
                d.prove = [(top.id, "README 已中文化并附变更说明")]
                d.finish = True
                d.reason = "全部目标达成"
        return d
