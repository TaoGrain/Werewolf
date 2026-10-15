# 多 Agent 狼人杀

这是一个可在本地完整运行的多 Agent 狼人杀项目，同时支持原有 6 人 `mvp-v1` 和经典 9 人猎人屠边 `classic-9-hunter-v1`。确定性规则引擎负责发牌、阶段转换、行动校验、死亡结算和胜负；LLM 只生成发言与行动意图。创建对局时可选择 MiniMax M3、DeepSeek V4 Pro 或 DeepSeek V4 Flash；未传模型选项的旧客户端仍使用后端默认 Provider。

## 已实现能力

- 完整状态机：大厅、发牌、预言家查验、狼人队内交流、狼人独立出刀、女巫行动、夜间结算、白天公布、顺序发言、投票、遗言与胜负。
- `mvp-v1` Ruleset：固定种子、狼人平票定刀、女巫首夜自救、同夜禁双药、狼人自刀、投票重投规则。
- `classic-9-hunter-v1` Ruleset：预言家、女巫、猎人、3 狼、3 民；采用屠边胜负，支持猎人死亡反应以及平票候选人 PK 发言和非候选人重投。
- 每名 Agent 独立会话、人格、消息历史与私有记忆；狼人交流通过团队私密记录共享，调用前由 `PlayerViewBuilder` 构造最小权限视图。
- 项目自有 `LLMProvider`、`ProviderRegistry`、统一请求/响应/用量/异常类型，以及 Fake、MiniMax、OpenAI-compatible 三种适配器。
- 模型 JSON 提取、Pydantic 校验和一次格式修复；规则引擎会再次校验目标与权限。
- SQLite + SQLAlchemy 2 + Alembic，含 11 张业务/审计表、追加式事件、命令幂等、事务回滚和服务重启恢复。
- FastAPI REST、SSE、后台阶段任务、安全观战过滤、Agent 检查器数据、事实型赛后复盘，以及按需生成并持久化的 AI 评分。
- Next.js 响应式界面：对局大厅、创建、主控制台、Agent 检查器、复盘页面；AI 复盘默认不调用模型，点击后可选择模型且默认继承对局模型。

## 本地启动

要求 Python 3.12+ 和 Node.js 20+。

首次运行先创建本地配置文件，并按需填写 MySQL 和模型密钥：

```powershell
Copy-Item .env.example .env
```

项目只使用根目录的 `.env`。后端、Alembic、前端公开 API 地址、启动端口和真实对局脚本都会读取该文件；`.env` 已被 Git 忽略，`.env.example` 只保存不含密钥的示例值。

项目根目录已提供前后端 CMD 启动脚本。分别打开两个 CMD 窗口运行：

```bat
cd C:\Users\123\Desktop\Werewolf
start-backend.cmd
```

```bat
cd C:\Users\123\Desktop\Werewolf
start-frontend.cmd
```

脚本默认读取 `.env` 中的 `BACKEND_HOST`、`BACKEND_PORT`、`FRONTEND_HOST` 和 `FRONTEND_PORT`。也支持把端口作为第一个参数临时覆盖，例如 `start-backend.cmd 8080`。启动脚本只负责启动服务，不会自动安装依赖或执行数据库迁移。

后端（PowerShell）：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

前端（另一个终端）：

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

打开 `http://127.0.0.1:3000`。后端 OpenAPI 文档位于 `http://127.0.0.1:8000/docs`。

默认 `LLM_PROVIDER=fake`，因此不需要密钥即可完成整局。

## 创建规则集

网页创建入口默认创建经典 9 人局。也可以直接调用 API；经典版必须使用 9 名玩家，省略姓名时由后端生成 9 个默认玩家和人格：

```powershell
$body = @{
  ruleset_version = "classic-9-hunter-v1"
  player_count = 9
  seed = 20260723
  god_mode = $false
  allow_private_inspection = $false
  model = "minimaxM3"
  thinking_enabled = $false
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/games" `
  -ContentType "application/json" `
  -Body $body
```

经典版固定阵容为 `3 狼 + 预言家 + 女巫 + 猎人 + 3 民`。好人消灭全部狼人获胜；狼人消灭全部神职、全部平民，或存活狼人数大于等于存活好人数时获胜。猎人被狼刀或放逐时可开枪或放弃，被毒杀时不能开枪；首轮投票平票后由候选人依次 PK 发言，再由存活的非候选人重投。

兼容旧 6 人客户端：不传 `ruleset_version` 时仍默认为 `mvp-v1`，人数固定为 6，既有接口和存档继续按原规则解释。显式创建 6 人局的最小请求示例：

