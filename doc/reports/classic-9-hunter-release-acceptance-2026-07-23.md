# 经典 9 人猎人屠边版发布验收报告

## 1. 文档状态

| 项目 | 内容 |
|---|---|
| 目标规则版本 | `classic-9-hunter-v1` |
| 兼容规则版本 | `mvp-v1` |
| 验收日期 | 2026-07-23 |
| 本地发布候选 | **GO**：代码、迁移、构建、全量回归和真实 FakeProvider 整局均通过 |
| 生产创建入口 | **NO-GO**：待生产备份恢复演练、现场迁移、版本化制品、监控和人工批准 |
| 需求基线 | `doc/requirements/classic-9-hunter-v1.md` |

本报告完成 G-12 的开发与本地发布候选验收，但不代表已经部署到生产。自动化能够证明候选代码行为；生产数据库备份、恢复演练、真实密钥、监控和发布责任人必须在目标环境另行确认。

## 2. 冻结范围

本次候选版本包含：

- 保留 6 人 `mvp-v1` 的默认创建、旧快照、胜负语义、页面和复盘兼容。
- 新增固定 9 人 `classic-9-hunter-v1`：3 狼、预言家、女巫、猎人、3 民。
- 经典屠边胜负：灭狼优先；仍有狼人时，神职全灭或平民全灭则狼人胜；人数相等本身不结束。
- 猎人被狼刀或放逐时可开枪/放弃；毒杀及刀毒同时命中禁枪；动作可恢复且幂等。
- 首轮平票进入候选人 PK 发言，第二轮仅存活非候选人可投；再次平票、全弃票或无投票人均无人出局。
- API、持久化、审计、事件、Agent 检查器、复盘和响应式 9 人桌完整识别实际规则版本及新增阶段。
- Provider 并发、总尝试、阶段超时、Token 预算、上下文窗口和按玩家检查点保护。

本次不包含警长、守卫、自定义板子、真人混合、多实例任务调度或把 6 人存档转换为 9 人存档。

## 3. 接口与离线配置

经典版创建请求：

```json
{
  "ruleset_version": "classic-9-hunter-v1",
  "player_count": 9,
  "seed": 20260723,
  "god_mode": false,
  "allow_private_inspection": false,
  "model": "minimaxM3",
  "thinking_enabled": false
}
```

兼容约束：

- 省略 `ruleset_version` 时仍为 `mvp-v1`；9 人和 6 人规则分别严格校验人数及姓名数量。
- `LLM_PROVIDER=fake` 是本地离线总开关：即使请求显式选择 MiniMax 或 DeepSeek 模型键，也统一使用同一个 FakeProvider 实例，不访问外部服务，同时保留所选模型名用于审计。
- 生产容量保护项为 `LLM_PROVIDER_CONCURRENCY_LIMIT=4`、`LLM_PROVIDER_MAX_RETRIES=2`、`LLM_GATEWAY_MAX_TOTAL_ATTEMPTS=9`、`LLM_HISTORY_MAX_MESSAGES=12`、`LLM_HISTORY_MAX_CHARACTERS=20000`、`LLM_PHASE_TIMEOUT_SECONDS=120`、`LLM_PHASE_OUTPUT_TOKEN_BUDGET=1000000`。
- 前端只读取 `NEXT_PUBLIC_API_BASE_URL`；模型密钥不得进入任何 `NEXT_PUBLIC_*` 配置。

## 4. 最终自动化执行记录

