"""首次规划的事务边界，保持 TaskManager 主循环短小。"""

from dataclasses import replace

from .task_model import ItemSpec
from .task_planner import PlanResult


def plan_run(manager, run_id: str, text: str, cwd: str, cancel) -> None:
    store = manager.store
    store.add_event(run_id, "planning_started", {"text": text, "cwd": cwd})
    if cancel.is_set() or (store.get_run(run_id) or {}).get("status") != "planning":
        return
    manager.planner.model = manager.model
    manager.planner.cfg = manager.cfg
    result = (
        PlanResult(
            [ItemSpec(instruction=text, cwd=cwd, agent="builtin")],
            error="自定义执行器跳过自动规划",
            used_fallback=True,
        )
        if manager.runner is not None
        else manager.planner.plan(text, cwd)
    )
    if cancel.is_set() or (store.get_run(run_id) or {}).get("status") != "planning":
        return
    try:
        planned_items = namespace_items(run_id, result.items)
        store.add_items(run_id, planned_items)
        criteria = [
            {"item_id": item.id, "criteria": item.acceptance.strip()}
            for item in planned_items
            if item.acceptance.strip()
        ]
        if criteria:
            store.set_goal_acceptance(run_id, criteria)
    except Exception as exc:  # noqa: BLE001
        if cancel.is_set() or (store.get_run(run_id) or {}).get("status") != "planning":
            return
        result = PlanResult([ItemSpec(instruction=text, cwd=cwd, agent="builtin")], str(exc), True)
        store.add_items(run_id, result.items)
    if result.used_fallback or result.error:
        store.add_event(run_id, "planning_error", {"message": result.error, "fallback": True})
    store.add_event(
        run_id, "planning_done", {"item_count": len(result.items), "fallback": result.used_fallback}
    )
    store.set_task_status(run_id, "running", event_type="planning_status")


def namespace_items(run_id: str, specs: list[ItemSpec]) -> list[ItemSpec]:
    mapping = {item.id: f"{run_id}-{item.id}" for item in specs if item.id}
    return [
        replace(
            item,
            id=mapping.get(item.id, ""),
            depends_on=tuple(mapping.get(d, d) for d in item.depends_on),
        )
        for item in specs
    ]
