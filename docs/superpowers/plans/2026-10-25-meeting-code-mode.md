# 会议代码修改模式实施计划

> 执行方式：用户确认设计并明确要求直接实施；本会话 inline 执行，遵循项目不 TDD、仅边界 smoke 的约定。

**Goal:** 交付会议讨论、显式整理、Birdview 地图、按钮/语音确认、实施验证闭环。
**Architecture:** SQLite 持久记录会议与不可变方案；现有 Runtime 受限工具生成方案，确认后复用执行宿主；上游固定渲染器输出隔离 HTML。
**Tech Stack:** Python、FastAPI、SQLite、React、Birdview Node 渲染器。
**Spec:** docs/superpowers/specs/2026-10-25-meeting-code-mode-design.md

## Global Constraints
- 文件最多 300 行，自有模块按单一职责拆分；第三方源码完整保留许可证。
- 不触碰原 checkout 的其他调度工作，不启动用户麦克风，不控制浏览器。
- 不安装完整 49 MB 图标包，只分发渲染使用的图标；固定上游提交及依赖锁。
- 生成阶段无 shell/产品写入/外部 MCP；确认按版本和讨论截止序号领取一次。

## Review Focus
- STT 与 MCP 重复、否定/引用、按钮语音竞争：控制解析和数据库领取边界 smoke。
- 方案生成时新意见、旧展示回执：版本/截止序号 smoke。
- HEAD 未变但工作区变：基线指纹 smoke。
- symlink 资源逃逸、预览同源权限：路径 smoke、HTTP/CSP 验证。
- 重启/取消/扩范围：持久状态与任务清理 smoke、端到端验证。

### Task 1: 会议存储与受控 Birdview
Files: meeting_store.py、birdview.py、vendor/birdview、scripts/setup-birdview.sh、skills.py、tests/smoke.py。
- [x] 保存开启状态、讨论序号、不可变方案、展示和确认；SQLite 条件更新原子领取。
- [x] 固定渲染器版本、许可和图标子集；Node 固定参数执行及超时，基线指纹覆盖 tracked/untracked 文件。
- [x] 技能资源只读访问、路径边界；边界 smoke、check、提交推送。

### Task 2: 生成、确认、实施与语音入口
Files: meeting.py、meeting_tools.py、meeting_routes.py、runtime.py、web_runtime.py、web_server.py、tools.py。
- [x] Meeting(host) 管理显式召唤、固定讨论快照、确认版本、重启失效、任务关联。
- [x] Runtime 接收受限工具集及受控回调；生成提交完整结构化方案，渲染成功才发布。
- [x] 语音完整指令直接控制；MCP 模式内不能绕过 STT 授权，普通讨论只记入会议。
- [x] 确认后范围内实施、活动及检查记录、扩范围停止、取消收尾；check、提交推送。

### Task 3: 工作台与完整验收
Files: Meeting.jsx、main.jsx、style.css、README.md、docs/runbook.md、docs/architecture.md、docs/decisions.md、docs/linx-voice-prompt.md。
- [x] 模式目录、讨论、显式整理、历史方案、隔离 iframe 展示回执、确认和结果回看。
- [x] HTTP/SSE 完整流程、真实上游校验渲染、前端 build；fresh review、修复、全检查。
- [x] 更新说明和本计划证据，提交推送；不自动合并、不重启用户服务。

## 验证记录

- `./scripts/check.sh` 通过，会议新增覆盖明确控制、原子领取/新意见/重启失效、未开放工具和范围外写入、未提交/未跟踪源码指纹。
- `npm run build --prefix apps/web` 通过；未控制浏览器，未打开麦克风。
- 上游固定版本 `birdview.mjs doctor` 通过；实际依赖约 8MB，图标 21 个，保留许可。
- `~/tmp/dongdong-meeting/integration.py` 在隔离 Git 项目实测：真实来源发现/规则编译/地图渲染、HTTP 展示/确认、STT 确认、MCP/TTS 不越权、实际写文件+命令验证、活动/SSE/diff、重复确认无额外 run。
- `~/tmp/dongdong-meeting/cancel.py`：在收尾渲染阶段取消，执行锁释放且持久记录 stopped。
- Fresh reviewer 发现并修复：疑问句误确认；新增/暂存文件 diff 缺失；活动地图无法即时读取；失败 iframe load 被当作展示；非 Git 根目录迟报错；取消收尾导致互斥锁残留。
- Ruling: 单独 `runtime_context.py` 承载原有提示与裁剪以保持 Runtime ≤300 行；自有新增模块均≤300行；第三方分发与既有唯一边界 smoke 的长度例外在 decisions.md 记明。
- 交付限制：模型工具流程用可控流完成，不等于真实模型质量评估；页面视觉和灵矽会议实录需真机验收。shell 维持既有本机权限，无操作系统沙箱。
- 审查追加修复：领取预检期间禁止并发生成；shell 前后核对源码变化，范围外修改停止并在实际结果列出。`~/tmp/dongdong-meeting/scope.py` 真实命令写 outside.txt，确认流程终止、错误及结果保留范围外文件；通过。
