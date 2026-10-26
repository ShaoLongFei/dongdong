# 任务调度内核 · 设计

> 把 ARTEX 的探索引擎剥成「拆解 → 派发 → 监察」三件事，领域知识全部外置。
> 本文是给实现者（人或 AI）看的规格；`kernel/` 是可运行骨架，跑法见 §9。

---

## 0. 怎么读

| 你想做什么 | 读哪几节 |
| --- | --- |
| 判断这套东西该不该用 | §1 目标与非目标、§12 已知缺口 |
| 接一个自己的执行器 | §4.3 Runner、§10.3 |
| 接一个自己的规划者 | §4.2 Planner、§10.2 |
| 换存储 / 加持久化 | §4.1 WorkStore、§10.4 |
| 改内核本身 | §5 时序、§7 生命周期、§8 并发 |
| 看它跟 ARTEX 什么关系 | §2 |

---

## 1. 目标与非目标

**目标**：给定一句目标（"把 README 改成中文"），内核负责

1. **拆解** —— 把目标展开成若干可独立派发的工作项，以及可验收的子目标；
2. **派发** —— 按优先级把工作项分给 N 个并发执行者，保证一项同时只有一个执行者；
3. **监察** —— 盯着执行情况：合并事件、定期巡检、纠偏在跑的、终止跑偏的、判定停摆、验收目标、收尾。

**非目标**（明确不做，别往里加）：

- 不实现 agent loop。执行者是外部程序（CLI、HTTP、子进程），内核只负责调用与收结果。
- 不做领域知识。资产、漏洞、仓库、文件、需求单——全是领域包的事。
- 不做多机调度、队列服务、消息中间件。单进程内的线程池。
- 不做 UI。状态通过 `WorkStore` 暴露给面板/CLI 去读。

**成功判据**：换一个领域（渗透 → 写代码 → 运维巡检），内核代码零改动，只换 `WorkStore` 实现、`Planner` 实现、`Runner` 实现。

---

## 2. 从 ARTEX 搬了什么、扔了什么

内核不是新设计，是把 ARTEX `server/engine*.go` 里被验证过的调度机制抽出来。对照表：

| ARTEX | 内核 | 搬了什么 |
| --- | --- | --- |
| `server/engine.go#plannerLoop` | `kernel/scheduler.py#_planner_loop` | 事件驱动 + debounce 合并 + 心跳兜底 + 唤醒重臂 |
| `server/engine.go#workerLoop`、`#claimNext` | `_worker_loop`、`_claim` | 并发池 + frontier 扫描 + CAS 抢占 |
| `server/engine.go#runIntent` 终态分流 | `kernel/settle.py#settle_item` | 暂停回队列、被杀转 stopped、失败/耗尽分流 |
| `server/engine_timeout.go` | `settle.py#settle_run`、`#deadline_loop` | settling 状态、drain 宽限、最后规划轮 |
| `server/engine.go` 的 work/steer 控制 | `Kernel.pause/kill/steer` + `Run.actions` | 单项干预不影响整个任务 |
| `db/exploration.go#Frontier/ClaimIntent/CompareAndSetIntentState` | `store.py#WorkStore` | 队列语义；SQL 换成接口 |
| `db/schema.sql` 的 `ck_node_kind` / `ck_node_state` | `model.py` 的枚举 | goal/intent/fact/finding → goal/item |
| `agent/planner.go#Plan` | `planner.py#Planner` | 但把「工具直写库」收紧成 `View → Decision` |
| `agent/worker.go#Execute` | `runner.py#Runner` | 但只管调外部程序，prompt 归执行者 |
| `server/manager.go#Task` 生命周期 | `kernel/run.py#Run` | 只留状态与干预，队列/并发上限没搬 |

**没搬的**（不是遗漏，是判过不值得）：

