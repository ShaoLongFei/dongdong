# 讲故事模式设计

## 目标

在咚咚现有 Web 控制台中增加“讲故事模式”。用户可以点击按钮，或通过灵矽语音说“开始讲故事”进入模式；之后的语音识别文本按自适应规则切成故事片段，模型为每个片段生成受控的场景 JSON，浏览器立即把场景绘制成暖米黄纸张风格的手绘 SVG。每幕 SVG 会持久化保存，故事结束后可以导出 PNG、PDF 或 MP4。

第一版优先低延迟和稳定可播放，直接在浏览器中播放 SVG 动画，不等待 PNG、图片生成或 MP4。目标仓库 `srt-whiteboard-animation` 的字幕驱动、叙事顺序、区域揭示和暖米黄白板视觉规范会内化为场景协议；它的离线 SRT/PNG/MP4 脚本不直接作为实时依赖。

## 用户流程

1. 用户在 Web 控制台点击“开始讲故事”，或说“开始讲故事”。按钮和语音指令都只切换模式，不生成场景。
2. 画布显示故事状态和当前故事标题。灵矽 STT 的最终文本进入故事会话，不会触发普通编程 agent。
3. 故事引擎按停顿、字数和事件词切分片段。短句先缓冲，最长等待 2 秒；单幕超过约 40 个字强制生成。
4. 每个片段立即请求文本模型生成 Scene JSON。请求进行时画布显示该片段字幕，上一幕继续播放。
5. Python 校验并规范化 JSON，限制元素数量、坐标、颜色和动作类型。模型失败或返回非法 JSON 时生成简单默认场景，保证字幕和故事队列继续前进。
6. 浏览器收到场景后按元素的 `startMs`/`durationMs` 顺序逐笔绘制；场景 SVG 同时写入故事目录。
7. 用户点击“结束故事”，或说“结束讲故事/停止讲故事”。停止接收新片段，等待已提交的场景请求完成，故事进入可导出状态。
8. 用户可以重新播放单幕或整个故事，发起 PNG、PDF、MP4 导出。导出在独立任务中执行，不阻塞新的普通对话。

## 架构与边界

```text
灵矽 STT
  → WebState.story_text
  → StorySession（缓冲/切幕/角色状态）
  → ModelConfig（Scene JSON 请求）
  → story_schema（校验/降级）
  → story_svg（固定手绘组件）
  → Story SSE
  → StoryCanvas（SVG 播放队列）
                         └→ ~/.dongdong/stories/<story_id>/scene-*.svg
结束故事 → ExportJob（PNG/PDF/MP4）
```

新增的 Python 模块各自只有一个职责：

- `story.py`：故事会话、片段缓冲、异步场景生成、队列和生命周期。
- `story_schema.py`：Scene JSON 的结构校验、默认值、边界限制和失败降级。
- `story_svg.py`：把受控元素转成 SVG；固定组件负责人物、动物、树、房屋、太阳、道具、表情和动作线，模型不能直接注入 SVG。
- `story_export.py`：把保存的 SVG 转换为 PNG/PDF，并使用项目内 FFmpeg 生成 MP4。

故事引擎不加入设备 MCP 工具表，也不让普通 Runtime 获得故事工具。语音回调在故事模式下只写入故事会话；普通语音会话和编程任务的会话、互斥和工具调用保持现状。

## Scene JSON

模型输出只允许 JSON，Python 负责最终可信边界。每幕画布固定为 1280×720，暖米黄背景为 `#F5EBD7`。元素数量限制为 3–8 个；坐标必须在画布内；颜色只允许深灰、红、橙、蓝和米黄；类型只能来自固定组件集合。

```json
{
  "sceneId": "scene-001",
  "storyText": "小兔子在森林里发现一颗发光的种子。",
  "storyBasis": "森林场景中，小兔子发现发光种子。",
  "durationMs": 6500,
  "characters": [{"id": "rabbit", "kind": "rabbit", "color": "#5B5550"}],
  "elements": [
    {
      "id": "forest",
      "kind": "tree",
      "label": "森林场景",
      "narrativeRole": "场景铺垫",
      "x": 80,
      "y": 90,
      "width": 420,
      "height": 540,
      "color": "#5B5550",
      "action": "draw",
      "startMs": 0,
      "durationMs": 1700
    }
  ]
}
```

`story_schema.py` 会补齐缺失的 `durationMs`、`startMs` 和颜色，按叙事顺序重排元素，并把不合法或过度复杂的元素替换为 `simple_shape` 默认元素。模型提示词要求“场景铺垫 → 主体 → 动作 → 结果”，同时禁止文字、字母、数字和复杂背景。

## 实时切幕与播放

`StorySession` 只接收 STT 最终文本。少于约 8 个字的片段先缓冲；累计达到 8–20 个字、检测到“然后/突然/于是/看见/跑向/拿起/变成”等事件词、停顿超过 1.2 秒或缓冲达到 2 秒时生成一幕；单幕达到 40 个字强制切幕。切幕后立即清空缓冲并开始下一幕，不等待上一幕渲染完成。