| 检查项 | 命令或证据 | 最终结果 |
|---|---|---|
| 后端全量 | `python -m pytest -p no:cacheprovider --basetemp C:\tmp\werewolf-g12-final-backend-v3 -q` | **119 passed**，1 条既有 Starlette TestClient 弃用警告，25.53s |
| 前端生产构建 | `npm run build` | 编译、Lint、TypeScript、5/5 静态页面及构建 trace 全部通过 |
| 前端 Chromium 回归 | `npx playwright test --project=chromium --workers=1` | **20 passed，1 skipped**，1.0m；跳过项仅为按环境变量门禁的真实 API 用例 |
| 真实前后端整局 | `REAL_API_E2E=1` 执行 `tests/real-api.spec.ts` | **1 passed**，36.9s（测试主体 20.2s） |
| Alembic | `alembic heads`；在全新隔离 SQLite 执行 `upgrade head`、`current` | 单一 head `f3c1a9b872de`，完整迁移链通过 |
| 经典规则专项 | `test_classic_9_engine.py`、`test_classic_9_agents.py` 等 | 引擎 17 passed，Agent 7 passed，均纳入 119 项全量 |
| 旧 6 人兼容 | 原有领域、引擎、服务、持久化、API 测试及旧存档前端复盘用例 | 全部纳入后端 119 项和前端 20 项通过结果 |

执行环境：

- Python `3.12.10`
- Node.js `v24.14.1`
- npm `11.11.0`
- Playwright `1.61.1`，Chromium `149`
- 数据库：隔离 SQLite；迁移 head `f3c1a9b872de`
- 候选提交标识：当前工作区不是 Git 仓库，无法提供提交 ID；生产发布前必须生成可追溯的版本化提交或制品

警告与豁免：

- 后端仅有 Starlette TestClient/httpx 的既有弃用警告，不影响测试结果。
- 前端开发服务器仅有 Next.js `allowedDevOrigins` 的未来版本提示，不影响生产构建或本次验收。
- 默认前端套件跳过真实 API 用例是设计行为；同一用例已在 `REAL_API_E2E=1` 下独立通过，因此没有未验收功能性跳过。

## 5. 完整对局证据

真实浏览器经创建页建立并完成一局 9 人 FakeProvider 对局：

| 字段 | 值 |
|---|---|
| 对局 ID | `51e928be-b246-41b0-acc9-e5eabc4299b5` |
| 固定种子 | `20260720` |
| 规则版本 | `classic-9-hunter-v1` |
| 终局 | 第 4 天，`FINISHED` |
| 胜方 | `WOLVES` |
| 胜因代码 | `ALL_VILLAGERS_ELIMINATED` |
| 终局存活 | 2 人；1 狼、1 神、0 民 |
| LLM 审计 | 52 次，全部为 `fake / MiniMax-M3 / SUCCEEDED` |
| 浏览器链路 | 创建 → 安全观战 → 开始 → 逐阶段推进 → 终局 → 赛后复盘 |

联调首次运行发现显式 `model=minimaxM3` 会绕过全局 FakeProvider 并等待真实供应商。候选版本已修正为 Fake 模式全模型离线覆盖，并新增三种模型键的 Provider/审计名回归测试；随后真实整局通过。

## 6. A-01 至 A-19 覆盖映射

| 场景 | 直接自动化证据 |
|---|---|
| A-01 | `test_same_seed_deals_identical_classic_lineup_with_exact_role_counts` |
| A-02 | `test_three_wolf_vote_majority_matrix_selects_unique_top_target`、`test_three_wolf_one_one_one_tie_is_deterministic_for_fixed_seed` |
| A-03 | `test_seer_inspecting_hunter_returns_good_alignment` |
| A-04 | `test_wolf_killed_hunter_can_shoot_and_replay_is_idempotent` |
| A-05 | `test_hunter_exile_orders_last_words_death_and_reaction_events` |
| A-06 | `test_witch_poisoned_hunter_never_calls_hunter_agent_or_leaks_publicly` |
| A-07 | `test_poison_suppresses_hunter_reaction_including_wolf_and_poison` 的刀毒参数分支 |
| A-08 | `test_rescued_hunter_stays_alive_and_does_not_trigger` |
| A-09 | `test_last_god_hunter_shoots_last_wolf_and_elimination_wins_first` |
| A-10～A-12 | `test_slaughter_side_win_conditions_and_parity_does_not_end_game` |
| A-13 | `test_classic_runoff_has_candidate_speeches_and_non_candidate_voters`、九人全候选测试 |
| A-14 | `test_second_round_all_eligible_voters_abstain_exiles_nobody` 及再次平票分支 |
| A-15 | 猎人引擎幂等测试与 `test_hunter_reaction_survives_restart_and_replay_is_idempotent` |
| A-16 | `test_hunter_reaction_survives_restart_and_replay_is_idempotent` 的 shoot/pass 参数分支 |
| A-17 | 安全观战 API 脱敏测试及“安全观战查看猎人 Agent 时不请求私有调用或记忆” |
| A-18 | “9 个座位在目标桌面与移动视口内可见且互不遮挡”：320/375/1024/1440，中心命中、逐座 trial pointer 和真实点击导航 |
| A-19 | `test_old_mvp_snapshot_without_new_fields_is_loadable_after_restart` 及“旧 6 人持久化对局可从大厅进入终局并打开复盘” |