| 部件 | 不搬的理由 | 想要时怎么办 |
| --- | --- | --- |
| 全局资产图、`exploration_anchors`、覆盖度 | 纯领域数据 | 领域包自己建表，`WorkStore` 加查询方法 |
| finding 生命周期、复测、报告 | 领域业务 | 把「产出物」当成 `Result.summary` + 外部存储 |
| 流量录制、证据库、拦截门禁 | 渗透专用 | 不要 |
| digest 冷分支压缩 | 复杂，先用全量 `items` 顶着 | §12 列为后续 |
| transcript / LLM 会话持久化 | 执行者自己管 | 执行者写自己的日志 |
| `tools` 表动态绑定（换工具不用改代码） | 那是领域包的机制 | 内核只需要三个接口 |
| 任务队列（并发上限、排队唤醒） | 单 run 够用 | §12 列为后续 |
| 父子任务编排（主 agent spawn 子任务） | 同上 | §12 |

---

## 3. 领域模型

### 3.1 四个概念

| 概念 | 是什么 | 谁写 |
| --- | --- | --- |
| **Goal** 目标 | 一条可验收的完成条件。`open / met / abandoned` | 规划者新增与验收；内核初始建一条 |
| **Item** 工作项 | 一条可派发的活。`open / running / done / blocked / exhausted / stopped` | 规划者新增；内核改状态 |
| **Event** 事件 | 「发生了什么」，用来唤醒规划者。`done/blocked/exhausted/goal_met/stall` | 内核产生 |
| **Run** 运行 | 一次任务的容器：目标 + 队列 + 线程 + 预算 | 内核 |

关系：一个 Run 有 1..N 个 Goal、0..N 个 Item；Item 可以挂在 Goal 下（`parent`），但内核不强制——父子只影响规划者怎么看，不影响调度。

### 3.2 状态机

```
Item

  open ──claim──▶ running ──done──────▶ done
   ▲               │  │
   │               │  ├──blocked─────▶ blocked     （出错，可重试）
   │               │  ├──exhausted───▶ exhausted   （预算耗尽，方向试过）
   │               │  └──kill────────▶ stopped     （外部终止，不回写结果）
   │               │
   └──pause────────┘  （任务暂停：回队列，恢复后重新领取）
```

```
Goal:  open ──prove──▶ met
        open ──规划者放弃──▶ abandoned   （暂未实现入口）
```

```
Run:  created ──start──▶ running ⇄ paused
                │
                ├──target met──────────▶ done
                ├──deadline 到点───────▶ timeout
                ├──cancel─────────────▶ canceled
                └──连续 N 轮无产出─────▶ failed
```

三个终态的区别很重要：`done` 是目标达成；`timeout` 是时间到（可能没做完）；`failed` 是**卡住了**（还有目标没验，但规划者连续产不出活）。把它们混成一个 `done` 会让「看似完成、实则停摆」的任务永远沉默。

### 3.3 三条不变量

1. **一项一执行者**。`claim` 是 CAS：`open → running` 成功才返回，失败说明别人抢到了。内核靠这条做到无锁派发。
2. **终态门**。Run 进终态后：worker 不再 claim，planner 不再规划，在跑的项一律转 `stopped`。没有这条，任务完成后 frontier 里的遗留项会被继续跑。
3. **单写者规划**。同一时刻只有一轮规划在跑（`_plan_lock`）。规划者读到的 `View` 在该轮内不会变，不用处理并发写。

---

## 4. 接口

### 4.1 WorkStore

内核唯一的数据入口。`Protocol` 定义在 `kernel/store.py`，骨架实现 `InMemoryStore`。

```python
class WorkStore(Protocol):
    # 目标
    def add_goal(self, text, *, parent=None) -> int: ...
    def goals(self, state=None) -> list[Goal]: ...
    def prove_goal(self, goal_id, *, by_item=None, evidence="") -> bool: ...

    # 工作项
    def add_item(self, text, *, priority=0, parent=None, meta=None) -> int: ...
    def get_item(self, item_id) -> Item | None: ...
    def items(self, state=None) -> list[Item]: ...
    def frontier(self, limit=20) -> list[Item]: ...
    def claim(self, item_id, owner) -> bool: ...
    def transition(self, item_id, frm, to, *, result="", reason="") -> bool: ...
```

