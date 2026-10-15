# 多 Agent 狼人杀项目综合代码审查报告

## 1. 文档信息

- 项目：多 Agent 狼人杀（Werewolf）
- 项目根目录：`C:\Users\123\Desktop\Werewolf`
- 审查日期：2026-07-23
- 报告性质：源代码、配置、测试、迁移、数据产物与需求一致性的只读审查
- 审查结论：当前版本适合作为受信任环境中的本机单用户 MVP；在接入真实付费模型、绑定非本机地址或进行多人/多实例部署前，必须先修复信息越权、内部推理落库、数据库一致性与并发幂等问题。

本报告是独立文档，不依赖审查时的聊天上下文。文中的代码行号以 2026-07-23 的工作区文件为准；后续修改代码后，行号可能移动。

## 2. 项目概况

项目实现了一个由六名 LLM Agent 参与的狼人杀 MVP：

- 后端：Python 3.12、FastAPI、Pydantic、SQLAlchemy 2、Alembic。
- 前端：Next.js 15、React 19、TypeScript、TanStack Query、Zustand。
- 数据库：SQLite 为当前 MVP，文档同时声明后续支持 MySQL。
- 实时通信：REST + SSE。
- 模型接入：Fake、MiniMax、DeepSeek 和 OpenAI-compatible Provider。
- 核心流程：发牌、夜间角色行动、狼人交流与出刀、白天发言、投票、遗言、胜负结算、Agent 审计和赛后复盘。

本次审查覆盖：

- `backend/app`、`backend/tests`、`backend/migrations`、`backend/scripts`
- `frontend/src`、`frontend/tests` 及前端构建配置
- 根目录启动脚本、环境配置示例和忽略规则
- `README.md`、需求文档、规则文档和既有报告
- 工作区中的 SQLite 数据库及 SQL 备份，仅做结构和聚合检查，不展示私密消息内容

规模约为：

- 后端业务、迁移与测试代码：46 个文件，约 7,382 行
- 前端业务与测试代码：26 个文件，约 1,727 行

未逐文件审查 `node_modules`、`.venv` 等第三方依赖源码。

## 3. 严重度定义

- **P0 / 阻断级**：可造成未授权读取、未授权控制、密钥或模型费用风险；在任何网络暴露或真实模型使用前必须修复。
- **P1 / 高严重度**：会破坏数据一致性、审计可信度、测试安全性或并发正确性；进入稳定使用前应修复。
- **P2 / 中严重度**：会造成错误行为、扩展阻塞、明显性能问题或用户体验缺陷。
- **P3 / 优化项**：工程化、可维护性、可访问性和长期成本方面的改进。

## 4. 总体结论

项目的确定性规则引擎和固定 `mvp-v1` 主流程基础较好。将默认数据库安全覆盖为隔离的内存 SQLite 后，后端 57 个自动化测试全部通过，说明发牌、状态转换、夜间结算、投票、平票、弃票、遗言和基础信息视图已有较完整的单元与集成验证。

目前主要风险不在基础规则，而在规则引擎外围：

1. 服务端把客户端传入的 `player_id` 和 `god_mode` 当作权限依据，活跃局可以绕过安全观战。
2. 前端的“安全观战”横幅与实际请求私密事件的状态相互独立。
3. 需求明确禁止保存的模型内部推理，被改名后写入数据库并通过 API 返回。
4. 应用导入即连接环境数据库并执行 `create_all()`，测试收集可能触达真实数据库。
5. `games.state_snapshot` 与规范化玩家、角色表已经发生可测量的数据漂移。
6. 并发启动、跨操作复用幂等键和多 worker 任务恢复存在重复执行窗口。
7. 前端 E2E 测试套件当前无法启动，关键安全回归没有自动保护。

## 5. P0：阻断级问题

### P0-01：活跃局可通过 `player_id` 冒充任意玩家

**位置**

- `backend/app/api/routes.py:378-436`
- 关键入口：`game_review(..., player_id: str | None = None)`

**问题机制**

`GET /api/games/{game_id}/review?player_id={player_id}` 直接信任客户端传入的 `player_id`，没有：

- 登录主体校验
- 玩家会话令牌校验
- `allow_private_inspection` 校验
- 活跃局禁止私密复盘的校验

当 `player_id` 存在时，代码会：

1. 从完整事件集合中筛选该玩家的 `PLAYER_PRIVATE` 和 `WOLF_TEAM_PRIVATE` 事件。
2. 在序列化后的玩家列表中补回该玩家真实角色。
3. 若该玩家是狼人，还会补回其他狼人角色。

公开的对局详情已经包含全部玩家 ID，因此调用方可以遍历每个玩家 ID，还原整桌身份和私密行动。

