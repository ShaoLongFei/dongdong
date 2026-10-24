# 前台面板 · 交接单

> 用途：给接手的人/AI 一份「做到哪了、还差什么」的清单。
> 面板上线并验收通过后，删掉本文件（规范只留 architecture / decisions / runbook 这些长期文档）。

## 0. 一句话目标

`dongdong run` 跑起来时，本机浏览器打开 `http://127.0.0.1:8766` 就能看到：连接状态、最近一次语音对话、当前 agent 任务的进度和实时输出、最近 30 次工具调用；页面底部可以直接派任务给 agent。

## 1. 已决策的边界（别扩大）

| 决定 | 理由 |
| --- | --- |
| 面板跑在 `run` 进程**内**（守护线程里的 `ThreadingHTTPServer`） | 状态在内存里，跨进程就得加 IPC，违反「不要消息队列」 |
| 不引入前端框架、不加依赖 | 原生 JS + `setInterval` 够用 |
| 不做停止/取消按钮 | 语音「停」已经覆盖；需要时再加 |
| 默认只听 `127.0.0.1` | 不给局域网开口子；要上手机/电视就改 `host: 0.0.0.0` 并设 `token` |
| 不写测试 | 没有协议解析、没有路径边界；按规范手动跑一次即算验证 |
| 一次只跑一个任务，忙时返回 409 不排队 | 跟 `agent.run` 现有约束一致 |

## 2. 已完成（工作区未提交）

当前 `git status`：`agent.py` / `linx.py` / `tools.py` 已修改，`state.py` / `panel.py` 是新文件。
**今天的 `./scripts/check.sh` 是全绿的**（ruff + smoke 通过），可以在这上面继续。

| 文件 | 改动 | 状态 |
| --- | --- | --- |
| `dongdong/state.py`（新，72 行） | 进程内共享状态：`set_()`、`record_call()`、`snapshot(cfg)`、`json_short()`；调用记录 deque(maxlen=30) | 完成 |
| `dongdong/agent.py` | `Job.started`、`snapshot()`（id/agent/cwd/running/elapsed/exit_code/output 尾部 4000 字）；顺手修了四个引号的语法错误 | 完成 |
| `dongdong/linx.py` | 重连循环写 `connection` / `reconnects`；`hello` 写 `connected` / `session_id` / `connected_at`；`stt` / `tts` 写 `last_stt` / `last_tts`（局部变量改名 `tts_state` 避免遮蔽模块 `state`） | 完成 |
| `dongdong/tools.py` | `call()` 计时并 `state.record_call(...)`；顺手补了缺的 `import time` | 完成 |
| `dongdong/panel.py`（新，121 行） | `start(cfg)`、`make_handler(cfg)`；`GET /`、`GET /state`、`POST /task`；`X-Dongdong-Token` 鉴权；端口占用只告警 | 写完但**没跑过** |

`panel.py` 已有的行为（照着用，别重写）：

- `GET /` → `panel.html`（`Cache-Control: no-store`），**不鉴权**（页面本身没秘密）
- `GET /state` → `state.snapshot(cfg)`，token 非空时校验 `X-Dongdong-Token`
- `POST /task`：`{instruction, agent?, cwd?}` → 202 `{ok, agent, cwd}`；空指令 400；未知 agent 400；缺环境变量 400（`agent.required_env`）；目录越界 400；已有任务在跑 409；`agent.run` 放守护线程，`wait_seconds=1` 让 HTTP 立刻返回
- `start(cfg)` 里 `OSError` 只打 warning，**绝不能让面板拖垮语音**

## 3. 剩余任务（按顺序做）

### 3.1 写 `apps/device-mac/dongdong/panel.html`（唯一的大活）

单文件，原生 JS，无 CDN、无构建。要求：

- 深色紧凑布局，一屏放下，手机竖屏也不挤。
- **1 秒轮询** `GET /state`（`setInterval`），失败时在标题旁显示「连不上」，别弹窗。
- 四个区块：
  1. **连接**：`connection` 做徽章（connected 绿 / connecting 黄 / disconnected 红），带 `device_id`、`session_id`、`uptime`（格式化成 `hh:mm:ss`）、`reconnects`。
  2. **对话**：`last_stt`（你）、`last_tts`（咚咚），空时显示占位符。
  3. **当前任务**：`job` 为 null 时显示「还没跑过任务」；否则显示 `agent` / `id` / `cwd` / `elapsed` / `running` / `exit_code`，下面是 `<pre>` 实时输出（`job.output`），每次刷新自动滚到底。`running` 时标题带个呼吸点。
  4. **工具调用**：`calls` 表格，列 `at` / `name` / `args`（单行省略） / `ms` / `ok`（✓ / ✗） / `text`（截断，hover 显示全文）。
