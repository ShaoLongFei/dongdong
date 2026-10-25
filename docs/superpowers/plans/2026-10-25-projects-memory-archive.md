# 项目、分层记忆与归档实施计划

**Goal:** 项目绑定目录，按项目整理会话、隔离记忆，并支持保留全部记录的归档/恢复。

**Architecture:** 原 Store 增量迁移，具体 projects router 管理项目/会话；WebState 决定运行目录与明确选择的语音项目。Mem0 使用独立 user_id 命名空间隔离全局/项目，Runtime 通过具体 memory_tools 模块接入。侧栏和记忆页使用现有 React，不新增服务。

**Spec:** `docs/superpowers/specs/2026-10-25-projects-memory-archive-design.md`，用户2026-10-25已批准并要求开始实施。

## Global Constraints

- 单文件≤300行，不TDD，默认不写UI/配置测试；迁移、路径、归档与删除边界只加tests/smoke.py。
- 不使用用户配置/记忆/数据库验证，不调用计费OpenAI服务、不自动采音、不控制浏览器。
- 原checkout的design/不改；复用隔离worktree，统一提交、check后pull --rebase并push，交付集成当前分支。
- 共享文件由root接入；worker不自行commit、不撤销别人修改。用户此前选择的并行实施方式沿用。

## Review Focus

- 旧库幂等迁移保留全部ID与记录；归档不删除任何内容。
- 项目目录与记忆范围由会话固定，浏览历史不能改变活动任务。
- Mem0提取/更新也隔离，不能只在查询时过滤；删除混合范围整体拒绝。
- 归档会话不能发送任务，语音不能重新使用归档指针。
- SDK阻塞操作在线程执行，失败不阻断普通任务；取消后不得误报记忆写入成功。

## Task 1: Store、项目与归档（root）

文件：schema.sql、store.py、projects.py、web_runtime.py、web_server.py。

- [x] projects(id,name,path,created_at)，sessions增project_id/archived_at；PRAGMA列检查后事务迁移、索引，旧记录默认NULL。
- [x] Store.create_session(title,channel,project_id=None)，list_sessions(project_id=None,archived=False,limit=20,offset=0)；all列表由明确API参数提供。project_id空字符串为未分类，None为全部（兼容CLI）。列表返回has_more。
- [x] GET/POST /api/projects；PATCH /api/projects/{id}仅name；PUT /api/projects/current {project_id:null|string}；GET返回projects,current_project。
- [x] GET /api/sessions?project_id=&archived=false&limit=20&offset=0；POST {title,project_id:null|string}；PATCH /api/sessions/{sid} {project_id?,archived?:bool}。
- [x] 项目真实目录存在且唯一；项目路径固定，可在全局workspace之外。会话移动/归档/当前项目切换持有gate，运行中409；归档只读、恢复不改updated_at。
- [x] WebState.current_project 与每项目task/voice指针；set_project显式切换；Web文字按会话目录执行，不隐式切换语音项目；新会话沿用显式项目参数。语音按current_project创建/复用，会话指针归档时清除。

## Task 2: 分层记忆（worker，独占）

文件：memory.py、memory_tools.py、memory_routes.py；不改Runtime/tools/root文件。

- [x] 保留search/save/list_/delete现有签名兼容，新增namespace(device_id,project_id=None)：全局保持原device_id，项目为device_id+':project:'+project_id。
- [x] Mem0所有同步操作及初始化用RLock串行；范围隔离用user_id，delete先查全部归属。错误不泄露正文/key。
- [x] memory_tools.SPECS含memory_search(query)、memory_save(text,scope='project'|'global')；async context(cfg,project_id,user_text)->str用于检索，async call(name,args,cfg,project_id)->str；所有SDK操作to_thread。无project时project scope回落global；搜索合并global/current各≤5，总注入≤4000字符，参考事实不是指令。无key自动检索跳过；失败由root记memory_error。
- [x] router(state)：GET /api/memories?project_id=，空为global；POST {project_id:null|string,text}；DELETE {project_id:null|string,memory_ids:[...]}。验证真实project，返回{enabled,memories:[{id,memory,scope}]}；无key列表enabled:false，不误报空。写删gate，to_thread等待期间gate不提前释放（包括取消）。
- [x] 临时本地SDK/Qdrant夹具验证命名空间隔离，不调用用户数据或OpenAI；报告可用接口和验证证据。

## Task 3: 前端（worker，独占）

文件：main.jsx、Sidebar.jsx、Memories.jsx、style.css、Conversation.jsx；不改后端。

- [x] API按Task1/2。Sidebar({selected,onChoose,onNew,state,report,onChanged})自己加载projects和分组sessions，每组limit20，加载更多；刷新不把所有历史拉回。创建项目/重命名/设当前语音项目、未分类和归档入口。普通查看onChoose不改执行项目。
- [x] main移除旧平铺列表轮询，使用Sidebar；onNew(project_id)创建相应会话，发送无选择时用current_project；展示会话project_path实际目录。
- [x] 详情提供移动项目、归档/恢复；归档隐藏composer，恢复后才能续聊。运行时按钮禁用并处理409。归档不影响全局审批。
- [x] Memories({report})显式范围选择global/projects、列表/添加/定向删除；请求乱序丢弃旧结果，未配置显示未启用。
- [x] Conversation展示memory_error与memory_search/save中文；npm build和SSR验证，不操作浏览器。

## Task 4: 集成、边界与交付（root）

- [x] Runtime从session固定project，自动注入context与memory_tools.SPECS/call；memory_save加入ask。保持≤300行。
- [x] tools.self.memory.*从绑定WebAgent读取current_project，开始调用固定命名空间；scope可显式global/project，不让模型传任意project；独立入口global。
- [x] tests/smoke.py保留旧覆盖、补迁移/归档/删除边界且≤300；CLI临时完整REST/Runtime/语音夹具、旧库/分页/归档恢复/多目录隔离；不重复无变化检查。
- [x] 独立审查、修复、check/build、更新architecture/runbook/nav与状态；重建隔离分支静态资源并提交推送功能分支。主目录正在并行实现调度器，本轮不合入主目录、不重启服务。

## 执行记录

- 起点016c093；三个任务共享接口如上，root串行接入公共文件。用户已明确批准设计并要求实施，不重复请求许可；不采用技能模板TDD，因为AGENTS明确禁止。

- 前端build/SSR与本地Mem0命名空间夹具验证通过；文档已同步项目目录、归档和Runtime分层检索。独立review与集成边界验证通过，check全绿；未进行真实浏览器或云端记忆验收。

- 交付调整：主checkout已出现另一任务的f7c117e及tools/web_runtime/web_server/smoke等未提交调度器改动，为保留其工作，本轮仅推送独立功能分支，不执行原快进集成步骤。后续需合并两路Store/schema/API改动后统一运行。
- 验证：旧schema两次迁移保持全部记录、两项目目录与符号链接边界、分页/归档恢复/运行中409、语音逐项目续聊；本地真实Mem0+Qdrant验证命名空间与混合删除拒绝；Runtime记忆审批/失败继续/取消等待线程结束。check.sh与Vite构建通过，独立审查无剩余P1/P2。

- 2026-10-25 整合main：与任务DAG、会议方案图和故事场景基础一起完成公共执行入口集成；原“不合主目录”交付限制已由本次主分支合并取代。归档会议只读，会议启用期间先退出再改项目；Runtime复用调度run，取消等实际worker退出。