**契约**：

- `frontier` 返回 `state=open` 的项，按 `priority DESC, id ASC`；`limit` 是扫描窗口，不是并发上限。
- `claim` 必须原子：一条 `UPDATE ... WHERE id=? AND state='open'`，受影响行数为 1 才算抢到。返回 `True` 时把 `owner`、`attempts+1`、`started_at` 一起写掉。
- `transition` 是 CAS：当前状态不等于 `frm` 就返回 `False`，**不要**覆盖。内核靠这个处理「规划者 kill 与执行者写回同时发生」。
- `prove_goal` 只对 `open` 的目标生效，重复验收返回 `False`。
- 所有方法都要线程安全。SQL 实现天然安全；内存实现用一把锁。

**为什么是接口而不是直接用 SQL**：内核要在「空实现 + 演示执行器」下跑通（现在就是这样），也要能在 dongdong 里换成 sqlite。如果内核直接写 SQL，这两件事都得改内核。

### 4.2 Planner

```python
class Planner(Protocol):
    def plan(self, view: View) -> Decision: ...
```

`View` 是**只读快照**，规划者这一轮能看到的一切：

```python
@dataclass
class View:
    goal: str            # 任务原话
    goals: list[Goal]    # 全部目标（含状态）
    items: list[Item]    # 全部工作项（含状态、尝试次数、结果摘要）
    triggers: list[Event]  # 本轮为什么被唤醒
    round: int
    stalled: bool        # 有 open 目标，但既没在跑的也没排队的
    final: bool          # 收尾轮：只判目标，别产新活
```

`Decision` 是**唯一写入口**：

```python
@dataclass
class Decision:
    add_goals: list[NewGoal]
    add_items: list[NewItem]
    prove: list[tuple[int, str]]     # (goal_id, 证据)
    steer: list[tuple[int, str]]     # (item_id, 一句话纠正)
    kill: list[int]                  # item_id
    finish: bool
    reason: str
```

**落地顺序由内核固定**，不让规划者决定：先 `kill`/`steer`（影响在跑的），再补目标与工作项，最后验收与收尾。这样「同一轮里既 kill 某项又新增某项」不会打架。

**契约**：

- `plan` 可以抛异常。内核捕获、记一行日志、下一轮重试——规划者崩一次不该杀掉整个任务。
- `plan` 必须能在 `final=True` 时工作：收尾轮不允许产生新工作项（内核不会拦，但按约定不该产）。
- `plan` 不要阻塞太久。它占着规划锁，阻塞期间 worker 照常跑，但整个任务的心跳会被推迟。

### 4.3 Runner

```python
class Runner(Protocol):
    def run(self, item: Item, ctx: RunContext) -> Result: ...
```

`RunContext` 是执行者能拿到的一切：

| 方法 | 语义 |
| --- | --- |
| `ctx.emit(text)` | 打一行进度，落到 `Kernel.on_output` |
| `ctx.steer()` | 取一条纠正；没有返回 `None`。**每个步骤边界调一次** |
| `ctx.should_stop()` | 是否该立刻收手（暂停/被杀/超时/任务结束） |
| `ctx.remaining()` | 距 deadline 还有几秒；无 deadline 返回 `None` |

`Result` 只有三种 `kind`：`done` / `blocked` / `exhausted`。`stopped` 由内核根据控制指令推断，执行者无权返回。

**契约**：

- 必须协作式响应 `ctx.should_stop()`。内核没有强制 kill 线程的手段（Python 限制），不响应的执行者会拖到 drain 宽限结束。
- 一次 `run` 只处理**一项**。需要长任务就拆成多项，别在里面循环。
- `blocked` 与 `exhausted` 的区别要守住：前者是"出错了"，后者是"预算用完了"。规划者对两者的反应不同——前者可能重试，后者通常换方向。

