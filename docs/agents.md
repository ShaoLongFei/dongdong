# 接入 code agent（配置驱动）

> 本文只描述旧的 CLI 执行器（`agent.py` + `agents` 配置）。新的内置 agent 由
> `runtime.py` / `chat_cli.py` 实现，工具和配置都不走 `agents`，见
> [`architecture.md`](architecture.md) 的「内置 agent（新）」。

agent 是配置项，不是代码。加一个新 agent = 往 `~/.dongdong/config.json` 的 `agents` 里加一项，然后重启 `dongdong run`。

## 结构

```json
{
  "agent": "codex",
  "agents": {
    "名字": {
      "cmd": ["可执行文件", "参数", "{prompt}"],
      "env": { "变量": "值，支持 ${环境变量}" },
      "timeout_s": 120
    }
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `agent` | 默认用哪个 |
| `cmd` | 参数模板。`{prompt}` 换成年说的话，`{cwd}` 换成项目目录 |
| `env` | 只对这个进程加的额外环境变量；`${VAR}` 从当前环境展开 |
| `timeout_s` | 语音里没指定等多久时，默认等多少秒 |

规则：**命令自己退出**，输出从 stdout/stderr 收回来。不需要它是「Agent 协议」，任何会写代码的 CLI 都行。

## 自带的三套

```jsonc
// codex：非交互模式，workspace-write 沙箱（能改文件，不能联网装东西）
"codex": {
  "cmd": ["codex", "exec", "-C", "{cwd}", "-s", "workspace-write",
          "--skip-git-repo-check", "{prompt}"]
}

// claude：-p 即 headless，acceptEdits 允许改文件
"claude": { "cmd": ["claude", "-p", "{prompt}", "--permission-mode", "acceptEdits"] }

// deepseek：复用 claude CLI，只换端点和 key
"deepseek": {
  "cmd": ["claude", "-p", "{prompt}", "--permission-mode", "acceptEdits"],
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "${DEEPSEEK_API_KEY}",
    "ANTHROPIC_MODEL": "deepseek-chat"
  }
}
```

> DeepSeek 目前没有独立 CLI，走 Claude Code 的 Anthropic 兼容端点，交互方式和 claude 完全一样。
> 如果之后有了自己的 CLI，加一项就行，Mac 端代码不用动。

## 再加一个（示例）

```json
{
  "agents": {
    "ollama": {
      "cmd": ["ollama", "run", "qwen2.5-coder:7b", "{prompt}"],
      "timeout_s": 300
    },
    "my-script": {
      "cmd": ["/Users/me/bin/my-agent", "--dir", "{cwd}", "{prompt}"],
      "env": { "MY_TOKEN": "${MY_AGENT_TOKEN}" },
      "timeout_s": 60
    }
  }
}
```

改完直接试：

```bash
./scripts/dev.sh call self.code.run '{"instruction":"在当前目录建个 hello.txt","agent":"ollama"}'
```

## 语音里怎么选

- 「用 ollama 在 xxx 项目里……」→ 会带上 `agent: "ollama"`
- 不说名字 → 用 `agent` 字段的默认值

## 注意

- 缺环境变量时不会悄悄失败，会直接回一句「agent deepseek 缺少环境变量: DEEPSEEK_API_KEY」
- `cwd` 必须在 `projects` 列表里，越界直接拒绝
- 同一时间只跑一个 agent 任务，第二个请求会被挡回去（避免两个 agent 同时改一个仓库）