```json
{
  "ruleset_version": "mvp-v1",
  "player_count": 6
}
```

两个版本都是已注册的固定预设，不支持任意人数、角色组合或在同一版本名下修改结算规则。

## MiniMax M3

密钥优先从根目录 `.env` 的 `MINIMAX_API_KEY` 读取；未设置时读取 `MINIMAX_API_KEY_FILE` 指向的文件。示例路径是项目根目录的 `minimax-apikey.txt`，该文件已加入 `.gitignore`，不得复制到前端、源码或日志。

在 `.env` 中设置 `LLM_PROVIDER=minimax`，并配置 `LLM_MODEL`、`MINIMAX_MODEL`、`MINIMAX_API_URL`、`LLM_MAX_TOKENS` 和 `LLM_TIMEOUT_SECONDS` 即可切换。

本次预检发现 `/v1/chat/completions` 对当前凭据返回 401，而 `/v1/text/chatcompletion_v2` 对 `MiniMax-M3` 返回 200，因此默认使用后者。Endpoint 始终可通过环境变量切换。`LLM_MAX_TOKENS` 默认 8192，避免推理模型在输出业务 JSON 前耗尽生成预算；创建对局时可关闭思考以控制延迟，`LLM_TIMEOUT_SECONDS` 会传递到每次真实模型请求。

## DeepSeek V4

密钥优先从 `.env` 的 `DEEPSEEK_API_KEY` 读取；未设置时读取 `DEEPSEEK_API_KEY_FILE`，示例路径是项目根目录的 `deepseek-apikey.txt`。该文件已加入 `.gitignore`，不会发送到前端或写入调用审计。

创建对局时可选择：

- `deepseekv4pro`：调用 `deepseek-v4-pro`。
- `deepseekv4flash`：调用 `deepseek-v4-flash`。

DeepSeek 请求使用 `https://api.deepseek.com/chat/completions`，并携带 `response_format: {"type":"json_object"}` 启用原生 JSON 输出。API 地址可通过 `DEEPSEEK_API_URL` 覆盖。

## 数据库和测试

`DATABASE_URL` 统一在根目录 `.env` 中配置。MySQL 示例：

```dotenv
DATABASE_URL=mysql+pymysql://用户名:密码@主机:3306/werewolf?charset=utf8mb4
```

用户名或密码含 `@`、`:`、`/` 等保留字符时，需要先进行 URL 编码。

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q

cd ..\frontend
npm.cmd run build
npm.cmd run test:e2e
```

开发经典 9 人规则时可先运行专项回归，再执行上面的全量回归：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests/test_classic_9_domain.py tests/test_classic_9_engine.py tests/test_classic_9_agents.py tests/test_classic_9_api_creation.py tests/test_classic_9_persistence.py tests/test_classic_9_review_api.py

cd ..\frontend
npm.cmd run build
npm.cmd run test:e2e
```

常用环境变量见根目录 [`.env.example`](.env.example)。前端只接收公开 API 地址，永远不接收模型密钥。

## 关键目录

- `backend/app/domain`：领域类型和 Ruleset。
- `backend/app/engine`：纯确定性状态机。
- `backend/app/agents`：玩家视图、AgentGateway、结构化输出和 Provider。
- `backend/app/persistence`：ORM、Repository、事务与审计记录。
- `backend/app/api`：REST、SSE 和服务端权限过滤。
- `frontend/src/app`、`frontend/src/components`：前端页面和游戏 UI。
- `doc/rules/mvp-v1.md`：6 人规则与状态转换基线。
- `doc/requirements/classic-9-hunter-v1.md`：经典 9 人猎人屠边版的冻结规则、需求和目标跟踪。
- `doc/reports/classic-9-hunter-release-acceptance-2026-07-23.md`：经典 9 人版部署、回滚和发布验收门禁。

## 当前限制

- 当前是本地单用户、单进程任务执行器；没有登录、多实例锁、Redis 或可靠任务队列。
- 仅支持 `mvp-v1` 与 `classic-9-hunter-v1` 两个固定预设；不含警长、守卫、自定义板子和真人混合玩法。
- SSE 在断线后通过事件序号和 REST 补偿恢复；进程终止时正在运行的模型请求需要主持人重试当前阶段。
- 事实时间线始终只读取已保存记录，不调用模型改写历史；逐 Agent 的 AI 评价与评分仅在用户主动点击后生成，并与原始事实分区显示。