### 4.4 Kernel

```python
Kernel(store, planner, runner, *, workers=3, debounce=0.8, heartbeat=600.0,
       empty_poll=0.2, stall_limit=3, drain_grace=90.0, logger=print)

kernel.submit(goal, timeout=None) -> Run
kernel.start(run)
kernel.wait(run, timeout=None) -> RunState
kernel.pause(run) / kernel.resume(run) / kernel.cancel(run)
kernel.steer(run, item_id, msg) / kernel.kill(run, item_id)
```

参数都是**按域要调**的，不是定值：

| 参数 | 默认 | 含义 | 什么时候改 |
| --- | --- | --- | --- |
| `workers` | 3 | 并发执行者数 | 受外部资源限制（几个 CLI 并发不炸机） |
| `debounce` | 0.8s | 事件合并窗口 | 执行器太碎就调大 |
| `heartbeat` | 600s | 无事件时多久巡检一次 | 渗透场景 10 分钟合适；交互式任务应降到 30–60s |
| `empty_poll` | 0.2s | 队列空时 worker 的空转间隔 | 别低于 0.05s |
| `stall_limit` | 3 | 连续几轮无产出判停摆 | 规划者可能合理地连空几轮时调大 |
| `drain_grace` | 90s | 收尾时等在跑项的上限 | 交互式任务降到 5–10s |

### 4.5 为什么规划者不直接写库

ARTEX 里规划者是一个带工具的 agent，`add_intent` / `prove_goal` / `kill_work` 直接改库，引擎只管唤醒。这套能跑，但代价是：

- **副作用不可重放**。要复现"这一轮为什么这么规划"，得把整条工具调用链录下来。
- **验证不了**。写单测要给规划者造一个假 store，等于把库也搬进测试。
- **换规划者就得改工具集**。工具是代码里的闭包，绑死在探索库上。

改成 `View → Decision` 后，规划者退化成纯函数：同样的 `View` 给两次，得到同样的 `Decision`。录制、回放、对比、单测都变成 `assert`。代价是规划者不能在一轮里"边查边改"——它得先把要做的都算出来。对拆解-派发这个场景，这个约束是良性的。

---

## 5. 一轮调度

### 5.1 时序

```
       提交                ┌──────────── planner 线程 ────────────┐
  submit ──▶ start ──▶ wake │ 等唤醒 ─▶ debounce 合并 ─▶ plan()   │
                             │                              │      │
                             │        Decision ◀────────────┘      │
                             │            │                        │
                             │      ┌─────▼──────┐                 │
                             │      │ _apply:    │  写库（CAS）     │
                             │      │ kill/steer │                 │
                             │      │ +目标/+项  │                 │
                             │      │ 验收/收尾  │                 │
                             │      └─────┬──────┘                 │
                             └────────────┼────────────────────────┘
                                          │ 新项落库
        ┌──────────── worker 线程 × N ────▼────────────────────────┐
        │ claim(CAS) ─▶ runner.run() ─▶ settle_item ─▶ 事件 ──────┼──▶ 唤醒 planner
        └──────────────────────────────────────────────────────────┘
```

### 5.2 唤醒与合并

三个唤醒源：**事件**（worker 结算、外部 steer/kill）、**心跳**（兜底巡检）、**起跑**（`start` 立刻来一轮）。

事件驱动的问题是一阵变动会连着来。比如 3 个 worker 同时结束，不去重就是 3 轮规划、3 倍 token。所以每次被唤醒后先等 `debounce` 秒，期间的唤醒全部丢弃，只跑一轮。

心跳是**兜底**：

