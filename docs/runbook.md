# 操作手册

## 0. 前置条件

- macOS 13+（当前验证：macOS 26 Tahoe / Apple Silicon）
- `brew install uv opus ffmpeg`（ffmpeg 只在调音频时用得上）
- 内置 agent 需要可用的模型 API key；使用 CLI 后端才需安装 `codex` 和/或 `claude`
- 语音功能需要灵矽账号：<https://xrobo.qiniu.com/>；仅 Web 文字任务不需要绑定

### 两个系统权限

| 权限 | 用途 | 在哪开 |
| --- | --- | --- |
| 麦克风 | 语音输入 | 系统设置 → 隐私与安全性 → 麦克风 |
| 辅助功能 | 自动点「屏幕镜像」 | 系统设置 → 隐私与安全性 → 辅助功能 |

辅助功能要勾**运行咚咚的那个程序**：终端（Terminal / iTerm / Ghostty）或 DeepSeek Harness。
改了权限要重启那个程序才生效。

---

## 1. 安装

```bash
git clone git@github.com:ShaoLongFei/dongdong.git
cd dongdong
./scripts/setup.sh
```

## 2. 体检

```bash
./scripts/dev.sh doctor
```

看到「可以开工」再往下。`⚠️` 一般是没装某个 agent 或没开辅助功能，不影响语音对话，影响对应功能。

## 3. 绑定设备（只需一次）

```bash
./scripts/dev.sh bind
```

终端会打印：

```
绑定这台 Mac（只需一次）
  Device-Id : AA:BB:CC:DD:EE:FF
  激活码    : 123456
  控制台    : https://xrobo.qiniu.com/
```

去智控台把激活码填进去（或在支持的小程序里绑定）。程序每 3 秒查一次，绑定完会自己拿到 token 并退出。

> 想换一台「新设备」重来：删掉 `~/.dongdong/config.json` 再 `bind`。

## 4. 运行

```bash
./scripts/dev.sh run
```

打开 <http://127.0.0.1:8766>，点击“开启语音”，连上后对着 Mac 说话。日志里 `你: ...` 是识别结果，`咚咚: ...` 是回复。前端尚未构建时，先执行下面“独立 Web 工作台”的构建步骤。

### 让它干活

直接说，例如：

- 「看看我有哪些项目可以改」
- 「在 dongdong 项目里把 README 的安装步骤补上」
- 「把屏幕投到卧室」
- 「停止投屏」
- 「刚才那个任务跑完没」

内置 agent 的语音工具最多同步等待 8 秒，未结束则返回任务号，用「刚才那个任务怎么样了」查询，或在工作台查看进度。CLI 后端默认同步等待 120 秒，超时后在当前服务进程内继续执行。

### 看状态

`./scripts/dev.sh run` 启动工作台，打开 <http://127.0.0.1:8766>。语音默认关闭，点击顶部“开启语音”才连接灵矽并打开麦克风；“关闭语音”停止采音、当前播报并断开连接，已派发的编程任务继续运行。刷新页面保留当前开关状态，重启服务回到关闭。顶部显示语音连接、最近识别和回复；builtin 语音任务在会话列表中可回看，进入该会话后能看流式输出和停止任务。语音启动失败时 Web 继续可用。当前 Web 仅支持本机访问，旧 `panel.host/token` 配置不再用于 `run`。

语音转写、语音回复和任务执行统一保存在当前对话，界面用来源标签区分；重连和麦克风开关不会切分会话，只存文字、不存音频。新建对话同步切换语音归属，随时选中未归档对话即可继续，语音随当前选择归属；后台会议不会占用其他对话。重启后开启语音使用当前选中的对话，没有选中才新建。历史独立“语音对话”原样保留，不自动合并。口播和语音原文仅展示，代码 agent 仍使用显式任务指令，会议实施仍需确认。听不到回复时先确认 Mac 输出未静音；`osascript -e 'get volume settings'` 可检查 `output muted`。

### 独立 Web 工作台

不绑定灵矽也能使用内置 agent。首次在仓库根目录执行：

```bash
cd apps/web
npm ci
npm run build
cd ../..
./scripts/dev.sh web
```

打开 <http://127.0.0.1:8766>：新建会话、发送消息、实时查看回答和工具调用、停止任务；“运行记录”回看当前会话，“设置”配置模型和工作区。密钥留空保留现有值，保存权限为 0600，API 不返回密钥。

