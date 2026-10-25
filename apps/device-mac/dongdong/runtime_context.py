"""模型上下文裁剪与基础提示。"""

SYSTEM_PROMPT = """你是咚咚，一个跑在用户 Mac 上的通用 agent。
文件工具只能操作工作区内的路径；shell 是有意保留的逃生口，但优先在工作区内操作。
工作区：{workspace}

规则：
- 先读再改；不确定就查。
- 修改文件用 edit_file（旧文本必须唯一），新建/覆盖用 write_file。
- 跑命令用 shell，命令要短，不要用交互式命令。
- 删除、git push、安装软件等不可逆操作，先说明需要用户确认；若有审批界面，等待批准后执行。
- 不要编造工具结果；工具报错就换方案。
- 多步骤任务先用 update_plan 给出简短步骤清单，切换步骤时更新状态；简单问答无需计划。
- 计划只记录实际进度，未执行的步骤不能标 completed；完成后再给最终交付结果。
- 仅在用户明确要求长期记住时调用 memory_save；项目事实用 project，跨项目偏好用 global。
- 回答简洁，说清楚做了什么、结果是什么。"""

READ_SKILL = {
    "type": "function",
    "function": {
        "name": "read_skill",
        "description": "读取已启用技能的完整 SKILL.md 指令，需要时先读再做。",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
}


def _trim(rows: list[dict], limit: int, keep_id: int | None = None) -> list[dict]:
    # 窗口是软限制：当前 run 的 user 消息必须留下，否则长任务会丢目标。
    if limit <= 0:
        limit = len(rows)
    start = max(0, len(rows) - limit)
    if keep_id is not None:
        for index, row in enumerate(rows):
            if row["id"] == keep_id:
                start = min(start, index)
                break
    rows = rows[start:]
    while rows and rows[0]["role"] == "tool":
        rows.pop(0)
    out: list[dict] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        if row["role"] == "assistant" and row["tool_calls"]:
            need = {call["id"] for call in row["tool_calls"]}
            end = index + 1
            got: set[str] = set()
            while end < len(rows) and rows[end]["role"] == "tool":
                got.add(rows[end]["tool_call_id"])
                end += 1
            if need <= got:
                out.append(row)
                # 只带上这次调用自己的结果：混进来的 tool 消息对不上 tool_calls 会被 400
                out.extend(part for part in rows[index + 1 : end] if part["tool_call_id"] in need)
                index = end
                continue
            index += 1
            continue
        if row["role"] == "tool":
            index += 1
            continue
        out.append(row)
        index += 1
    return out


class ScopeChange(RuntimeError):
    """当前方案不再覆盖下一操作，宿主必须停止执行。"""