- 底部派发表单：指令 `<input type="text">`、agent `<select>`（用 `snapshot.agents` 填，`default_agent` 预选）、「派活」按钮。提交 `POST /task` 带 `X-Dongdong-Token`；202 清空输入并提示「已派给 xx」；400/409/401 把 `error` 原文显示出来（409 尤其常见）。
- token 来源：`new URLSearchParams(location.search).get('token') || ''`，只放内存，不进 URL 之外的任何地方、不写 localStorage。
- **所有动态文本用 `textContent`，不要拼 `innerHTML`**（agent 输出里会有 `<`、`&`）。
- 刷新时重建动态区块的内联 HTML 是允许的，但每次重建后要重新挂表单的 `submit` 监听（或者干脆把表单放静态区，循环里只更新动态容器——推荐后者，代码更少）。

### 3.2 配置默认值

`apps/device-mac/dongdong/config.py` 的 `DEFAULTS` 加一行：

```python
"panel": {"host": "127.0.0.1", "port": 8766, "token": ""},
```

放在 `"bridge"` 附近。`_merge` 会自动给老配置文件补上，不用迁移。

### 3.3 接进 `run`

`apps/device-mac/dongdong/__main__.py`：只在 `run` 路径启面板，`bind` 不需要。

- 顶部 import 改成 `from . import bridge, config, doctor, panel, screen, tools`
- `else` 分支（`asyncio.run(device.run())` 之前）加 `panel.start(cfg)`

### 3.4 验收（必须真的跑一遍）

```bash
# 1) 不连灵矽也能验面板：直接起服务，看空状态
.venv/bin/python -c 'from dongdong import config, panel; panel.start(config.ensure()); import time; time.sleep(600)'
curl -s localhost:8766/state | python3 -m json.tool
open http://127.0.0.1:8766

# 2) 派任务但不烧 LLM：临时在 ~/.dongdong/config.json 的 agents 里加一个假 agent
#    "fake": {"cmd": ["bash","-c","for i in 1 2 3 4 5; do echo tick $i; sleep 1; done"], "timeout_s": 5}
curl -s -X POST localhost:8766/task -H 'Content-Type: application/json' \
  -d '{"instruction":"刷屏","agent":"fake"}'
# 然后盯页面：输出应每秒多一行，elapsed 在涨，结束后 running=false、exit_code=0
# 再派一次（趁它还在跑）应得 409；验完把 fake 从配置删掉

# 3) 错误路径
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8766/task -d '{}'                    # 400
curl -s -X POST localhost:8766/task -d '{"instruction":"x","agent":"nope"}'                    # 400 未知 agent
curl -s -X POST localhost:8766/task -d '{"instruction":"x","cwd":"/etc"}'                      # 400 目录不在允许范围

# 4) 真机：./scripts/dev.sh run，连上后页面应显示 connected + 说一句话看 last_stt/last_tts + 语音派活看工具调用表

# 5) 收尾
./scripts/check.sh
```

### 3.5 提交

一个功能一个提交。前缀 `feat:`，例如 `feat: 本地前台面板，看状态、派任务`。要把 `state.py` / `panel.py` 两个新文件一起 `git add`（它们是为了这个功能加的），提交前 `git pull --rebase`，然后 `git push`。

顺手补文档（各一行/一小节，别扩写）：

- `docs/decisions.md`：加一行决定 ——「面板跟 `run` 同进程（守护线程 HTTP），不跨进程；默认只听 127.0.0.1」
- `docs/runbook.md`：在「4. 运行」里加一小节「看状态」，说明 `http://127.0.0.1:8766`、能派任务、要上手机就把 `~/.dongdong/config.json` 的 `panel.host` 改成 `0.0.0.0` 并设 `panel.token`（浏览器打开 `http://<Mac IP>:8766/?token=xxx`）

## 4. 别做的事

- 别加 `requirements`/`pyproject` 依赖（标准库 `http.server` 就够）
- 别加前端构建、别引 CDN
- 别把面板做成独立进程或独立 CLI 子命令（读不到 `state`）
- 别加停止按钮、日志落盘、历史任务列表、WebSocket 推送（轮询 1 秒够）
- 别为它写测试
- 单文件超 300 行就说明写多了，回来砍

## 5. 已踩过的坑（别复活）

- `agent.py` 的 `self.output` 曾经是 `""""`，四引号导致 `SyntaxError: invalid character '，'`
- `tools.py` 调 `time.monotonic()` 但没 `import time`，每次工具调用都会 `NameError`
- `linx.py` 里局部变量曾叫 `state`，遮蔽了模块 `state`；已改名 `tts_state`，改这块时注意
- `python -c 'import panel'` 报 `ImportError` 是因为 `panel.py` 当时还没建；现在应该没问题