**额外跨角色泄漏**

`backend/app/api/routes.py:408-413` 对女巫私有事件采用“事件 payload 中存在 `target_id`”作为额外放行条件。预言家查验事件同样包含 `target_id` 和 `alignment`，因此女巫视角可能看到预言家的私有查验结果。

**影响**

- “安全观战”可以被直接绕过。
- 活跃局身份、狼人交流、夜间行动和查验结果可能泄漏。
- API 的信息隔离承诺不成立。

**修复建议**

1. 玩家身份必须来自不可伪造的认证会话，而不是查询参数。
2. 主持人、玩家和公开观战者使用不同权限主体。
3. 私有事件统一依据 `audience_player_ids` 或明确 ACL 判定。
4. 活跃局的复盘接口必须执行与 Agent 检查器相同或更严格的权限校验。
5. 删除女巫基于 `target_id` 的宽泛判断。
6. 增加“遍历全部玩家 ID 不得获取额外秘密”的回归测试。

### P0-02：前端显示“安全观战”时仍可请求主持人事件

**位置**

- `frontend/src/components/game-console.tsx:54-57`
- `frontend/src/components/game-console.tsx:65-69`
- `frontend/src/components/game-console.tsx:79`
- `frontend/src/components/game-console.tsx:171-177`

**问题机制**

前端存在两个独立状态：

- `godMode`：来自 URL，控制安全观战横幅和角色显示。
- `informationView`：控制传给 `useGameStream()` 的 `god_mode` 参数。

只要对局配置中的 `god_mode_enabled` 为真，即使当前 URL 没有 `god_mode=true`，主持人标签仍可点击。点击后 `informationView` 变为 `host`，前端会以 `god_mode=true` 请求 REST 和 SSE，但页面上方仍显示“安全观战中”。

从上帝视角切回安全模式时，`informationView` 也不会自动重置，旧的主持人事件流可能继续工作。

**验证结果**

使用无头 Chromium 和模拟 API 验证：

- URL 保持 `/games/game-001`
- 页面存在“安全观战中”横幅
- 点击“主持人信息”后成功显示 `WOLF_TEAM_PRIVATE` 事件

**修复建议**

1. 以服务端返回的有效权限 `data.god_mode` 作为唯一授权状态。
2. 当安全模式生效时强制 `informationView = "player"`。
3. 立即关闭旧的主持人 EventSource。
4. 安全模式下禁用主持人标签。
5. 增加“主持人切回安全模式后私密事件必须立即消失”的 E2E 测试。

### P0-03：服务非本机暴露时，所有对局和模型操作都可匿名调用

**位置**

- `backend/app/api/routes.py:176-284`
- 涉及创建、列举、启动、推进、暂停和重试接口

**问题机制**

系统没有认证、对局所有权、速率限制或模型额度控制。任何能访问后端的人都可以：

- 枚举所有 `game_id`
- 创建无限对局
- 选择真实模型和思考模式
- 推进、暂停、重试任意对局
- 触发付费 LLM 调用

CORS 只限制部分浏览器跨域请求，不限制脚本、命令行工具或服务端请求，因此不是安全边界。

**当前边界**

README 已声明 MVP 是本地单用户、单进程系统。只绑定 `127.0.0.1` 时风险受本机信任边界限制；一旦绑定 `0.0.0.0`、局域网地址、反向代理或公网地址，本问题立即成为阻断级风险。

**修复建议**

- 本机模式：启动时拒绝非 loopback 绑定，或要求显式危险确认。
- 网络模式：加入认证、对局所有权/RBAC、CSRF 策略、请求限流、模型额度、全局并发闸门和审计日志。

## 6. P1：高严重度问题

### P1-01：模型内部推理正文被持久化并返回

**需求约束**

`doc/requirements/requirements-analysis.md:319` 明确规定：

> `reasoning_content` 等内部推理内容不进入游戏记录、复盘界面或其他 Agent 上下文；只记录推理 token 数量等用量元数据。

**实际数据链路**

1. Provider 读取内部推理：
   - `backend/app/agents/providers/openai_compatible.py:49-52`
   - `backend/app/agents/providers/minimax.py:40-43`
2. Gateway 将其命名为 `reasoning_text`：
   - `backend/app/agents/gateway.py:149-153`
   - `backend/app/agents/gateway.py:232-237`
3. 脱敏器只过滤 `reasoning_content`、`thinking`、`chain_of_thought`，不处理 `reasoning_text`：
   - `backend/app/persistence/repository.py:38-57`
4. Recorder 写入 `response_snapshot`：
   - `backend/app/services/recorder.py:54-60`
