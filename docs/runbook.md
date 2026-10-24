# 操作手册

## 0. 前置条件

- macOS 13+（当前验证：macOS 26 Tahoe / Apple Silicon）
- `brew install uv opus ffmpeg`（ffmpeg 只在调音频时用得上）
- 已安装 `codex` 和/或 `claude` CLI
- 灵矽账号：<https://xrobo.qiniu.com/>

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

连上后对着 Mac 说话即可。日志里 `你: ...` 是识别结果，`咚咚: ...` 是回复。

### 让它干活

直接说，例如：

- 「看看我有哪些项目可以改」
- 「在 dongdong 项目里把 README 的安装步骤补上」
- 「把屏幕投到卧室」
- 「停止投屏」
- 「刚才那个任务跑完没」

agent 任务默认最多等 120 秒，超时会转后台，用「刚才那个任务怎么样了」查。

### 看状态

运行时打开 <http://127.0.0.1:8766> 可查看连接、对话、任务输出和工具调用，也可以直接派任务。要从手机或电视访问，在 `~/.dongdong/config.json` 设置 `panel.host` 为 `0.0.0.0` 并设置 `panel.token`，重启 `run` 后打开 `http://<Mac IP>:8766/?token=xxx`。

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
所以后续把「投屏」换成 `ffmpeg` 抓屏 + `pyatv` 推 Apple TV 是可行的自动化路线，见 [decisions.md](decisions.md)。

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
| 连上就断、反复重连 | token 过期：删 `~/.dongdong/config.json` 重新 bind |
| 说的话没反应 | 终端没有麦克风权限；或 `echo_guard` 把它当成了回声 |
| 它自己跟自己聊 | 外放声音被麦克风收进去了：戴耳机，或把 `~/.dongdong/config.json` 里 `echo_guard` 保持 `true` |
| 投屏报 -1719 / 辅助访问 | 没开辅助功能权限，见上面 |
| 投屏失败但权限有 | 跑 `airplay debug`，把脚本里的选择器改成你系统里的结构 |
| agent 没反应 | `which codex` / `which claude`；或 `./scripts/dev.sh call self.code.run '{"instruction":"说 ok"}'` 单独试 |
| `目录不在允许范围` | 要改的项目不在 `~/.dongdong/config.json` 的 `projects` 里 |

看完整配置：`cat ~/.dongdong/config.json`。

## 10. ESP32

见 [`firmware/README.md`](../firmware/README.md)。
