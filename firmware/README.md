# ESP32（第二阶段）

现在还没开始写，先把路径钉死，避免后面返工。

## 结论

**不自己写固件**，直接用开源 [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)。
我们要做的只有一件事：在固件的 MCP 工具表里加 7 个转发工具，把调用转给 Mac。

## 为什么可行

灵矽平台的协议和小智固件是同一套（同样的 hello / listen / tts / mcp 消息）。
小智固件已经处理了：麦克风、扬声器、Opus、Wi-Fi 配网、激活、OTA、断线重连。
这些在 ESP32 上重写一遍至少两周，没必要。

## 要做的事

1. 板子到了以后，按 xiaozhi-esp32 的 board 配置烧录，先跑通「你好小智」
2. 在 `main/mcp_server.cc` 里加工具，每个工具内部就是一个 HTTP POST：

   ```
   POST http://<Mac 局域网 IP>:8765/call
   X-Dongdong-Token: <配置里的 token>
   {"name": "self.code.run", "arguments": {"instruction": "..."}}
   ```

3. Mac 上跑 `./scripts/dev.sh serve --host 0.0.0.0`，把 `bridge.token` 配上
4. 工具名、参数和 Mac 端完全一致，平台的智能体不用改

## 移植时唯一要改的

- `self.screen.mirror` 在 ESP32 上没意义（屏幕在 Mac 上），但保留转发即可
- ESP32 到 Mac 的 IP 建议写进配置项，别硬编码

## 待定

- 具体板型（等买了再填）
- ESP32 和 Mac 不在同一 Wi-Fi 时的兜底方案（先用手机热点，别搞内网穿透）
