# 咚咚生产任务调度内核设计

> 状态：基础调度、自动规划、多 Item DAG、Goal 验收证据、受限失败修复、文件范围冲突串行化、停摆检测与有限 drain 已实现；完整监察仍待后续演进。
> 
> 依据：`design/` 的 Goal/Item/Event/Run 模型、CAS claim、View → Decision 与收尾规则；`design2/` 的调度、规划落地和监察分层；当前 `Runtime`、SQLite/SSE、Web 和灵矽 MCP 入口。

## 1. 目标

咚咚需要把一次 Web 或灵矽语音请求变成可观察、可恢复的任务。一个目标可以拆成带依赖关系的多个子任务，由多个独立 Agent 并行执行；子任务失败时可以有限重试，进程退出或 worker 崩溃后可以恢复未完成任务。

调度内核负责目标、DAG、派发、租约、状态、事件和收口。Agent 负责自己的模型循环、文件工具、MCP 工具和执行输出。Web 与灵矽通过同一任务记录查询状态。

成功条件：

1. 一个目标可以生成没有环的 DAG，只有依赖全部完成的子任务才能执行。
2. 同一个子任务在任意时刻最多由一个 worker 持有。
3. worker 崩溃、进程重启或租约过期后，任务可以重新领取，不会永久卡在 running。
4. 子任务的结果、失败、重试和输出保留在事件记录中，Web 和灵矽看到同一状态。
5. 现有简单任务仍然可以作为只有一个 Item 的 Run 执行。

当前代码已交付的能力：SQLite WAL 迁移、Run/Goal/Item/Attempt/Event 持久化、依赖与环检测、CAS claim、lease、有限重试、取消/暂停/恢复、统一 Web/MCP 状态投影、4 worker、结构化 Planner、规划失败回退、多 Item DAG、独立 Item Runtime 上下文和规划状态 UI。首次规划已经接入 TaskManager；本规格剩余内容属于后续演进，不应再按“自动规划尚未实施”理解。

已完成的自动规划与验收边界：模型为一次目标返回经过 schema 校验的多个 Item 和依赖关系；规划失败有可见事件并回退到单 Item；独立 Item 可以并行执行；依赖 Item 串行执行；Run 收口前持久化 Goal acceptance、Item 结果摘要和 `goal_verified` 事件。有约束但没有 verifier 的 Run 进入 `needs_review`，不会标记为 `done`。Item 文件范围声明与 SQLite 事务内冲突检测已完成；完整 Monitor 收口、配置化调度和跨进程协调继续保留为后续阶段。

当前实现与后续事项：

- **已完成**：首次结构化规划、Item 数量/ID/依赖/环/cwd/Agent 校验、规划失败回退、规划事件、独立 Item 执行、依赖等待、统一 Web/MCP 投影。
- **未完成**：更完整的模型驱动自动修复规划；完整 Monitor 收口（最终验收 Planner 和严格收口顺序）；真正的跨进程 worker 协调与自动恢复。当前 Goal verifier 仍由调用方注入，默认 Web 入口只持久化证据并对无约束任务自动通过。
- **暂不纳入**：多机共享工作区、分布式锁、自动合并并行修改，以及故事模式事项。

## 2. 范围与约束

### 包含

- Goal、Run、Item、依赖边、Attempt、Event 的 SQLite 持久化。
- Planner 生成和修正 DAG；Scheduler 跨进程安全派发；Worker 执行单个 Item；Monitor 负责租约、停摆和收口。
- 内置 Runtime、codex、claude、deepseek CLI 三类执行适配器。
- 失败分类、有限自动重试、暂停、恢复、取消、停止和超时。
- 现有 `self.code.run/result/stop` 的兼容入口，以及新的任务状态入口。

### 不包含

- Redis、RabbitMQ 等外部队列或新的常驻基础设施。
- 多机调度、跨 Mac 的共享工作区和分布式锁服务。
- 自动修改用户代码的合并器；并行 Item 必须由 Planner 明确划分工作区或文件范围。
- 把 `design/`、`design2/` 的实验实现直接 import 到生产路径。

SQLite WAL + 原子条件更新是跨进程队列和 claim 的基础。worker 可以来自同一进程的线程/任务池，也可以来自另一个运行咚咚的进程；SQLite lease 保证两者不会同时持有同一 Item。默认仍只启动一个调度进程，显式配置 worker 数量后才并行。

## 3. 领域模型

### 3.1 Run、Goal、Item