会话和运行存于 `~/.dongdong/dongdong.db`；未配置模型时可以先进入设置。前端开发运行 `cd apps/web && npm run dev`，Vite 将 `/api` 代理到 Python。生产只需 Python，无需 Node 常驻。

`web` 和 `run` 都通过界面按钮启停语音，启动时均不采音，二选一启动。设置中将“语音编程后端”选为“内置 agent”，语音与 Web 就共享 Runtime、模型、工作区、会话和停止入口；CLI 选项保留原来的 codex/claude 路径。模型、工作区、后端设置保存后从下一任务起生效。忙时拒绝改设置和重复派活。

**独立进程之间不共享互斥**，不要另开 `chat/call/serve` 对同一工作区同时派活。语音设备绑定仍用 `./scripts/dev.sh bind`，本轮未增加灵矽自定义模型 `/v1` 端点。

### 复杂任务的执行限制

内置 agent 每次任务最多 **1000 轮模型调用**，每轮可以执行多个工具；会议生成和实施各自计数，
普通调度任务按每次执行尝试计数。修改 `~/.dongdong/config.json` 中
`runtime.max_steps` 后重启 `run/web`；已有显式配置会覆盖程序默认值。
达到上限时停止并保存执行记录，不自动接着断点执行，也不会回滚已完成的文件修改。

| 限制 | 当前值与影响 | 调整方式 |
| --- | --- | --- |
| 每轮模型输出 | `model.max_tokens` 默认 4096，包含工具参数；大型方案 JSON 可能截断 | 设置中调整，界面允许 1–65536，实际还受所选模型限制 |
| 模型上下文 | `history_messages=30` 是软窗口；保留本轮用户目标以来的全部记录，没有自动摘要或 token 预算 | 提高轮数不会提高模型上下文容量；长任务仍可能收到服务端上下文超限错误 |
| 模型网络等待 | 连接 15 秒，网络读写等操作超时 180 秒；不是整个任务只能运行 180 秒 | 当前写在 `llm.py`，流式持续收到数据时可运行更久 |
| 单条 shell 命令 | 默认 30 秒，模型可传 `timeout_s`，硬上限 300 秒；超时终止进程组 | 长构建/测试可显式用 300 秒，超出需要调整工具实现 |
| 外部 MCP | 连接及工具发现 15 秒，单次调用 180 秒 | 当前由 `MCPTools` 默认参数控制；会议阶段不连接外部 MCP |
| 文件与工具结果 | 普通工具返回、shell 输出及工具结果存储不设字符数上限；读取/编辑文件仍不超过 200000 字节 | 完整结果传给模型，仍受模型上下文容量限制；目前 `read_file` 没有分页参数，会议生成阶段没有 shell |
| 文件查找 | list_dir/glob 最多 200 项，grep 最多 100 条、保留完整匹配行，跳过超过 1MB 文件 | 缩小目录或匹配范围；目前条数截断没有统一提示 |
| Birdview 项目规模 | 基线扫描最多 20000 个 Git 可见文件、200MB 常规文件；Git/Node 命令通常 30 秒，地图交付 60 秒 | 超限会停止；当前要求 Git 仓库根目录，不能用任意子目录绕过 |
| 单版方案 | 最多 100 个范围项、200 个文件、30 条验证项；solution 最多 12000 字符 | 大范围改动按可确认的方案拆分；Schema 限制在 `meeting_tools.py` |
| 调度与并发 | Web 宿主共享 4 个 worker，不同对话可并行，超出后等待线程；同一对话不可重入；普通任务每个 Item 默认最多尝试 2 次，会议不自动重试 | 8 秒语音等待只影响同步回复，不停止后台任务；正常提交没有默认总时长截止 |

1000 轮是允许执行的上限，模型正常完成即可提前结束。工作台仍可停止任务。

### 语音调用本地内置 agent

默认 `code_backend=cli`，语音还是走 codex/claude。要用新的内置 agent：

```json
{ "code_backend": "builtin", "workspace": "~/OtherProjects" }
```

重启 `run`。工具名仍是 `self.code.run`，平台提示词和配置见
[`linx-voice-prompt.md`](linx-voice-prompt.md)。这条路径不需要公网穿透，也不加入站端口。

### 技能、外部 MCP 与操作确认

