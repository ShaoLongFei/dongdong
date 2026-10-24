# 咚咚长期记忆设计

## 目标

让 Mac 设备端通过 Mem0 Python SDK 保存和检索跨会话的长期记忆，并将能力作为 MCP 工具提供给灵矽智能体。记忆在设备本地持久化；Mem0 使用 OpenAI API 做记忆提取和向量嵌入。

## 方案

在现有 Python 设备端进程内初始化 Mem0，不另起 Docker 服务或 OpenMemory MCP server。现有 `tools.py` 是灵矽 MCP 与本地 HTTP bridge 共用的注册表，因此记忆能力沿用它。

```text
灵矽模型
  ├─ self.memory.search ─┐
  ├─ self.memory.save   ├─ MCP over WebSocket ─→ dongdong tools.py
  ├─ self.memory.list   │                           └─ Mem0 Python SDK
  └─ self.memory.delete ┘                               ├─ OpenAI API: 提取/嵌入
                                                        └─ 项目本地存储
```

## 数据与模型

- Qdrant 向量库：仓库根目录 `.memory/qdrant/`。
- Mem0 SQLite 历史库：仓库根目录 `.memory/history.db`。
- `.memory/` 加入 `.gitignore`，运行数据不进入提交。
- OpenAI 凭据从 `OPENAI_API_KEY` 环境变量读取，不写入项目配置或日志。
- 以稳定的 `device_id` 作为 Mem0 `user_id`，隔天重连后仍检索同一用户记忆。
- 不保存原始音频或完整对话。只有模型调用保存工具时，提交的文字才进入 Mem0；该文字会发给 OpenAI 做提取/嵌入。

## MCP 工具

| 工具 | 参数 | 行为 |
| --- | --- | --- |
| `self.memory.search` | `query` | 返回少量最相关的记忆 |
| `self.memory.save` | `text` | 交给 Mem0 提取、去重并保存 |
| `self.memory.list` | 无 | 列出已有记忆及其 ID，供用户检查 |
| `self.memory.delete` | `memory_ids` | 只删除指定 ID，不提供清空全部记忆的工具 |

工具描述引导模型保存明确要求记住的信息、稳定偏好和长期项目事实；跳过一次性任务内容、密码、token 和其他凭据。回答涉及用户偏好或过去对话时，灵矽智能体指令要求先搜索；发现值得长期保留的新信息时再保存。工具和提示只能引导模型，不能保证平台每次都调用。

## 错误处理

沿用 `tools.py` 的约定：操作失败转成 MCP 文本错误并记录日志，不让 Mem0 异常中断音频会话。日志不得包含 OpenAI API key 或完整记忆正文。

## 范围外

- 自动归档每轮完整对话、原始录音或转写全文。
- 多设备共享记忆、云端 Mem0 Platform、独立 OpenMemory 服务。
- 本地 LLM 或 embedding 模型。
- 在 Mac 侧强制每轮回答前检索；检索策略由灵矽智能体按工具描述执行。

## 验证

- `./scripts/check.sh` 验证格式、工具注册和本地 smoke 检查。
- 配置 `OPENAI_API_KEY` 后手动保存一条测试记忆、重启咚咚、搜索并删除该 ID，确认跨重启持久化和定向删除。
- 检查 `.memory/` 被 Git 忽略，且 key 与记忆正文没有进入日志或仓库变更。

## 主要限制

本地存储只保证向量与历史数据库落在 Mac；保存和检索时的文本仍会发送给 OpenAI。灵矽本身也在云端处理语音和对话。若 Mem0 的依赖安装体积超过仓库 20 MB 门槛，安装前需先说明并取得同意。