- 死锁兜底——事件丢了、执行者卡死了，至少还有周期巡检；
- 监督兜底——定时检查在跑的项是否跑偏（这时规划者该 `steer` 或 `kill`）；
- 每次唤醒都会重臂心跳，所以静置时长从最后一次变动算起。

一个容易踩的坑：**验收完目标但没补新活时，如果不主动再唤一轮，就要白等一个心跳周期**。内核在 `_apply` 里处理了——`Decision` 只验收不补活时立刻 `wake`，直接进下一轮。这条是跑骨架时发现的，ARTEX 对应位置靠规划者总是顺手补活掩盖了。

### 5.3 活跃性：停摆检测

任务卡住的典型形态：还有目标没验，队列空着，也没东西在跑。这时没人会再唤醒规划者，除了心跳。

ARTEX 把这条规则写在 prompt 里（`agent/planner.go` 的 user 文本：「frontier 抽干且无在跑意图时，本轮必须产出至少一个意图」）。问题是它不是代码强制——换领域重写 prompt 时必然丢，丢了就静默停摆。

内核把它移到代码：

```
连续 stall_limit 轮：open 目标 > 0 且「没有 open/running 项」且本轮没补任何活
  → Run 落 failed，reason = "连续 N 轮无产出，判定停摆"
```

同时在 `View.stalled` 里把信号明明白白交给规划者。这样既能让规划者自己救，也能在它救不回来时给一个可观测的失败，而不是永远 `running`。

---

## 6. 监督

监督不是"定时看一眼"，是三个各有触发条件的手段：

| 手段 | 触发方 | 作用 | 副作用 |
| --- | --- | --- | --- |
| `steer(item, msg)` | 规划者或人 | 往在跑的实现里插一句话，执行者在下一个步骤边界收到并调整 | 无，不重启、不丢上下文 |
| `kill(item)` | 规划者或人 | 终止一项，结果丢弃，状态转 `stopped` | 该项不会再被领取 |
| `pause(run)` / `cancel(run)` | 人 | 暂停/取消整个任务 | 见 §7 |

**为什么 steer 比 kill 重要**：执行者往往只差一点就对了。kill 掉重来要重花一遍预算，steer 只花一句话。ARTEX 里 steer 是通过 `PreToolUse` 钩子在下一次工具调用前塞进消息的；这里抽象成"执行者在步骤边界主动取"，对子进程执行器同样成立（在下一个 prompt 里带上）。

**监督的质量取决于规划者看到什么**。`View` 里给了 `attempts`、`result`、`reason`、`triggers`——都是为了让规划者判断"这项是跑偏了还是快好了"。如果只给状态不给结果摘要，规划者只能瞎猜。

---

## 7. 生命周期

### 7.1 暂停 / 恢复

`pause(run)`：状态转 `paused`；给所有在跑的项打 `pause` 标记；worker 停止领取。

在跑的项收到 `ctx.should_stop() == True`，返回，`settle_item` 看到 `pause` 标记，把项**退回 `open`**（不是 `stopped`）。这样恢复后会被重新领取。

代价：执行者要从头再跑一遍，除非它自己能从工作目录续上。真做续跑需要执行者自己负责 checkpoint——内核只保证"这项回到了待领队列"。

### 7.2 单项终止

`kill(run, item)`：在跑的转 `stopped`（不写回结果）；还没跑的直接从 `open` 转 `stopped`。

### 7.3 有序收尾（超时 / 取消）

不能直接置终态——在跑的项还在写。`settle_run` 走五步：

```
1. 置 settling（worker 不再领新项，planner 丢弃普通唤醒）
2. 等在跑项自然结束，最多 drain_grace
3. 给规划者最后一轮（final=True）：只判目标、不产新活
   —— 这一轮可能把任务判成 done，那就以 done 为准
4. 置终态（timeout / canceled / 原定状态）
5. stop 事件唤醒所有线程退出
```

第 3 步是 ARTEX 的一个细节，值得保留：超时的任务不该无条件记 `timeout`，如果最后一轮发现目标其实已经达成，应该是 `done`。否则会出现"任务明明做完了，因为差 2 秒被记成超时"。