场景生成请求在专用异步任务中运行。模型返回后先持久化 `scene-###.json` 和 `scene-###.svg`，再发出 SSE 事件；持久化失败时不把场景加入播放队列，并向画布发出错误状态。默认场景不依赖模型，可在模型超时、JSON 解析失败或元素校验失败时立即生成。

浏览器维护一个 FIFO 场景队列。每幕从干净的米黄画布开始，按元素时序使用 SVG `stroke-dasharray`/`stroke-dashoffset` 动画；已完成元素保留，当前元素显示笔尖和线稿，未开始元素完全隐藏。场景结束后至少停留 500ms。画布提供当前字幕、幕号、生成中/播放中状态和停止按钮，并尊重 `prefers-reduced-motion`。

## 持久化与恢复

故事文件保存在 `~/.dongdong/stories/<story_id>/`，不写入工作区，也不提交用户故事内容：

```text
manifest.json
scene-001.json
scene-001.svg
scene-002.json
scene-002.svg
exports/
```

`manifest.json` 包含故事 ID、标题、创建/结束时间、状态、画布尺寸、场景顺序、每幕字幕和角色状态。故事状态为 `recording`、`stopping`、`ready` 或 `error`。WebState 启动时只读取最近故事索引；用户打开故事页时再读取对应 manifest 和 SVG，避免启动扫描大量素材。故事 SSE 断开后可用 `GET /api/stories/{id}` 重建队列。

## HTTP API 与 SSE

- `PUT /api/story/mode`：`{"enabled": true|false}`，按钮进入/结束故事。
- `GET /api/story/state`：当前故事 ID、模式、缓冲文本、队列和最近场景。
- `GET /api/story/events`：当前故事的场景、状态和导出进度 SSE。
- `GET /api/stories`：列出最近故事。
- `GET /api/stories/{id}`：读取 manifest、场景列表和导出文件。
- `POST /api/stories/{id}/replay`：重新推送已保存场景。
- `POST /api/stories/{id}/exports`：`{"format":"png|pdf|mp4"}`，返回 export job ID。
- `GET /api/stories/{id}/exports/{job_id}`：查询导出状态和下载路径。

所有故事文件路径都由 story ID 和固定目录拼接后校验，拒绝绝对路径、`..` 和符号链接越界。导出文件通过同源静态下载接口提供。

## 导出

PNG 和 PDF 从保存的 SVG 生成，优先使用项目已有 Python 依赖或浏览器无头能力；导出失败不影响源 SVG。MP4 按 manifest 的场景顺序和 `startMs`/`durationMs` 重放 SVG，生成 1280×720、30fps、H.264/AAC 文件。

FFmpeg 直接随项目入库：

```text
tools/ffmpeg/darwin-arm64/ffmpeg
tools/ffmpeg/LICENSE
tools/ffmpeg/VERSION
```

当前开发机为 arm64，第一版提交 arm64 可执行文件；后续需要 Intel 时再增加 `darwin-x86_64`。导出器只解析项目相对路径，不调用本机 `PATH`。启动导出前检查文件存在、可执行权限和记录的 SHA-256；缺失或校验失败时返回可读错误。版本、来源、SHA-256、许可证和二进制体积会记录在 `docs/decisions.md`。实现前必须确认 FFmpeg 的再分发许可证允许随项目分发。

## 失败处理与安全边界

- 模型超时、限流或返回非法 JSON：保存字幕，生成默认场景，向 SSE 发送降级原因。
- 场景文件写入失败：保留内存状态但标记故事错误，禁止假报“可导出”。
- 导出失败：保留所有 SVG 和 JSON，导出任务进入 `error`，不删除已有导出。
- 用户结束故事时，已开始的场景请求最多等待一个有限超时；超时的片段用默认场景收尾。
- SVG 只由固定 Python 组件生成，文本字段在写入 SVG 前转义；模型输出不允许带 `<script>`、外部资源或事件属性。
- 故事目录权限为 `0700`，文件权限为用户可读写；故事内容不发送到其它服务，除非调用已配置的文本模型生成分镜。

## 验证

需要加入 `tests/agent_mcp.py` 之外的单个故事协议边界测试文件 `tests/story_smoke.py`，覆盖：Scene JSON 校验和降级、切幕规则、路径越界、SVG 转义、manifest 恢复和 FFmpeg 路径校验。手动验证 Web 画布、语音进入/退出、刷新恢复和三种导出；不为普通 UI 胶水增加快照测试。

## 取舍与后续

第一版不生成图片、不渲染实时 MP4、不把目标仓库的 OpenCV/NumPy/AV视频环境复制进设备端。固定 SVG 组件牺牲部分画面丰富度，换取低延迟、角色一致性和可验证输出。后续可以在 `story_svg.py` 之后增加图片素材后端，或把同一 Scene JSON 交给更丰富的渲染器，实时协议和持久化格式不变。