- **技能**：把已有 `SKILL.md` 放到 `~/.dongdong/skills/<名称>/`，在“技能”页刷新、查看和启停。模型先看描述，再按需读取正文；名称只用字母、数字、下划线或连字符，正文最多32KB。没有自动安装功能。
- **MCP**：在“MCP”页手动保存 stdio、Streamable HTTP 或 SSE 服务，点击测试查看工具；启用项在下一任务开始时连接。env/headers 使用 JSON 字符串键值对象，支持 `${ENV}`；编辑时留空保留原值，填 `{}` 清空。列表只显示这些字段的 key；不要把密钥放入可见的命令参数或 URL。
- **审批**：设置 → 工具执行方式 → “逐次确认”。shell、写/改文件和外部工具调用会在所属对话出现“允许一次 / 拒绝”；切回该对话即可处理，停止任务撤销等待。读取文件、技能和更新计划无需确认。默认自动执行；审批不限制 shell 的系统权限，也不审批 MCP 服务启动本身。

以上能力用于内置 Runtime；Web 文字始终走内置 Runtime，语音的 `code_backend` 选择不改变文字入口。CLI 后端使用各 CLI 自身权限机制。独立 `chat/call/serve` 没有 Web 审批回调，ask 模式会拒绝需确认操作；长任务应使用持续运行的 `run/web`，不要依赖一次性 `call` 退出后的 builtin 后台线程。

数据路径与两套工具的区别见 [项目架构](architecture.md)，接口定位见 [文档导航](README.md)。

## 5. 投屏

```bash
./scripts/dev.sh airplay list              # 局域网里的目标
./scripts/dev.sh airplay mirror 卧室       # 直接投
./scripts/dev.sh airplay stop              # 停
./scripts/dev.sh airplay debug             # 点不动时看 UI 结构
```

**当前状态：自动投屏在 macOS 27 上走不通。** 系统把菜单栏项目交给了 `MenuBarAgent`，
控制中心里没有能被 AppleScript 点到的「屏幕镜像」项（`airplay debug` 可复现）。
在换方案之前，用下面任意一种：

- 手动点一次：控制中心 → 屏幕镜像 → 选电视
- 自己录一个能用的脚本（Automator / 快捷指令 / 一段 AppleScript），配到 `~/.dongdong/config.json`（推荐，语音能触发它）：

```json
{ "airplay": { "mirror_command": "osascript /path/to/your.scpt {target}" } }
```

`{target}` 会被替换成目标名，`self.screen.mirror` 语音调用会直接走它。

发现设备这件事是好的：`airplay list` 能扫到 Apple TV（实测「卧室」AppleTV14,1）。
后续 `ffmpeg` 抓屏 + `pyatv` 推 Apple TV 是尚未验证的候选路线，当前未实现，见 [decisions.md](decisions.md)。

## 6. 换 / 加 agent

`codex`、`claude`、`deepseek` 都是配置项，加新的只改 `~/.dongdong/config.json`，见 [agents.md](agents.md)。

DeepSeek 需要 key（走 Claude Code 的兼容端点）：

```bash
export DEEPSEEK_API_KEY=sk-xxxx
./scripts/dev.sh call self.code.run '{"instruction":"讲个笑话","agent":"deepseek"}'
```

## 7. 长期记忆

Mem0 在 Mac 设备进程内运行，Qdrant 向量和 SQLite 历史保存在仓库根目录 `.memory/`，不会提交到 Git。Mem0 使用 OpenAI 做记忆提取和向量嵌入：保存时提交的文字以及搜索时的查询文字会发送给 OpenAI；不要把密码、令牌或其他凭据交给记忆工具。程序只从 `OPENAI_API_KEY` 环境变量读取 OpenAI key，不写入配置文件或日志。

本地开发时，在启动进程的 shell 中设置 key：

```bash
export OPENAI_API_KEY=...
./scripts/dev.sh run
```

使用 launchd 时，把 key 填入 `~/Library/LaunchAgents/com.dongdong.device.plist` 的 `OPENAI_API_KEY` 项后重新加载服务。样例文件中的 key 留空。

在灵矽智能体指令中，要求它在回答前用 `self.memory.search` 查找与当前问题相关的既有信息，并在用户明确要求记住内容、或出现稳定偏好和长期项目事实时调用 `self.memory.save`。不要保存临时请求、密码、令牌或凭据。模型是否调用工具由灵矽智能体模型决定，咚咚只提供工具，不能保证模型每次都按指令调用。

`self.memory.list` 可检查记忆及其 ID；删除时用 `self.memory.delete` 并只提供要删除的 ID。

## 8. 开机自启（可选）

```bash
cp deploy/com.dongdong.device.plist ~/Library/LaunchAgents/
# 把 plist 里的路径和 DEEPSEEK_API_KEY 改成自己的
launchctl load ~/Library/LaunchAgents/com.dongdong.device.plist
```