- **Run**：一次用户请求的生命周期，关联会话、入口、工作区、状态和并发预算。
- **Goal**：Run 需要证明的最终结果，包含目标文本和验收证据。
- **Item**：一个可独立派发的工作单元，包含 instruction、验收标准、Agent 配置、依赖、尝试次数和结果摘要。
- **Attempt**：Item 的一次具体执行，保存 worker、开始/结束时间、输出摘要和错误分类。重试不覆盖旧 Attempt。
- **Event**：不可变的状态变化和进度记录，供 SSE、审计和恢复使用。

一个 Run 可以有多个 Goal，一个 Goal 可以有多个 Item。Item 之间用 `depends_on` 边组成 DAG；父子关系只用于展示和 Planner 上下文，不代替依赖边。

### 3.2 状态

```text
Run:   created → planning → running ⇄ paused
                         ├────────────→ done
                         ├────────────→ failed
                         ├────────────→ timeout
                         └────────────→ canceled

Item:  open → running → done
                 │  ├─→ retry_wait → open
                 │  ├─→ blocked
                 │  ├─→ exhausted
                 │  └─→ stopped
```

`blocked` 表示当前 Item 因输入、权限、依赖或明确执行错误无法继续；`exhausted` 表示已经用完该 Item 的重试预算；`stopped` 表示被用户或 Run 收口主动终止。Run 只有在 Goal 已验收且没有未完成 Item 时才是 `done`。

### 3.3 不变量

1. `claim` 必须以 `state='open'`、依赖全部 `done`、Run 处于 `running` 为条件，并在同一事务中写入 lease owner 和 lease expiry。
2. lease 未过期的 `running` Item 不得被其他 worker 领取；过期后只能由恢复流程转回 `open`，并记录 `lease_expired` 事件。
3. Run 进入终态后禁止新增和领取 Item；在跑的 Item 进入 `stopped` 或自然收口。
4. DAG 写入必须在事务中完成并通过环检测；失败时整个规划结果回滚。
5. 只有 Planner/Run Manager 能新增或修改 Item，Worker 只能写 Attempt、结果和事件。
6. 任意状态变化都先写 Event，再更新当前快照，或在同一事务中完成两者。

## 4. SQLite 设计

现有 `runs`、`events`、`messages` 和 `tool_calls` 保留。新增表和字段如下；迁移必须幂等，旧数据库启动时自动补表/补列。

```text
runs
  id, session_id, channel, workspace, status, goal_text,
  max_workers, deadline_at, error, started_at, ended_at

goals
  id, run_id, text, status(open/met/abandoned), evidence, created_at, met_at

items
  id, run_id, goal_id, parent_id, instruction, acceptance,
  agent, cwd, priority, status, attempts, max_attempts,
  lease_owner, lease_until, result, error, created_at, updated_at

item_deps
  item_id, depends_on_id, PRIMARY KEY(item_id, depends_on_id)

attempts
  id, item_id, worker_id, status, started_at, ended_at,
  output, error_kind, error, exit_code

events
  ... existing columns ...
  item_id, attempt_id, sequence
```

`item_deps` 必须有 `item_id != depends_on_id` 的约束。SQLite 外键约束保证引用存在；应用层在插入同一批边时做 DFS/Kahn 环检测。`items` 上建立 `(run_id, status, priority)` 和 `(lease_until, status)` 索引；`item_deps` 建立 `depends_on_id` 索引。

`claim` 使用类似下面的事务语义，不依赖进程内锁：

```sql
BEGIN IMMEDIATE;
UPDATE items
SET status='running', lease_owner=?, lease_until=?, attempts=attempts+1,
    updated_at=?
WHERE id=? AND status='open'
  AND EXISTS (SELECT 1 FROM runs WHERE runs.id=items.run_id AND runs.status='running')
  AND NOT EXISTS (
    SELECT 1 FROM item_deps d JOIN items p ON p.id=d.depends_on_id
    WHERE d.item_id=items.id AND p.status!='done'
  );
COMMIT;
```

实际实现可先查询 frontier 再执行带条件的 `UPDATE`；只有受影响行数为 1 才算 claim 成功。

## 5. Planner、Scheduler、Worker、Monitor

### 5.1 Planner

Planner 接收只读 `View`，返回结构化 `Decision`：新增/修改 Goal、Item、依赖边、验收 Goal、暂停或终止 Item、Run 是否收口。Planner 不直接写 SQLite。

`View` 至少包含 Run 目标、Goals、Items、Attempt 摘要、最近事件、剩余预算、是否停摆和是否收尾。Decision 必须通过 schema、工作区白名单、Agent 名称、依赖存在性、DAG 环检测和数量上限校验后才落库。

