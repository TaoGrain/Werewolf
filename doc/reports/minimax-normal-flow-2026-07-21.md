# MiniMax M3 正常流程真实运行报告

> 这是修复前的故障现场报告。修复和完整复测结果见 `minimax-structured-output-fix-2026-07-21.md`。

- 测试日期：2026-07-21
- 测试方式：通过真实 Next.js 页面创建、开始和逐阶段推进
- Provider：`minimax`
- 模型：`MiniMax-M3`
- Endpoint：项目默认 `https://api.minimax.io/v1/text/chatcompletion_v2`
- 规则：项目默认 `mvp-v1`
- 随机种子：表单默认 `20260720`
- 观察模式：表单默认安全观战
- 数据库：`backend/minimax-normal-flow-20260721.db`
- 对局 ID：`76370f90-9c21-46cf-baea-a4ae7fb9d1d7`

## 结论

**未通过，真实流程阻塞。**

本次没有使用 FakeProvider、没有注入角色行动、没有修改规则、提示词、Token 上限或模型配置。对局正常完成了首夜狼人、预言家、女巫、夜间结算和天亮公布，但第一天白天发言阶段连续 3 次失败，达到项目现有完整对局流程的阶段重试上限，因此停止在 `DAY_DISCUSSION`。

阻塞原因是 MiniMax 多次返回 `finish_reason=length`，最终回答区没有完整行动 JSON；部分修复调用又返回了 JSON Schema 本身，而非符合 Schema 的行动对象。

## 正常流程执行记录

| 顺序 | 阶段 | 尝试 | 结果 |
| ---: | --- | ---: | --- |
| 1 | 创建对局 | 1 | 成功 |
| 2 | 开始游戏 | 1 | 成功 |
| 3 | `ROLE_ASSIGNED → NIGHT_WOLF` | 1 | 成功 |
| 4 | `NIGHT_WOLF` | 1 | 失败：无 JSON 对象 |
| 5 | `NIGHT_WOLF` | 2 | 失败：无 JSON 对象 |
| 6 | `NIGHT_WOLF → NIGHT_SEER` | 3 | 成功 |
| 7 | `NIGHT_SEER → NIGHT_WITCH` | 1 | 成功 |
| 8 | `NIGHT_WITCH` | 1 | 失败：缺少 `action` 字段 |
| 9 | `NIGHT_WITCH → NIGHT_RESOLUTION` | 2 | 成功 |
| 10 | `NIGHT_RESOLUTION → DAY_ANNOUNCEMENT` | 1 | 成功 |
| 11 | `DAY_ANNOUNCEMENT → DAY_DISCUSSION` | 1 | 成功 |
| 12 | `DAY_DISCUSSION` | 1 | 失败：缺少 `speech/public_reason` |
| 13 | `DAY_DISCUSSION` | 2 | 失败：不完整 JSON |
| 14 | `DAY_DISCUSSION` | 3 | 失败：无 JSON 对象，达到上限 |

数据库中共有 13 条阶段命令：7 条成功、6 条失败。失败阶段的游戏状态没有被提交，最终状态保持为：

- 状态：`ACTIVE`
- 天数：第 1 天
- 阶段：`DAY_DISCUSSION`
- 持久化事件：24 条
- Agent 记忆快照：13 条
- 从创建到停止约 6 分 26 秒

## 真实 Agent 行动复盘

本轮身份分配：

| 座位 | 身份 |
| ---: | --- |
| 1 号沈砚 | 狼人 |
| 2 号林雾 | 女巫 |
| 3 号周烬 | 平民 |
| 4 号许昼 | 平民 |
| 5 号白露 | 预言家 |
| 6 号陆川 | 狼人 |

首夜由 MiniMax 自主产生的行动：

1. 1 号狼人沈砚选择击杀 5 号白露。
2. 6 号狼人陆川选择击杀 4 号许昼。
3. 两狼目标分歧，规则按固定种子裁定最终刀口为 5 号白露。
4. 5 号预言家白露查验 1 号沈砚，结果为狼人。
5. 2 号女巫林雾选择 `pass`，未使用解药或毒药。其真实理由是首夜缺少身份信息，担心解药救狼或毒药误伤，因此保留双药。
6. 夜间结算后，5 号白露死亡。

这次女巫不救人是 MiniMax 的真实行动选择，不是测试代码预设。

## LLM 调用统计

| 指标 | 数值 |
| --- | ---: |
| 调用记录总数 | 40 |
| 成功 | 13 |
| 失败 | 27 |
| `structured_output_error` | 27 |
| `finish_reason=length` | 21 |
| `finish_reason=stop` | 19 |
| 输入 Token | 53,053 |
| 输出 Token | 4,719 |
| 推理 Token | 1,583 |
| 总 Token | 57,772 |
| 平均延迟 | 16,216.23 ms |
| 最大延迟 | 36,131 ms |

调用类型：

- `wolf_kill`：6
- `seer_check`：1
- `witch_action`：2
- `speech`：10
- `repair`：21

## 根因

项目当前每次模型请求默认最多输出 800 Token，定义位于 `backend/app/agents/providers/base.py`。MiniMax M3 的推理内容多次耗尽该额度，返回 `finish_reason=length`，而最终 JSON 内容为空或不完整。

网关虽然最多进行两次内部格式修复，但本轮大量修复调用仍然被截断，或错误地返回了输出结构定义。第一天讨论需要多个存活 Agent 顺序发言，任一 Agent 在内部修复后仍失败，整个阶段事务就无法提交，因此连续 3 次阶段重试仍不能完成。

## 隔离与页面检查

- 检查了 40 条模型请求快照。
- 6 个 Agent 使用 6 个独立会话。
- 玩家 ID 与专属视图不一致：0。
- 专属视图包含其他玩家服务端私密状态：0。
- 当前页面正确显示“白天发言”“阶段执行失败”“模型输出中没有 JSON 对象”。
- 浏览器没有业务错误；仅有一条 Next.js 未来版本的 `scroll-behavior` 兼容性警告。

## 后续处理边界

本轮严格使用项目现有默认配置，达到既定 3 次阶段尝试上限后停止。没有擅自提高输出 Token、切换 Endpoint、修改提示词、跳过失败 Agent 或替角色生成行动。

如果要让正常流程继续，需要先由项目负责人明确选择配置或代码调整方案，再重新创建一局完整测试；不能把当前未完成对局报告为通过。
