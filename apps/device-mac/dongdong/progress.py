"""用户可见的步骤计划 schema 与校验；不承担任务调度。"""

PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "update_plan",
        "description": "更新用户可见的任务步骤和当前进度，不执行命令。每次提供完整计划。",
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "string"},
                            "status": {"enum": ["pending", "in_progress", "completed"]},
                        },
                        "required": ["step", "status"],
                    },
                }
            },
            "required": ["steps"],
        },
    },
}


def _plan(args: dict) -> dict:
    steps = args.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
        raise ValueError("计划需要 1 到 8 个步骤")
    for item in steps:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("step"), str)
            or not 1 <= len(item["step"].strip()) <= 200
            or item.get("status") not in ("pending", "in_progress", "completed")
        ):
            raise ValueError("步骤需要简短描述和有效状态")
    if sum(item["status"] == "in_progress" for item in steps) > 1:
        raise ValueError("一次只能有一个进行中的步骤")
    return {"steps": [{"step": s["step"].strip(), "status": s["status"]} for s in steps]}