第 2 步的等待可能超时（执行者不响应停止）。超过宽限后，`finish` 会给还在跑的项打 `kill`，它们下一次轮询 `should_stop` 时退出，结果被丢弃。**Python 没有强制终止线程的手段**，这是硬限制，只能在文档里要求执行者配合。

---

## 8. 并发与正确性

| 场景 | 保护手段 |
| --- | --- |
| 两个 worker 抢同一项 | `claim` 的 CAS；只有一个 `True` |
| 规划者 kill 与执行者写回同时 | `transition` 的 CAS + `settle_item` 先 pop 控制指令再判 |
| 暂停与执行者完成同时 | 同上；`pause` 标记优先 |
| 同一轮两个规划 | `_plan_lock` |
| 收尾与 worker 领取同时 | worker 循环每轮检查 `settling` / `terminal` |
| 关停时写坏数据 | `inflight` 计数 + drain 等待 |

**加锁顺序**：`run.lock` → store 的内部锁。反过来会死锁（`finish` 先拿 run.lock 再查 store）。

**不在锁内做慢事**：`runner.run()` 绝不持锁；`plan()` 只持 `_plan_lock`（不是 `run.lock`）。

**已知的弱保护**：`Run.terminal` 是个无锁读的属性。读到旧值最坏是多跑一轮空转，不会写坏数据。

---

## 9. 代码地图 & 跑起来

```
design/
├── kernel-design.md      # 本文
└── kernel/
    ├── model.py      136  枚举 + 数据类（Goal/Item/Event/Decision/Result）
    ├── store.py      148  WorkStore 契约 + InMemoryStore
    ├── planner.py     93  Planner 契约 + DemoPlanner
    ├── runner.py     130  RunContext + Runner 契约 + DemoRunner + SubprocessRunner
    ├── run.py         62  Run：状态、锁、事件、steer 队列
    ├── scheduler.py  287  Kernel：线程编排、规划循环、执行循环、干预
    ├── settle.py      76  单项结算、超时线程、有序收尾
    └── demo.py        54  跑一遍：两波拆解 + 一次耗尽重试 + 一次外部纠正
```

行数受 dongdong 的「单文件 ≤ 300 行」约束。

**跑**：

```bash
cd design
python3 -m kernel.demo          # 或 ../.venv/bin/python -m kernel.demo
```

预期输出：3 个 worker 并发、`翻译正文` 第一次耗尽预算、规划者补重试项、外部纠正被中途接收、两个目标依次验收、2–4 秒内 `done`。

**已手动验证的路径**（骨架附带，不是测试文件）：

| 路径 | 预期 |
| --- | --- |
| 正常完成 | `done`，目标全 `met` |
| `pause` → `resume` | 暂停后无 `running` 项，恢复后跑完 |
| `cancel` | `canceled`，在跑项 `stopped` |
| 超时 | `timeout`，在跑项 `stopped` |
| 规划者连续空转 | `failed`，reason 含「停摆」 |
| `kill` 单项 | 该项 `stopped`，其余继续 |

---

## 10. 落进 dongdong

### 10.1 迁移顺序

不要一次全接。按这个顺序，每步都能单独验证：

1. **搬代码**：`design/kernel/` → `apps/device-mac/dongdong/tasks/`。改 import，跑 `python -m dongdong.tasks.demo` 确认还活着。
2. **只换执行器**：`SubprocessRunner` 接上 `config.py` 里已有的 `agents` 配置（`cmd` / `timeout_s`）。此时规划者仍是 `DemoPlanner`，但派发的活已经真的在跑 codex。
3. **接面板**：`panel.py` 的 `POST /task` 从「起一个 `agent.Job`」改成「`kernel.submit()` + 后台 `start()`」；`GET /state` 里加 run 的目标、工作项、进度。此时「一次只跑一个任务」的技术债顺手还掉。
4. **换规划者**：见 §10.2。到这一步才真正有「拆解」能力。
5. **持久化**：见 §10.4。