首次规划已由编程模型完成接入。Planner 使用专用的结构化输出提示和 JSON schema，只允许返回目标摘要、Item 列表、依赖 ID、验收说明、Agent 和工作目录；模型不能直接写 SQLite，也不能把任意路径或凭据带入 Item。模型可以调用现有 `update_plan` 生成用户可见计划，但生产调度器将规划结果转换为 Item DAG；`update_plan` 不再是唯一的任务状态来源。

规划流程分两步：先记录 `planning` 事件并调用 Planner，再在同一 Run 内事务性写入完整 Item 批次。规划结果必须通过 Item 数量上限、非空 instruction、依赖存在性、环检测、工作区路径和 Agent 白名单校验。校验失败时保留 `planning_error` 事件，回退为原始目标的单 Item，并继续走现有执行器。模型不可用、返回非 JSON 或超时采用同一回退路径，不能把用户任务误报为已完成。

Planner 不重复执行任务，也不在首次规划阶段修改已经运行的 Item。后续任务追加或修复 Item 需要单独的 steer/repair 设计；这部分仍未实现。

### 5.2 Scheduler

Scheduler 当前已负责：

- 收到新 Run、Item 结算、人工 steer/kill、租约过期和心跳事件后唤醒 Planner；
- 用 debounce 合并短时间内的多个事件；
- 扫描 frontier，按 priority 和依赖就绪程度派发 Item；
- 控制 Run 的 `max_workers`，不超过全局 worker 上限；
- 在 Run 终态或取消时停止继续 claim。

当前实现以单进程 polling 和线程池为主；事件 debounce、跨进程唤醒和完整的 steer/kill 触发链仍待补齐。

Scheduler 不执行模型调用，也不直接执行 shell。跨进程唤醒使用 SQLite 事件轮询加短间隔退避；不引入消息队列。

### 5.3 Worker

Worker 每次只执行一个 Item。它创建独立的 Agent 会话，输入包括：Run 目标、Item instruction、acceptance、前置 Item 结果摘要、工作区和必要的 Skill/MCP 配置。

执行器适配器统一返回 `done`、`blocked`、`retryable_error`、`exhausted` 或协作式 `stopped`。内置 Runtime 复用现有模型工具循环；CLI 适配器复用 `agent.py`。Worker 不直接修改依赖关系或其他 Item。

为避免并行 Agent 改同一文件，Item 必须携带 `cwd` 和可选的文件范围元数据；Planner 在生成 DAG 时负责声明互斥范围。发现范围冲突时，Run Manager 拒绝并要求串行化。

### 5.4 Monitor

目标 Monitor 需要定期执行三类检查：

- lease 到期：将没有完成回写的 `running` Item 转为 `open`，增加恢复 Attempt 和事件；
- 停摆：Goal 未完成、没有可执行/运行中的 Item，且 Planner 连续若干轮没有新增有效 Item 时，Run 标记 `failed`；
- 收口：取消、超时或 Goal 达成后停止领取，等待有限 drain 时间，再将未完成 Item 标为 `stopped`。

Python 无法强制终止任意线程，因此 Worker 必须在工具边界检查取消信号；超出 drain grace 的外部 CLI 由适配器终止进程。

当前仅有 lease 恢复和基础 deadline 检查；停摆检测、有限 drain、最终验收 Planner 和完整收口顺序尚未实现。

## 6. 重试与收口

默认 `max_attempts=2`，可在 Item 上覆盖但设上限。可重试错误包括 worker 崩溃、网络临时错误、模型限流和租约丢失；路径越权、用户拒绝、参数校验失败和明确的业务失败默认 `blocked`。达到上限转 `exhausted`。

Item `blocked` 或 `exhausted` 后，依赖它的下游 Item 默认转 `blocked`，并记录阻断原因。Planner 可以在下一轮显式替换失败 Item 或追加修复 Item，但不能复活已有 Attempt。

Run 收口顺序固定为：停止新 claim → 等待在跑 Item drain → 运行一次 `final=True` Planner，只允许验收或标记失败 → 写 Run 终态 → 唤醒所有 worker 退出。Goal 已达成优先于 deadline 的超时状态。

## 7. 现有入口迁移

### Web

Web 消息创建 Run 后交给 Run Manager。当前 `WebState.start → Runtime.run` 的直连路径已经由 TaskManager 接管；本阶段只补上“创建 Run → Planner → Scheduler → Worker”中的首次 Planner。SSE 继续读取 `events`，前端的 `plan` 视图同时显示规划状态、Item 数量和 Item 依赖；旧事件格式继续支持历史运行记录。