5. API 原样返回：
   - `backend/app/api/routes.py:139-158`
6. 前端 Agent 检查器展示“思考”：
   - `frontend/src/components/agent-inspector.tsx:89-102`

**影响**

- 内部推理、可能的身份信息和敏感文本长期落库。
- SQL 备份会复制这些内容。
- 游戏结束后私密调用接口自动开放，扩大暴露范围。
- 实现与需求及 UI 中“隐藏推理不会展示”的文案相矛盾。

**修复建议**

1. 在 Provider 边界丢弃内部推理正文。
2. 只保留 `reasoning_tokens`。
3. 如确需解释，单独要求模型生成有限长度、可公开的业务理由。
4. API 对审计快照采用字段白名单，不返回厂商原始响应。
5. 清理现有数据库和备份中的 `reasoning_text`。

### P1-02：导入 `app.main` 就连接环境数据库并执行 DDL

**位置**

- `backend/app/main.py:21-24`
- `backend/app/main.py:101`
- `backend/tests/test_api.py:7`

**问题机制**

模块级 `app = create_app()` 会立即：

1. 读取根目录 `.env`
2. 创建数据库 Engine
3. 连接配置数据库
4. 执行 `Base.metadata.create_all()`

测试文件只是为了导入 `create_app`，也会先执行上述副作用。

**验证结果**