日志：`~/Library/Logs/dongdong.log`

## 9. 排错

| 现象 | 原因 / 处理 |
| --- | --- |
| `拿不到 token` | 设备没绑定，或智控台里没给设备配智能体 |
| 一轮语音结束后断链并重连 | 平台可能正常结束连接，自动重连成功即可；持续握手/鉴权失败再检查绑定与 token，不要仅因重连删除配置 |
| 说的话没反应 | 终端没有麦克风权限；或 `echo_guard` 把它当成了回声 |
| 它自己跟自己聊 | 外放声音被麦克风收进去了：戴耳机，或把 `~/.dongdong/config.json` 里 `echo_guard` 保持 `true` |
| 投屏报 -1719 / 辅助访问 | 没开辅助功能权限，见上面 |
| 投屏失败但权限有 | 跑 `airplay debug`，把脚本里的选择器改成你系统里的结构 |
| agent 没反应 | `which codex` / `which claude`；或 `./scripts/dev.sh call self.code.run '{"instruction":"说 ok"}'` 单独试 |
| `目录不在允许范围` | 要改的项目不在 `~/.dongdong/config.json` 的 `projects` 里 |

看完整配置：`cat ~/.dongdong/config.json`。

## 10. ESP32

见 [`firmware/README.md`](../firmware/README.md)。


## 会议代码修改模式

1. 首次安装渲染依赖：`./scripts/setup-birdview.sh`（Node.js 18+；固定上游版本，约 8MB 已安装依赖，不需要完整图标包）。前端仍用 `npm ci --prefix apps/web && npm run build --prefix apps/web`。
2. 开启新对话，在“代码修改模式”填写工作区内已有 Git 项目目录，点击进入。修改模式绑定固定项目；另一个项目使用新会话。
3. 多人语音讨论需手动开启语音；灵矽实际转写到的用户语音进入本次会议，也可在输入框补充。普通讨论不会自动画图、调用写代码任务。
4. 说“咚咚，整理一下方案”或点击“整理方案”。Agent 只读源码并生成方案、架构/改动地图和已审查约束；规则审查不足会披露覆盖缺口。生成期间的新意见保留，需再次整理才纳入下一版。
5. 等地图加载，审阅当前方案。点击“确认此方案并实施”或说“按这个方案实施”，直接启动当前版本；有新意见/待决问题、未展示、工作区变化时拒绝执行。旧方案只读回看。
6. 实施中可继续补充讨论、查看工具记录或停止；新意见不直接扩大本次范围。工具写文件会检查已确认文件集合；shell 每次结束后核对 Git 可见文件变化，发现范围外变化就停止并报告；执行期间仍是原有本机权限，不是路径沙箱，也不覆盖被 Git 忽略文件或项目外路径。`tool_policy=ask` 仍逐次审批工具。
7. 收尾查看实际检查、diff 和地图回放；检查退出码 0 不等于所有验收都通过。差异对比实施前后文件内容，包含已批准的新增文件和已暂存内容；仍需结合工具记录核对。重启不会自动续跑；无变化且已确认但中断的方案可在重新展示后显式恢复，否则重新整理。

首版使用内置 Runtime；代码修改模式中原 CLI/MCP run 不直接执行代码。退出模式后恢复通常工作流。该模式仅本地单用户会议，不识别主持人、说话人或投票。未通过真实麦克风/浏览器验收时，命令行测试不能证明会议转写覆盖或视觉体验。

## 项目与归档（已整合main）

侧栏创建项目绑定已有真实目录；目录固定，名称可改。旧会话留在未分类。文字任务按会话目录执行，语音使用明确选择的项目，各项目分别续聊；重启后重新选择。详情支持移动、归档和恢复，移动不搬文件/记忆，归档保留全部历史且只读。每个对话独立保存模式、目录和方案，新建与切换不受其他对话影响；目标对话在会议模式开启时先退出再移动/归档，防止会议目录与项目范围失配。

记忆页按全局/项目管理。设置服务进程的OPENAI_API_KEY后，Runtime每轮自动检索全局+本项目，用户明确要求记住才调用memory_save；ask模式需批准。设备搜索同样合并两层，保存/列表/删除默认当前语音项目，scope=global明确选择全局。旧记忆保留全局，归档不删记忆。

任务调度在重启后暂停遗留任务，确认当前文件状态后可通过self.task.resume恢复；不会自动重放未确认的写命令。
