"""自证：一句话 → 拆两波 → 并行 → 两类故障 → 收口。

跑：.venv/bin/python design2/demo.py
"""

from __future__ import annotations

import time

from kernel import Ctx, Kernel, Snapshot
from model import Decision, GoalState, NewGoal, NewWork, Result, ResultKind, Work, WorkState

FIRST_WAVE = ["读目录", "读构建脚本", "读现有文档", "读测试", "读部署", "跑一次冒烟"]
PROOF_SET = {"读目录", "读构建脚本", "读现有文档", "读测试（换目录）", "跑一次冒烟（缩范围）"}


def find(snap: Snapshot, text: str) -> Work | None:
    """规划者只能从快照里看东西，看不见的就是不知道。"""
    pool = [*snap.open, *snap.running, *snap.finished, *snap.undelivered]
    return next((w for w in pool if w.text == text), None)


class DemoRunner:
    """假的执行者：走三步；两处故意失败，演示两种不同的失败语义。"""

    def run(self, work: Work, ctx: Ctx) -> Result:
        if work.attempts > 1:
            return Result(ResultKind.DONE, f"{work.text} 完成（第 {work.attempts} 次）")
        if work.text == "读测试":
            return Result(ResultKind.BLOCKED, reason="测试目录不存在")
        if work.text == "跑一次冒烟":
            return Result(ResultKind.EXHAUSTED, reason="超时预算")
        for n in range(1, 4):
            if ctx.should_stop():
                return Result(ResultKind.BLOCKED, reason="被叫停")
            ctx.emit(f"{work.text} {n}/3")
        return Result(ResultKind.DONE, f"{work.text} 完成")


class DemoPlanner:
    """不做领域智能，只把「先核资料、再出交接单」拆成两波。"""

    def plan(self, snap: Snapshot, final: bool) -> Decision:
        d = Decision()
        root = snap.goals[0]
        child = next((g for g in snap.goals if g.parent == root.id), None)

        if snap.total == 0:
            d.goals = [NewGoal("资料已核对", parent=root.id)]
            d.works = [
                NewWork(text, goal_id=root.id, priority=len(FIRST_WAVE) - i)
                for i, text in enumerate(FIRST_WAVE)
            ]
            return d
        if final:
            return d

        # 出错：换方案重做。这是重新规划，不是重试，所以不带 retry_of。
        if (w := find(snap, "读测试")) and w.state is WorkState.BLOCKED:
            if not find(snap, "读测试（换目录）"):
                d.works = [NewWork("读测试（换目录）", goal_id=root.id, priority=5)]
                return d

        # 耗尽：方向没错但没做完。带 retry_of，下游能看出这是同一条线的第二次。
        if (w := find(snap, "跑一次冒烟")) and w.state is WorkState.EXHAUSTED:
            if not find(snap, "跑一次冒烟（缩范围）"):
                d.works = [
                    NewWork("跑一次冒烟（缩范围）", goal_id=root.id, priority=4, retry_of=w.id)
                ]
                return d

        done = {w.text for w in snap.finished if w.state is WorkState.DONE}
        if child and child.state is GoalState.OPEN and PROOF_SET <= done:
            # 验收必须给证据，否则 goal 永远是个形容词
            d.prove = [(child.id, "五个来源读过，冒烟可跑（读部署由人接管）")]
            return d

        report = find(snap, "写交接单")
        if report is None:
            if child and child.state is GoalState.MET:
                d.works = [NewWork("写交接单", goal_id=root.id, priority=9)]
            return d
        if report.state is WorkState.DONE and root.state is GoalState.OPEN:
            d.prove = [(root.id, "资料核对完毕并产出交接单")]
            d.finish = True
            d.reason = "全部目标达成"
        return d


def main() -> None:
    kernel = Kernel(
        DemoPlanner(),
        DemoRunner(),
        max_inflight=2,
        # 根目标下会有 8 个子项；默认上限 8 会把「写交接单」拒掉，
        # 拒绝会出现在快照的 refusals 里——这是设计行为，不是 bug。
        max_children=12,
        emit=lambda i, t: print(f"    [w{i}] {t}"),
    )
    kernel.start("把这个仓库的交接资料补齐")
    kernel.tick()

    for w in kernel.snapshot().open:
        if w.text == "读部署":
            kernel.kill(w.id)
            print(f"\n人叫停了排队中的「{w.text}」（这一项由人接管，不进验收）\n")

    while kernel.rounds < 200 and kernel.tick():
        snap = kernel.snapshot()
        if snap.undelivered:
            print(f"轮 {kernel.rounds}: 在跑 {len(snap.running)}  待消费 {len(snap.undelivered)}")
            for w in snap.undelivered:
                print(f"  ← {w.text}: {w.state}  {w.reason or w.result}")
            kernel.consume([w.id for w in snap.undelivered])
        time.sleep(0.005)

    print(f"\n收尾：finished={kernel.finished}  reason={kernel.reason}")
    for w in reversed(kernel.snapshot(finished_cap=20).finished):
        print(f"  {str(w.state):9} {w.text}  {w.reason or w.result}")


if __name__ == "__main__":
    main()