直接按 README 执行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
```

测试在收集 `tests/test_api.py` 时尝试连接根目录 `.env` 指定的数据库并失败，测试尚未有机会注入临时 SQLite。

将 `DATABASE_URL` 安全覆盖为内存 SQLite，并提供可写临时目录后：

```text
57 passed, 1 warning
```

**额外问题**

`create_all()` 只能创建缺失的表，不会为旧表补充新列、约束或正确执行数据迁移，因此不能替代 Alembic。旧 revision 数据库即使启动成功，运行时仍可能因缺列失败。

**修复建议**

1. 将无副作用的应用工厂与 ASGI 入口分开。
2. 使用 `uvicorn ... --factory` 或显式入口模块。
3. 测试在导入入口前注入隔离配置。
4. 生产启动只检查 Alembic revision，不执行 `create_all()`。
5. 数据库升级统一使用 `alembic upgrade head`。

### P1-03：`state_snapshot` 与规范化玩家、角色表持续漂移

**位置**

- 玩家死亡只修改领域对象：`backend/app/engine/game_engine.py:920-926`
- 结算只更新游戏快照：`backend/app/services/game_service.py:594-605`
- 玩家表只在创建时写入：`backend/app/services/game_service.py:114-121`
- 角色私有状态只在开始游戏时写入：`backend/app/services/game_service.py:631-646`
- 规范化列：
  - `backend/app/persistence/models.py:112`
  - `backend/app/persistence/models.py:134`

**现有数据证据**

对工作区 12 个 SQLite 数据库、17 局游戏进行只读聚合：

- 领域快照中共存在 25 个死亡玩家。
- `players.alive` 错配 25/25。
- 102 条角色分配中，`role_assignments.private_state` 错配 16 条。
- `games.phase`、`games.day` 和结束状态与快照之间暂未发现错配。

**影响**

- 当前 API 主要读取快照，因此表面功能可能正常。
- 后台统计、审计、数据迁移或未来直接查询规范化表时会得到错误结果。
- 数据库无法被视为可信的业务事实来源。

**修复建议**

两种方案二选一：

1. 以领域快照为唯一真源，删除或明确标记会陈旧的派生列。
2. 在同一事务内同步更新玩家、角色投影，并增加每次结算后的一致性断言。

### P1-04：并发开始游戏会重复提交旧状态

**位置**

- `backend/app/services/game_service.py:154-161`
- `backend/app/services/game_service.py:584-605`

**问题机制**

`start_game()` 在事务和命令锁外：

1. 检查命令是否存在
2. 读取 LOBBY 快照
3. 运行规则引擎

持久化时没有传入读取快照时的版本，而是使用锁行后“当前最新版本”。因此两个不同 command ID 的并发请求可以都从同一个旧 LOBBY 状态计算，并依次成功写入。

**复现结果**

模拟两个请求先读取同一 LOBBY 快照、再依次持久化后：

- `state_snapshot` 中有 9 个事件
- `game_events` 表中有 17 个事件
- 两个 start 命令都显示成功

FastAPI 的同步路由会在线程池中执行，因此单 worker 也可能触发。

**修复建议**

- 事务内原子认领 start 命令。
- 保存读取时的 `initial_version` 并作为 `expected_version`。
- 对事件增加 domain event ID 或 `(game_id, command_id, event fingerprint)` 唯一约束。
- 增加并发 start 回归测试。

### P1-05：SQL 审计备份未被忽略或加密

**位置**

- `backend/backups/werewolf-before-sqlite-merge-20260722-154033.sql`
- 根目录 `.gitignore:1-15`

**现状**

SQL 备份约 8.99 MB，包含：

- 160 条 `llm_calls`
- 1,030 条 `llm_messages`
- 109 条 `agent_memory_snapshots`
- 角色分配、事件、动作和投票记录

根目录忽略了 `*.db` 和 `*.sqlite3`，但没有忽略：

- `*.sql`
- `backend/backups/`

工作区还存在两个非空、按 Provider 命名的疑似密钥文本文件。它们已在 `.gitignore` 中，但打包或复制整个目录时仍会被包含。本次审查没有读取或展示密钥值。

**限制说明**

当前目录没有可用 Git 元数据，无法确认这些文件是否曾进入版本历史。

**修复建议**

1. 将数据库备份移出源码目录。
2. 忽略 `backend/backups/`、测试数据库、构建缓存和 `*.tsbuildinfo`。
3. 对审计备份加密并配置保留期。
4. 若项目曾被分享或上传，检查历史并轮换相关密钥。
5. 使用系统密钥存储或部署平台 Secret，不使用项目目录明文文件。

### P1-06：前端 E2E 测试套件无法启动

**位置**

- `frontend/playwright.config.ts:2`
- `frontend/root-env.mjs:5-6`

**问题机制**

Playwright 加载 TypeScript 配置时按 CommonJS 路径处理，随后静态导入使用 `import.meta.url` 的 `.mjs` 文件，触发：

```text
SyntaxError: Cannot use 'import.meta' outside a module
```

**验证环境**

- Node.js：`v24.14.1`
- Playwright：`1.61.1`
- `npm run test:e2e`：退出码 1
- 实际执行测试数：0

**影响**

现有前端测试文件看似覆盖了安全观战、主持人信息和复盘，但 CI 或开发者实际运行时一个用例都没有执行。

**修复建议**

- 将 Playwright 配置改为明确的 ESM，例如 `.mts` 或包级 `"type": "module"`。
- 或将环境读取器改为配置加载器可稳定消费的 CJS/TypeScript 模块。
- 在 CI 中强制要求测试数量大于 0。

### P1-07：多 worker 会把其他 worker 的正常任务误判为孤儿

**位置**

- 本地任务表与锁：`backend/app/services/game_service.py:62-63`
- RUNNING 判定：`backend/app/services/game_service.py:179-193`
- 创建后台任务：`backend/app/services/game_service.py:203-219`

**问题机制**

Worker B 能在数据库中看到 Worker A 的 `RUNNING` 命令，但看不到 Worker A 进程内的 `_tasks`。当前逻辑会据此认定该命令已中断，将其标记为 `FAILED`，随后创建新命令。

**影响**

- 重复 LLM 调用和计费
- 重复审计记录
- 两个任务争用同一游戏版本
- 滚动发布或横向扩容不安全

当前 README 明确声明单进程，因此这是已知部署边界；在增加 worker 或多实例前必须改为数据库 lease、owner/heartbeat、分布式锁或可靠任务队列。

### P1-08：模型重试、修复与并发缺少统一成本预算

**位置**

- 结构修复：`backend/app/agents/gateway.py:67-69`
- Gateway 调用循环：`backend/app/agents/gateway.py:101-121`
- Provider HTTP 重试：`backend/app/agents/providers/openai_compatible.py:67-85`
- 六人投票并发：`backend/app/services/game_service.py:401-432`

**问题机制**

- 一次结构化动作默认最多调用模型 3 次。
- 每次模型调用在 Provider 层又最多进行 3 次 HTTP 尝试。
- 六名玩家并发投票时，最坏可产生 `6 × 3 × 3 = 54` 次 HTTP 请求。

系统没有：

- 单阶段总 deadline
- 单阶段 token/费用预算
- 全局或每 Provider semaphore
- `Retry-After` 处理
- 外部请求幂等键

**修复建议**

- 将格式修复和网络重试纳入同一个总尝试预算。
- 为阶段设置总时间、总 token 和费用上限。
- 加入全局、单 Provider、单游戏三级并发限制。
- 对 429 使用 `Retry-After` 和带抖动退避。

## 7. P2：中严重度问题

### P2-01：幂等键没有绑定命令类型和请求内容

**位置**

- `backend/app/services/game_service.py:563-571`
- 唯一约束：`backend/app/persistence/models.py:272-274`

同一游戏中只要 `command_id` 相同，系统就返回旧结果，不比较 `command_type`、phase 或 payload。

已复现：

1. 使用 `command_id="same"` 启动游戏。
2. 再用同一 ID 请求 advance。
3. 服务返回 start 的 `SUCCEEDED / ROLE_ASSIGNED` 结果，游戏没有推进，也没有冲突提示。

应保存命令指纹并在重放前比较；不同操作或 payload 使用同一幂等键时返回 409。

### P2-02：结束游戏可被恢复为 ACTIVE

**位置**

- 暂停校验：`backend/app/services/game_service.py:551-556`
- 恢复逻辑：`backend/app/services/game_service.py:558-561`

`pause_game()` 会拒绝结束局，但 `resume_game()` 无条件写入 `ACTIVE`。`/retry` 会先恢复再推进，可能留下：

```text
status = ACTIVE
phase = FINISHED
```

恢复操作应只允许 `PAUSED -> ACTIVE`。

### P2-03：MySQL 离线迁移 SQL 无法生成

**位置**

- `backend/migrations/versions/d17f83a94c61_add_column_comments.py:22-35`
- `backend/migrations/env.py:27-37`

使用虚拟 URL、不连接数据库即可复现：

```powershell
$env:DATABASE_URL = "mysql+pymysql://u:p@localhost/db"
.\.venv\Scripts\python.exe -m alembic upgrade head --sql
```

注释迁移对离线 `MockConnection` 调用 `inspect()`，稳定报 `NoInspectionAvailable`。

现有迁移测试只运行在线 SQLite，而该迁移在 SQLite 中直接返回，因此实际 MySQL 逻辑没有覆盖。

### P2-04：LLM 调用无法追溯到最终动作和事件

数据库模型预留了：

- `PhaseAction.llm_call_id`
- `GameEvent.llm_call_id`

但 `GameApplicationService` 保存 action 和 event 时没有传入这些关联 ID。

现有数据库聚合显示：

- 200 条 `phase_actions` 的 `llm_call_id` 全为 `NULL`
- 同期存在 227 条成功 LLM 调用

这会使审计系统无法判断：

- 哪次调用真正产生了最终动作
- 哪次调用因版本冲突成为“幽灵调用”
- 哪个事件来自哪次模型响应

Recorder 应返回内部 call ID，并通过 `GatewayResult -> action -> event` 全链路携带。

### P2-05：女巫结构化输出缺少动作与目标一致性校验

**位置**

- `backend/app/agents/schemas.py:47-50`
- `backend/app/services/game_service.py:351-369`

当前模型允许以下互相矛盾的数据：

- `action="poison", target_id=null`
- `action="save", target_id=null`
- `action="pass", target_id=<玩家 ID>`

`poison + null` 最终会被转换成实际跳过；`save` 又忽略模型给出的目标并自动使用狼刀目标，导致解析结果、审计和真实动作不一致。

建议使用以 `action` 为判别字段的联合模型，或增加 `model_validator`。

### P2-06：Provider 响应、连接和安全标记处理不完整

**位置**

- `backend/app/agents/providers/openai_compatible.py:40-92`
- `backend/app/agents/providers/minimax.py:28-55`

具体问题：

- 只捕获超时，没有统一包装连接失败和协议错误。
- 没有严格验证响应 JSON、`choices`、`message` 和 usage 类型。
- 每次重试都新建 `AsyncClient`，无法复用连接池。
- MiniMax 的 `input_sensitive`、`output_sensitive` 仅写入元数据，既不阻止动作，也没有设置 `safety_flagged`。

建议使用 lifespan 级共享客户端、响应 DTO、响应体大小限制和统一错误分类。

### P2-07：SSE 前后端共同造成高频和平方级数据开销

**后端**

`backend/app/api/routes.py:320-334` 中，每个 SSE 连接每 250ms 使用同步 SQLAlchemy 查询数据库：

- 空闲连接仍产生约 4 QPS
- 多客户端会线性放大数据库压力
- 同步数据库访问运行在异步生成器中，可能阻塞事件循环

**前端**

`frontend/src/hooks/use-game-stream.ts:68-69` 对每条 SSE 消息执行：

```ts
invalidateQueries({ queryKey: ["games", gameId] })
```

这是前缀失效，会同时重取游戏详情和从 `after=0` 开始的完整事件历史。事件越多，每条新事件重新下载的数据越大，整体趋近平方级增长。

**修复建议**

- 后端使用事件通知、pub/sub 或至少无事件时指数退避。
- 前端只精确失效游戏详情。
- 事件使用 `setQueryData` 增量写入，断线时才按游标补偿。

### P2-08：SSE 切换视角存在陈旧异步结果竞态

**位置**

- `frontend/src/hooks/use-game-stream.ts:48-59`
- `frontend/src/hooks/use-game-stream.ts:83-89`

旧连接 `onopen` 中的补偿请求完成后，没有再次检查 `stopped`、连接代次或当前 `streamKey`。切换公开/主持人视角时，旧请求可能覆盖新视角状态。

建议：

- 使用 `AbortController`
- 为每次连接分配 generation ID
- 每次 `await` 后验证 generation 和 `streamKey`

### P2-09：Agent 检查器的前端权限判断与服务端不一致

**位置**

- `frontend/src/components/agent-inspector.tsx:28-40`

前端使用：

```ts
privateAccess = godMode || game.data?.phase === "FINISHED"
```

但服务端在活跃局还要求：

- 对局配置允许上帝视角
- 当前请求有效开启上帝视角
- `allow_private_inspection = true`

用户仅在 URL 加 `?god_mode=true`，页面就会先宣称“私密调试视角”，随后调用接口并收到 403。

前端应使用服务端返回的有效权限字段，而不是 URL 意图。

### P2-10：对局名称被完整丢弃，大厅状态也不准确

**位置**

- 前端提交名称：`frontend/src/components/create-game-form.tsx:42`
- 后端创建路由：`backend/app/api/routes.py:176-189`
- 详情固定名称：`backend/app/api/routes.py:98`
- 大厅摘要：`backend/app/services/game_service.py:670-679`
- 大厅存活人数：`frontend/src/components/game-list.tsx:52-68`

前端提交 `name`，但后端没有传给服务层、没有数据库字段，并固定返回“狼人杀对局”。

大厅列表又不返回玩家信息。前端遇到空玩家列表时回退为六名全部存活，因此结束局仍可能显示 `6 / 6 存活`。

应持久化名称，并为大厅提供专用 DTO，至少包含名称、真实存活人数、总人数和胜方。

### P2-11：生产环境缺少配置时会静默使用 localhost

**位置**

- `frontend/root-env.mjs:31-35`
- `frontend/next.config.mjs:3-6`

`root-env.mjs` 将根目录 `.env.example` 当作运行时回退。由于示例文件含 `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`，生产部署即使漏配真实 API 地址也能构建成功，最终浏览器却访问用户自己机器的 localhost。

示例文件应只用于说明；生产构建必须要求显式配置。

### P2-12：未知游戏阶段被静默伪装为 LOBBY

**位置**

- `frontend/src/lib/schemas.ts:41`
- `frontend/src/components/game-console.tsx:87`

`GamePhaseSchema.catch("LOBBY")` 会把后端新增阶段或拼写错误改成 `LOBBY`。控制台随即显示“开始游戏”并可能发送错误命令。

契约错误应直接失败，或映射到不可操作的 `UNKNOWN` 状态。

### P2-13：复盘指标口径与服务端不一致

**位置**

- `frontend/src/components/review-dashboard.tsx:44-48`
- `frontend/src/components/review-dashboard.tsx:63-68`

问题包括：

- “无效行动”通过匹配事件类型中的 `INVALID|FAILED` 计算，可能把模型失败误算为规则无效行动。
- 忽略服务端 `metrics.invalid_action_count`。
- Token 回退未包含 `reasoning_tokens`。
- 调用数和平均延迟没有优先使用服务端权威指标。

应明确指标定义并统一由服务端计算。

### P2-14：复盘页在常见手机宽度横向溢出

**位置**

- `frontend/src/app/globals.css:171`
- `frontend/src/app/globals.css:253-256`

实测：

- 320px 视口：文档宽约 419px
- 360px 视口：文档宽约 419px
- 375px 视口：文档宽约 419px

`.review-layout` 的 `1fr` 会被筛选器的 min-content 宽度撑开。

建议：

- 使用 `grid-template-columns: minmax(0, 1fr)`
- 为 `.review-timeline`、`.review-sidebar` 等 Grid 子项设置 `min-width: 0`

### P2-15：Ruleset 的可扩展能力目前只是名义支持

**位置**

- `backend/app/domain/rules.py:9-20`
- `backend/app/engine/game_engine.py:357-370`
- `backend/app/engine/game_engine.py:399-400`

当前固定 `mvp-v1` 默认配置可工作，但自定义规则存在问题：

- `frozen=True` 只浅冻结模型，可变 `role_counts` 字典仍可原地修改。
- 零狼配置可绕过 validator，并在启动或狼人结算时失败。
- `wolves_win_at_parity=False` 时缺少完整替代终局条件。
- 部分 Ruleset 字段没有真正驱动状态机。

在正式声明支持自定义规则前，需要深层不可变结构和非默认分支测试。

### P2-16：外键不能保证关联对象属于同一游戏

例如 `RoleAssignment.game_id` 和 `player_id` 是两个独立外键。数据库可以接受：

- `game_id` 属于游戏 A
- `player_id` 属于游戏 B

AgentSession、Vote、LLMCall、PhaseAction 和 Memory 存在相同模式。当前服务正常路径通常传入同一局 ID，但 Repository 调用错误会形成跨局审计污染。

建议增加 `(game_id, id)` 复合唯一键和复合外键，或在 Repository 统一校验归属。

## 8. P3：工程化与体验优化

### P3-01：会话历史和审计消息无界增长

**位置**

- `backend/app/agents/gateway.py:46`
- `backend/app/agents/gateway.py:70-98`
- `backend/app/agents/gateway.py:216-218`
- `backend/app/services/recorder.py:39-51`

Gateway 永久保存每个玩家的完整历史；每个新用户消息又包含不断增长的公开事件视图。Recorder 每次调用还会重复保存此前全部消息。

长期影响：

- 内存持续增长
- 输入 token 和费用增长
- 数据库消息接近二次增长
- 最终触发模型上下文长度限制

建议增加每 session 锁、token 窗口、摘要、结束局 TTL 和显式清理。

### P3-02：列表、事件、调用、记忆和复盘缺少分页

增长型接口均一次性返回全部记录。长局会同时增加：

- 数据库扫描
- JSON 序列化
- 网络传输
- Zod 解析
- React DOM 节点

应采用游标分页，并为时间线和调用列表增加虚拟化。

### P3-03：输入约束与数据库约束不一致

- `player_names` 没有元素长度限制，但数据库 `players.name` 长度为 100。
- 请求体中的 `command_id` 限制 128 字符，但 `Idempotency-Key` Header 没有限制，数据库字段仍为 128。
- 路径 ID 使用普通字符串，没有 UUID 格式校验。
- 请求体未统一设置 `extra="forbid"`，拼写错误字段可能被静默忽略。

应在 API DTO 层严格限制请求大小、列表长度、单项长度、Header 长度和 ID 格式。

### P3-04：错误映射不稳定且可能泄漏内部细节

启动和推进接口把多数异常统一转换为：

```text
409 + str(exc)
```

后台任务又把原始异常文本写入命令错误并返回前端。数据库、网络或内部实现细节可能泄漏，客户端也无法区分：

- 规则冲突
- 暂时性 Provider 故障
- 数据库错误
- 程序缺陷

建议集中异常处理，返回稳定错误码、脱敏消息和 trace ID，详细堆栈只写服务器日志。

### P3-05：应用缺少完整 lifespan

当前没有统一处理：

- 取消或等待后台任务
- 关闭共享 HTTP 客户端
- `Database.dispose()`
- 停机前更新 RUNNING 命令

异常关闭可能长期留下 RUNNING 命令。应通过 FastAPI lifespan 管理资源生命周期。

### P3-06：健康检查只返回固定 ok

`GET /health` 不检查数据库，也不反映启用 Provider 的配置有效性。建议拆分：

- `/health/live`：进程存活
- `/health/ready`：数据库 revision、连接和必要配置就绪

### P3-07：可访问性不足

确认的问题包括：

- 自定义开关输入透明，但代理滑块没有 `:focus-visible`。
- Agent 调用明细使用 `role="table"`、`role="row"`，但没有列头和单元格语义。
- tabs 缺少 `tabpanel`、`aria-controls` 和方向键操作。
- 多处小字颜色对比度低于普通文本 4.5:1。

建议加入 axe 自动检查和键盘操作 E2E。

### P3-08：工程质量工具不足

后端当前只有 pytest，没有明确配置：

- Ruff 或其他 lint/format
- mypy/pyright
- coverage 阈值
- Python 锁文件

前端方面：

- `next lint` 已提示废弃，应迁移到 ESLint CLI。
- 缺少独立 `typecheck` 脚本。
- `package.json` 没有声明 Node engines。
- Playwright 仅配置桌面 Chromium。

建议增加 CI，至少执行：

```text
backend: lint + typecheck + pytest + migration check
frontend: eslint + tsc + build + playwright
```

## 9. 数据治理缺口

需求文档已经提出：

- 按游戏删除全部相关数据
- 配置 LLM 对话记录保留时间
- 完整提示词和响应可加密存储

当前未发现：

- 删除游戏 API
- 定时保留策略
- 数据加密
- 备份清理策略
- 会话历史清理

由于项目保存完整提示词、私密身份、模型响应和 Agent 记忆，该缺口应在使用真实用户数据或长期运行前处理。

## 10. 自动化验证结果

| 检查项 | 结果 | 说明 |
|---|---|---|
| 后端普通 `pytest -q` | 失败 | 测试收集时导入 `app.main`，触达根目录 `.env` 指定的数据库 |
| 后端隔离数据库测试 | 通过 | 57 passed，1 条 Starlette/httpx 兼容性警告 |
| `pip check` | 通过 | 未发现已安装 Python 依赖冲突 |
| 前端 ESLint | 通过 | 同时提示 `next lint` 将被移除 |
| TypeScript `tsc --noEmit` | 通过 | 无类型错误 |
| Next.js 生产构建 | 通过 | 页面首载约 130–186 kB |
| 前端 E2E | 失败 | 配置加载阶段 ESM/CJS 错误，0 个用例执行 |
| 前端生产依赖审计 | 通过 | 当次检查报告 0 个漏洞 |
| MySQL Alembic offline SQL | 失败 | 注释迁移无法 inspect MockConnection |

## 11. 关键测试缺口

建议新增以下自动化测试：

### 安全和隐私

- 活跃局遍历所有 `player_id` 不得获得角色或私有事件。
- 女巫不得看到预言家查验。
- 安全观战切换前后不得保留主持人 SSE。
- API 返回及数据库中不得出现 `reasoning_text`、`reasoning_content`、`thinking`。
- 非授权主体不得创建、推进或读取其他对局。

### 并发和幂等

- 同一游戏并发 start，不同 command ID 只能有一个成功。
- 同一幂等键跨 start/advance/retry 使用时返回冲突。
- 两个 `GameApplicationService` 实例模拟多 worker。
- 进程崩溃、租约过期和安全恢复。
- finished 游戏不可 pause、resume、retry。

### 持久化

- 每次结算后校验快照与玩家/角色投影一致。
- action、event 与 LLM call 必须完整关联。
- 跨游戏外键污染必须失败。
- SQLite 和 MySQL 从旧 revision 带数据升级。
- MySQL 离线 SQL 可以成功生成。

### 前端

- Playwright 配置必须成功加载且测试数大于 0。
- SSE 实际发送 `data:` 帧，覆盖重连、补偿、乱序和去重。
- 320、360、375px 移动视口无横向滚动。
- Firefox、WebKit 和键盘可访问性。
- 对局名称与大厅存活人数使用真实 API 验证，不只依赖 mock。

## 12. 推荐修复顺序

### 阶段 0：立即收紧风险边界

1. 禁用活跃局的 `review?player_id=` 私密读取，直到服务端权限模型完成。
2. 修复前端安全观战与主持人事件流状态分裂。
3. 停止保存和返回模型内部推理正文。
4. 强制本机绑定，或在网络暴露前加入认证和限流。
5. 将 SQL 备份、密钥和运行数据库移出源码目录。

### 阶段 1：修复数据与命令正确性

1. 移除导入时 `create_all()`。
2. 以 Alembic 作为唯一迁移机制。
3. 明确数据库唯一真源并修复现有投影漂移。
4. 为 start/advance/retry 建立数据库原子认领、版本校验和命令指纹。
5. 禁止 finished 游戏恢复。
6. 补齐 LLM call 到 action/event 的关联。

### 阶段 2：恢复可信测试

1. 修复 Playwright ESM 配置。
2. 后端测试默认使用隔离数据库，禁止读取开发 `.env`。
3. 增加安全、并发、投影一致性和 MySQL 迁移测试。
4. 建立 CI 阻断规则。

### 阶段 3：控制性能与模型成本

1. 增加总 deadline、token/费用预算和并发 semaphore。
2. 复用 HTTP 客户端。
3. 改造 SSE 为事件驱动或退避轮询。
4. 前端采用增量缓存，不再每条事件重取完整历史。
5. 为历史、事件和审计记录增加分页、摘要和 TTL。

### 阶段 4：完善产品与体验

1. 持久化对局名称并修复大厅摘要。
2. 修复移动端复盘溢出。
3. 统一复盘指标口径。
4. 完善键盘操作、ARIA 语义和颜色对比度。
5. 实现按局删除、保留期和加密备份。

## 13. 上线前最低验收标准

在接入真实付费模型或允许非本机访问前，至少应满足：

- 活跃局无法通过任意 query 参数获取额外私密信息。
- 安全观战模式的所有网络请求均不携带私密权限。
- 数据库和 API 中不存在模型内部推理正文。
- 服务启动与测试导入不会自动修改环境数据库结构。
- 并发 start/advance 不产生重复事件或重复模型调用。
- 快照和规范化业务表具有明确、可验证的唯一真源。
- E2E 测试可以实际执行，并覆盖安全模式切换。
- SQL 备份、数据库和密钥不位于可分发源码包中。
- 真实模型调用具有总并发、总超时和费用上限。

