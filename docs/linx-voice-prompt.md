# 灵矽语音调用本地 agent

目标：灵矽仍用云端大模型做理解、对话和 TTS；需要操作 Mac 时，云端模型调用本地 MCP 的 `self.code.run`，由 Mac 上的 agent 执行。**不需要公网穿透。**

## 1. 本地配置

编辑 `~/.dongdong/config.json`：

```json
{
  "code_backend": "builtin",
  "workspace": "~/OtherProjects",
  "model": {
    "base_url": "https://api.deepseek.com",
    "api_key": "${DEEPSEEK_API_KEY}",
    "model": "deepseek-flash",
    "temperature": 0.2,
    "max_tokens": 4096,
    "extra_body": {"thinking": {"type": "disabled"}}
  }
}
```

- `code_backend`：`cli` 是原来的 codex/claude 子进程；`builtin` 是本地 `Runtime`（本文件用这个）。
- 工作区决定内置工具能读写哪里；默认 `~/OtherProjects`。
- `model.api_key` 支持 `${DEEPSEEK_API_KEY}`；key 只在调用时展开，不会写回配置。
- 改完重启 `./scripts/dev.sh run`。

平台侧前提：智能体启用设备 MCP；云端模型支持 tool calling（function calling）。这两项不满足，提示词写得再好也不会调用工具。

## 2. 系统提示词

把下面整段粘到灵矽智能体的“角色/系统提示词”里。可按自己的角色设定调整语气，不要删掉工具规则。

```text
你是灵矽语音助手。你自己不能直接读写 Mac 文件、执行命令或修改项目。

当用户要求做以下事情时，必须调用 self.code.run，不要自己回答“已完成”：
- 读写、修改、搜索 Mac 上的文件
- 运行命令、测试、构建、git 操作
- 需要多步操作的项目任务
- 用户说“让 agent 做”“帮我改一下”“跑一下”

调用时，把用户的话改写成具体、可执行的 instruction，包含：
1. 目标是什么
2. 在哪个目录/项目
3. 验收标准
4. 不要做什么（可选）

一次只交一个任务。wait_seconds 用 8，不要长时间干等。
如果工具返回“还在跑”，告诉用户任务已开始，并说可以稍后问“好了吗”，不要编造结果。
用户再问进度或结果时，调用 self.code.result。
用户说“停”时，调用 self.code.stop。

只向用户播报结论，不要朗读 JSON、日志、工具名或大段代码。
普通聊天、天气、音乐、知识问答不要调用 self.code.run，用平台自带能力回答。
```

## 3. 推荐加的例子

Few-shot 对工具选择帮助很大，放在提示词最后：

```text
用户：帮我把 README 的安装步骤补一下
→ 调用 self.code.run({
    "instruction": "在 dongdong 项目里更新 README 的安装步骤，保持现有风格，不要改其他章节",
    "cwd": "~/OtherProjects/dongdong",
    "wait_seconds": 8
  })

用户：帮我看看这个项目为什么测试挂了
→ 调用 self.code.run({
    "instruction": "在 dongdong 项目里运行测试，定位失败原因并修复；修完再跑一遍测试",
    "cwd": "~/OtherProjects/dongdong",
    "wait_seconds": 8
  })

用户：好了吗
→ 调用 self.code.result({})
```

## 4. 时间和长任务

- 工具最多等 8 秒；`builtin` 后端不传时默认 8 秒，传更大值也会被压到 8。
- 超时后返回任务 ID，任务继续在 Mac 上跑；用户再问时用 `self.code.result` 查。
- 灵矽的模型调用是同步的，平台不会主动推送任务完成；语音只能“用户问 → 查结果”。要主动通知，后续做 Web 面板或设备侧消息。
- 每次 `self.code.run` 在本地新建一个会话，不复用上一个任务的上下文；需要延续时，在 `instruction` 里带上项目、文件或上一轮结果。
- 目前没有“执行到一半暂停等用户确认”的机制。本地 agent 可以把问题当最终回答返回，但这不是强保证；危险操作应在调用前由云端模型问清楚。

## 5. 安全边界

提示词不是安全边界。`builtin` 后端的 shell 能执行任意命令，`workspace` 只限制文件工具，shell 仍可访问工作区外；当前也**没有运行时二次确认**。语音入口建议：

- 只给 `workspace` 下的目录；
- 删除、覆盖、`git push`、安装软件等不可逆操作不要用语音做，走终端或 Web 手动确认；
- 保留 `self.code.stop`，长任务要能停；
- 不要把 `run` 的 MCP 端口/隧道暴露到公网。本方案本来就不需要入站端口。

如果只是自己用，先接受“能跑命令”的风险；要给别人用，先加审批和沙箱。

## 6. 验证清单

1. `./scripts/check.sh` 通过。
2. `./scripts/dev.sh chat --message "写个 hello.txt"` 本地 Runtime 能用。
3. 绑灵矽，确认设备在线、智能体启用 MCP。
4. 说“帮我在 dongdong 项目里看看 git 状态”，看平台日志是否出现 `MCP ← tools/call`，工具名是否为 `self.code.run`。
5. 如果云端不调用工具：先检查 MCP 工具列表、模型 tool calling、提示词；再在工具描述里补“什么时候必须用”。
