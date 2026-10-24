# 灵矽协议摘要

只记我们实际用到的字段，原文以官方文档为准：
<https://linx.qiniu.com/docs/xrobot/platform/websocket>

## 地址

| 用途 | 地址 |
| --- | --- |
| OTA + 激活 | `https://xrobo.qiniuapi.com/v1/ota/` |
| 激活状态轮询 | `https://xrobo.qiniuapi.com/v1/ota/activate` |
| WebSocket | `wss://xrobo-io.qiniuapi.com/v1/ws/`（优先用 OTA 返回的 url） |

## 握手头

```
Authorization: Bearer <token>      # 只有已绑定的设备才有
Device-Id: AA:BB:CC:DD:EE:FF       # 我们生成的伪 MAC，稳定不变
Client-Id: <uuid4>                 # 软件生成
Protocol-Version: 1
```

## 绑定流程（必走）

1. `POST /v1/ota/` 带 `Activation-Version: 1`、`Device-Id`、`Client-Id`、`User-Agent`
2. 未绑定 → 返回 `activation.code`（6 位数字）
3. 用户去智控台输入激活码绑定
4. 轮询 `POST /v1/ota/activate`（只带 `Device-Id`）：`200 success` = 已绑定，`202` = 还没
5. 再 `POST /v1/ota/` → 这次返回 `websocket.url` + `websocket.token`

## 消息

设备 → 服务器：

```json
{"type":"hello","version":1,"features":{"mcp":true},"transport":"websocket",
 "audio_params":{"format":"opus","sample_rate":16000,"channels":1,
                 "frame_duration":60,"play_buffer_duration":1000}}
{"session_id":"...","type":"listen","state":"start","mode":"realtime"}
```

服务器 → 设备：

| type | 用途 |
| --- | --- |
| `hello` | 握手确认，`audio_params` 是 TTS 的音频参数 |
| `stt` | ASR 结果，`text` |
| `tts` | `state`: `start` / `sentence_start` / `stop`，`is_aborted` 表示被打断 |
| `mcp` | JSON-RPC 2.0，包在 `payload` 里 |
| `llm` | 表情，`emotion` / `action`，暂时忽略 |

音频是二进制帧，TTS 为 opus，采样率看服务器 hello 的 `audio_params.sample_rate`。

## MCP

```
{"type":"mcp","payload":{"jsonrpc":"2.0","method":"initialize","id":1,...}}
{"type":"mcp","payload":{"jsonrpc":"2.0","method":"tools/list","id":2}}
{"type":"mcp","payload":{"jsonrpc":"2.0","method":"tools/call","params":{"name":"...","arguments":{}},"id":3}}
```

返回 `result.content[{type:"text",text:"..."}]` + `isError`。
工具名用 `self.xxx.yyy` 风格，和官方示例一致。

## 已知的坑

- 平台 HTTP API 认证失败时 **HTTP 状态码仍是 200**，要看 `body.code`；OTA 接口不受影响。
- 智能体改完配置要**重启设备**（重新连 WS）才生效。
- `play_buffer_duration` 越大服务器发得越积极；1000ms 够用。
- 服务器 TTS 采样率可能是 16000 或 24000，必须按 hello 返回值建解码器，不能写死。