### 灵矽 MCP

保留 `self.code.run` 参数兼容。简单任务创建单 Item Run；复杂任务让云端灵矽提交目标，或由本地 Planner 拆 DAG。返回内容包含 Run ID、当前状态和已提交 Item 数。

新增内置工具：

- `self.task.status(run_id?)`：返回 Run、Goal、Item、当前 Attempt 和最近错误摘要；
- `self.task.stop(run_id?)`：停止 Run，触发有序收口；
- `self.task.resume(run_id?)`：只恢复 `paused` 或可恢复失败的 Run，重新校验工作区和配置。

现有 `self.code.result/stop` 转发到对应 `task.status/stop`，避免灵矽提示词立刻失效。新增工具仍通过设备 MCP 暴露给灵矽；工作台 MCP 页面继续只管理外部 MCP 服务，内置调度能力在“任务/运行记录”页面展示。

### 记忆与 Skill

记忆工具仍属于设备内置 MCP。Worker 的 Agent 会话可以按配置加载 Skill 和外部 MCP；Planner 只接收工具结果摘要，不直接读取 Mem0。是否自动检索/保存记忆由 Agent 的系统提示词和工具策略决定，调度器不把记忆伪装成调度 Item。

## 8. 恢复、并发与安全

- 服务启动先扫描过期 lease 和上次未收口的 Run；可恢复 Item 回到 `open`，不可恢复 Run 标记 `failed` 并写明原因。
- 同一工作区默认只允许一个写入型 Item 并发；只有 Planner 声明文件范围互斥且配置允许时，才提高 `max_workers`。
- `cwd` 必须在配置 workspace 下；Agent 名称必须来自配置；环境变量和凭据不写入 Item、Attempt 或 Event。
- 所有用户可触发的 stop/resume/steer 都检查 Run 所属 session 和当前设备身份。
- Worker 输出和模型结果按现有长度上限截断，完整日志不进入语音响应。

## 9. 迁移顺序

1. 扩展 schema 和 Store：迁移、Goal/Item/Attempt、事件、依赖、CAS claim、lease 恢复。
2. 实现 DAG 校验、Decision 应用器和单进程 Scheduler；用一个 Worker 运行现有 Runtime。
3. 接入 Planner 和 Item Worker，先支持单 Item 兼容路径，再启用多 Item DAG。
4. 将 Web 和 `self.code.*` 切换到 Run Manager；保留旧事件投影和 CLI 行为。
5. 加入多 worker、重试、暂停/恢复、超时收口和跨进程恢复。
6. 更新工作台运行记录、任务状态和灵矽提示词；增加结构化 smoke 验证。
7. 运行稳定后，把 `design/`、`design2/` 标记为历史设计并迁移有价值的章节；确认没有引用后再归档或删除。

## 10. 验证

必须覆盖会破坏用户数据或执行命令的边界，测试继续遵守项目约定，集中到现有 smoke 入口或明确的调度 smoke 文件：

- DAG 环检测、缺失依赖和依赖状态计算；

自动规划阶段还必须覆盖：结构化规划成功生成多个 Item、无效 JSON/超时/非法路径回退单 Item、独立 Item 在 worker 上并行、依赖 Item 等待前置完成、规划事件与 Item 投影可通过旧 SSE/API 读取，以及规划失败不会伪装成成功。
- 两个 worker 同时 claim 同一 Item 只有一个成功；
- lease 过期恢复、进程重启恢复和 Run 终态禁止新 claim；
- retryable/blocked/exhausted 的状态转移和下游阻断；
- cancel/stop 的 drain 与 CLI 终止；
- `self.code.run/result/stop` 兼容单 Item 调用；
- 旧会话事件和新 Item 事件都能被 Web/SSE 展示。

不为 getter、配置读取或 UI 胶水增加测试；每个协议、并发和执行边界只保留一个能证明行为的 smoke 场景。

## 11. 与设计目录的关系

生产实现吸收：

- `design/kernel-design.md`：Goal/Item/Event/Run、CAS claim、View → Decision、lease/收口/停摆语义；
- `design2/README.md` 与 `design2/kernel.py`：调度、规划落地、监察分层和有界快照。

生产实现不直接复用：两个目录中的内存 Store、Demo Planner/Runner、独立线程编排和实验 API。它们在迁移完成前保留，作为设计来源；迁移完成后更新文档索引，再根据 git 历史和引用情况归档或删除。