## 7. 部署顺序

生产发布必须按以下顺序执行：

1. 生成可追溯提交或制品，记录版本、数据库类型、当前 revision、6/9 人对局数量和事件行数基线。
2. 冻结写入窗口，制作数据库备份，并在隔离环境完成恢复演练；只生成备份文件不算通过。
3. 在备份副本执行 `alembic current`、`alembic heads`、`alembic upgrade head`，确认单一 head、旧 6 人可读取且迁移日志无异常。
4. 在正式数据库先执行迁移，再部署后端；暂不切换 9 人前端入口。
5. 完成健康检查、6 人旧局读取/复盘烟测，以及受控 9 人 FakeProvider 创建和终局烟测。
6. 部署前端，复验安全观战、上帝视角、猎人、PK、响应式布局、阶段进度和复盘。
7. 只有第 9 节所有生产门禁均完成后，才能开放 9 人创建入口并开始监控。

## 8. 回滚策略

旧程序不认识 `HUNTER`、`HUNTER_REACTION`、`DAY_RUNOFF_DISCUSSION` 及新增猎人事件。一旦产生 9 人数据，不能直接把应用回滚到旧程序。

- 尚未产生 9 人数据：关闭入口并停止写入后，可回滚应用；数据库降级仍须先在备份副本验证，优先保留向后兼容的新增列。
- 已产生 9 人数据：关闭新建入口、暂停未完成 9 人局并保留能解析新枚举的后端，优先前滚修复。
- 必须回退时：先备份当前现场，再恢复到发布前整库备份，或使用经审核的数据迁移隔离全部 9 人主记录、事件、行动、调用与记忆；不得只删除主对局行，也不得未经验证直接 `alembic downgrade`。

## 9. 门禁清单与结论

本地候选已完成：

- [x] A-01 至 A-19 具有直接自动化证据。
- [x] 后端全量、前端生产构建和前端 Chromium 回归通过。
- [x] 真实浏览器与后端完成一局 9 人 FakeProvider 对局并打开复盘。
- [x] 旧 6 人快照、默认规则、页面和复盘兼容通过。
- [x] 单一 Alembic head 和全新 SQLite 升级链通过。
- [x] 部署顺序、回滚限制和残余风险已经记录并审查。

生产开放前仍需完成：

- [ ] 生成并记录可追溯 Git 提交或不可变制品标识。
- [ ] 在生产数据库副本完成备份恢复演练并归档日志。
- [ ] 在生产环境完成迁移、6 人旧局烟测和受控 9 人烟测。
- [ ] 验证真实 Provider 密钥、限流、费用、超时告警和监控看板。
- [ ] 完成安全观战、权限、响应式页面的人工抽检，并记录执行人、批准人和时间。
- [ ] 确认可以快速关闭 9 人入口，且值班人员接受“产生 9 人数据后优先前滚”的回滚限制。

最终结论：**开发与本地发布候选验收 GO；生产 9 人创建入口 NO-GO，直至上述 6 项生产门禁全部完成。**

生产发布批准人、执行时间和证据链接：待生产发布时填写。