第 2 步就能产生价值——面板上能看到多项并发、能 steer、能 kill。规划者是不是 LLM 可以晚点决定。

### 10.2 规划者从哪来

这是 dongdong 落地时最实际的问题：**dongdong 现在没有自己的 LLM 调用通道**。它所有智能都来自灵矽平台（语音链路，MCP 回调回来），平台不暴露"给我一段文本"的 API。

四个选项，按建议顺序：

| 方案 | 做法 | 代价 | 建议 |
| --- | --- | --- | --- |
| **让 CLI agent 自己拆** | 跑一次 `codex exec --json "只输出 JSON 计划，不要执行"`，把结果转成 `Decision` | 多一次 LLM 调用；格式要约束 | 首选。零新依赖，复用已配好的 agent |
| **规则拆解** | 固定模板：一句话 → 「读代码 → 改 → 测 → 提交」四项 | 不智能，但零成本、可预测 | 先跑通链路时用 |
| **直连 LLM API** | 用已有的 `DEEPSEEK_API_KEY` 调 chat completions | 多一条网络依赖；要处理 key 管理 | 需要真正的规划能力时 |
| 平台内拆 | 平台侧不提供 | — | 不可行 |

注意：规划者不一定要 LLM。**规则规划者也是合法实现**，而且它不会连续空转、不会乱 kill。先用规则跑通，再换 LLM，比反过来稳。

### 10.3 执行器接 CLI agent

`SubprocessRunner` 已经把形状给出来了：

```python
SubprocessRunner(["codex", "exec", "--cd", repo, "{prompt}"], timeout_s=600)
```

和现有 `agent.py` 的差别：

- 现在是「一条指令 → 一个 Job」，之后是「一项工作项 → 一次调用」。工作项文本要自带上下文（"在 repo X 里做 Y"），因为执行器之间不共享会话。
- `ctx.emit` 打到面板的实时输出，替代 `Job.output`。
- `ctx.steer()` 对子进程表现为「下一项的 prompt 里带上纠正」；同一进程内插话需要 CLI 支持 stdin，多数不支持，接受这个限制。
- `ctx.should_stop()` 由 watchdog 线程 `proc.kill()` 实现，绑定 `timeout_s` 和任务的 deadline。

### 10.4 持久化

骨架是内存态，进程退出即丢。落地方案：

- **sqlite3**（标准库，不违反"不加依赖"）：`~/.dongdong/tasks.db`。`claim` 写成单语句 CAS，`transition` 带 `AND state=?`。
- 表就三张：`runs`、`goals`、`items`。别急着抄 ARTEX 的 58 张表。
- 恢复语义：进程重启时把 `running` 的项扫回 `open`（执行者已经死了），`Run` 状态恢复成 `running` 并重新 `start`。这条要在实现时明确测试，否则重启会留下永久 `running` 的僵尸项。

### 10.5 和现有面板的关系

- `state.py` 继续管连接/对话/工具调用记录；新增一个 `tasks` 段落读 `WorkStore`。
- 面板的"一次只跑一个任务，忙时 409"可以放宽成"最多 N 个 run"，或者干脆单 run 但内部 N 个 worker——后者对用户更简单（还是一个任务，只是跑得快）。
- **别把内核做成独立进程**。`decisions.md` 第 8 条的结论仍然成立：状态在内存里，跨进程就要加 IPC。

---

## 11. 与 AGENTS.md 的冲突

这份设计与 `AGENTS.md` 有两处正面冲突，不回避：

| 条款 | 冲突点 | 处理 |
| --- | --- | --- |
| §7「只有一个实现的接口/ABC」黑名单 | `WorkStore` / `Planner` / `Runner` 现在各只有一个实现 | **显式例外**，理由见下 |
| §7「为了通用化而做的重命名」黑名单 | 本设计的目的就是通用化 | 例外，但限定在 `design/` 与将来的 `tasks/` 模块内 |

**为什么该例外**：那两条规则防的是"为想象中的第二个调用方写抽象"。这里第二个实现是**具体的、已排期的**——`SubprocessRunner` 要接 4 个 CLI agent，`WorkStore` 要接 sqlite，`Planner` 至少要有规则版和 LLM 版。三个接口都对应真实的、已知的第二个实现，不是想象。

**但保留退路**：如果最终只落地规则规划者 + 内存 store，就把 `Protocol` 删掉，只留 §4 的契约表——文档仍然是规格，代码不用背着空接口。这条写在这里，提醒后来的人别把接口当成教条。

**另外**：`design/` 不在 AGENTS.md §4 的目录职责表里，不参与 `./scripts/check.sh`。搬进 `apps/` 时再补一次规范评审。

---

## 12. 已知缺口与风险

**没做的**（按价值排序，都可能下一版加）：

| 缺口 | 影响 | 补法 |
| --- | --- | --- |
| 无持久化 | 重启丢任务 | §10.4 |
| `Decision` 只能新增，不能修改/删除工作项 | 规划者改不了自己上一轮写错的项 | 加 `update`/`drop` 字段 |
| 无 `artifacts` 落盘 | 产出只在 `Result.summary` 里，长产出会丢 | `Result` 加 `artifacts: list[Path]`，存 run 工作目录 |
| 无 token/预算分配 | 一项吃光所有预算 | `Item.budget` + 执行者自报用量 |
| 无重试退避 | 反复失败会反复重领 | 规划者负责，内核不自动重试 |
| 无 digest 压缩 | `items` 全量进 `View`，上百项时 prompt 爆 | 移植 ARTEX 的冷分支压缩 |
| 无父子 run | 大目标拆成子任务各自调度做不到 | 引入 `run.parent_id` |
| 单进程多 run 未验证 | 多任务会抢同一 store | 实现支持，没测过 |

**风险**：

1. **执行者不配合停止**。Python 无法强杀线程，drain 宽限是唯一的兜底。子进程执行器必须由 watchdog 杀，别指望它自己退出。
2. **规划者质量决定上限**。内核只保证调度正确，不代表拆得对。规划者每轮全量读 `items`，项多了会又慢又贵。
3. **心跳值是最容易配错的参数**。600s 来自渗透场景（扫一轮就几分钟），套到交互式任务上会显得"卡住不动"。
4. **`View` 是快照，不是事务**。规划者基于快照做的决策，落地时状态可能已经变了——`transition` 的 CAS 会挡住冲突，但被挡掉的那条 `Decision` 会静默失效。目前只记日志，没有重试。
5. **没有背压**。规划者一轮可以产出 100 项，内核照单全收。加个上限（比如单轮 ≤ 20）会更稳。

---

## 附录：关键设计取舍一览

| 取舍 | 选了 | 没选 | 理由 |
| --- | --- | --- | --- |
| 规划者形态 | `View → Decision` 纯函数 | 带工具的 agent 直写库 | 可回放、可单测、换域只换实现 |
| 并发模型 | 线程池 | asyncio | 执行者是子进程/阻塞 IO；线程最直接；dongdong 已有先例 |
| 状态机放哪 | store 的 CAS | 内核内存状态 | 一个 run 一个状态源，重启/多进程才对 |
| 停摆检测 | 代码 + `failed` | prompt 约定 | 约定会丢，失败要可观测 |
| 收尾 | 五步有序 + 最后规划轮 | 直接置终态 | 别把做完了的任务记成超时 |
| 存储 | 接口 + 内存实现 | 直接 sqlite | 先验证接口形状，换实现不改内核 |
| 领域知识 | 外置 | 内嵌 | 这是整个设计的立身之本 |
