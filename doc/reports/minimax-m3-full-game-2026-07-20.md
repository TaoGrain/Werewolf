# 真实 MiniMax-M3 狼人杀完整对局测试报告

- 测试日期：2026-07-20（Asia/Shanghai）
- 项目目录：`C:\Users\123\Desktop\Werewolf`
- 测试性质：真实外部模型调用、六个独立玩家 Agent、从创建游戏连续推进至终局
- 最终运行数据库：`backend/minimax-full-game-run4.db`
- 游戏 ID：`8daabde1-4d35-4433-adc0-66ac60bde73f`
- 随机种子：`20260720`

## 结论

测试通过。系统使用真实 MiniMax-M3，从角色分配后连续运行至 `FINISHED`，最终在第 1 天处决结算后由狼人阵营获胜，胜因是存活狼人人数大于或等于存活好人人数。

完整运行耗时 524,825 ms（约 8 分 44.8 秒）。期间白天讨论阶段有 1 次结构化输出失败，使用新的命令 ID 自动重试后成功，游戏状态未被失败尝试污染。最终 6 个 Agent 会话隔离检查、服务端玩家视图检查、API 密钥与内部推理内容检查全部通过。

## 真实调用配置

| 项目 | 值 |
| --- | --- |
| Provider | `minimax` |
| 模型 | `MiniMax-M3` |
| 适配器版本 | `minimax-m3-v1` |
| API 协议 | OpenAI Chat Completions 兼容接口 |
| Endpoint | `https://api.minimaxi.com/v1/chat/completions` |
| 玩家 Agent 数 | 6 |
| 阶段最大重试次数 | 3 |
| 单次结构化输出内部修复次数 | 2 |

本次密钥属于中国区，因此国际站地址会返回 `invalid api key`，切换到中国区兼容接口后鉴权成功。MiniMax 官方文档给出的中国区 OpenAI 兼容地址同样是 `https://api.minimaxi.com/v1/chat/completions`，并使用 Bearer 鉴权：[文本对话（OpenAI API 兼容）](https://platform.minimaxi.com/docs/api-reference/text-chat-openai)。

## 完整对局阶段记录

| 序号 | 阶段 | 尝试 | 结果 | 耗时 |
| ---: | --- | ---: | --- | ---: |
| 0 | `ROLE_ASSIGNED → NIGHT_WOLF` | 1 | 成功 | 40 ms |
| 1 | `NIGHT_WOLF → NIGHT_SEER` | 1 | 成功 | 68,920 ms |
| 2 | `NIGHT_SEER → NIGHT_WITCH` | 1 | 成功 | 21,679 ms |
| 3 | `NIGHT_WITCH → NIGHT_RESOLUTION` | 1 | 成功 | 27,399 ms |
| 4 | `NIGHT_RESOLUTION → DAY_ANNOUNCEMENT` | 1 | 成功 | 31 ms |
| 5 | `DAY_ANNOUNCEMENT → DAY_DISCUSSION` | 1 | 成功 | 29 ms |
| 6 | `DAY_DISCUSSION` | 1 | 失败，未提交状态 | 192,999 ms |
| 6 | `DAY_DISCUSSION → DAY_VOTING` | 2 | 自动重试成功 | 112,039 ms |
| 7 | `DAY_VOTING → DAY_RESOLUTION` | 1 | 成功 | 61,017 ms |
| 8 | `DAY_RESOLUTION → FINISHED` | 1 | 成功 | 40,360 ms |

阶段命令共尝试 10 次：9 次成功，1 次失败后恢复。事件共持久化 41 条，玩家记忆快照共持久化 19 条。

## LLM 调用统计

| 指标 | 结果 |
| --- | ---: |
| LLM 调用记录 | 36 |
| 成功 | 19 |
| 失败并进入修复/重试 | 17 |
| 输入 Token | 108,541 |
| 输出 Token | 10,361 |
| 其中推理 Token（仅计数，不保存内容） | 2,188 |
| 总 Token | 118,902 |
| 平均延迟 | 28,645.47 ms |
| P95 延迟 | 55,038 ms |
| 最大延迟 | 64,687 ms |

调用类型分布：`wolf_kill` 2 次、`seer_check` 1 次、`witch_action` 1 次、`speech` 11 次、`vote` 5 次、`repair` 16 次。

17 条失败记录均为 `structured_output_error`，且 Provider 的 `finish_reason` 均为 `length`；19 条成功记录的 `finish_reason` 均为 `stop`。这说明主要问题不是网络稳定性，而是初始请求的 800 Token 输出上限对推理型模型偏紧，部分 JSON 在生成结束前被截断。内部修复调用通常能用更短输出完成纠正。MiniMax 官方兼容接口文档也说明，当返回原因为 `length` 时应调高生成上限，当前文档允许的 `max_completion_tokens` 上限为 2048：[接口参数说明](https://platform.minimaxi.com/docs/api-reference/text-chat-openai)。

## 会话隔离与敏感数据审计

| 审计项 | 结果 |
| --- | --- |
| 已检查 LLM 请求快照 | 36 |
| 独立会话数 | 6 |
| 玩家 ID 与专属视图不一致 | 0 |
| 专属视图出现服务端全量角色/夜间行动等禁用字段 | 0 |
| 持久化快照出现 API 密钥材料 | 未发现 |
| 持久化快照出现 `reasoning_content`、`thinking` 或思维链字段 | 未发现 |
| 综合结果 | 通过 |

报告没有复制模型原始发言、私有策略或内部推理文本。真实调用数据库仅保留在本地，并受项目 `.gitignore` 的 `*.db` 规则保护，不会被默认提交。

## 测试过程中发现并处理的问题

1. **SOCKS 代理依赖缺失**：运行环境配置了 SOCKS 代理，`httpx` 首次调用缺少 `socksio`。已在后端依赖中加入 `socksio>=1.0,<2`。
2. **API 区域不匹配**：国际站接口对当前中国区密钥返回 `invalid api key`。改用官方中国区 OpenAI 兼容接口后成功。
3. **非法目标未进入结构化修复**：一次真实返回把玩家 UUID 多写了一位。网关现会把输出 Schema 与精确合法目标列表放入提示，并在解析后验证 `target_id` 必须完全匹配合法值。
4. **规则异常构造错误**：非法模型目标分支曾少传 `GameRuleError` 的错误码。已改为 `INVALID_MODEL_TARGET` 加中文错误信息。
5. **真实返回的截断率偏高**：完整局虽能依靠修复完成，但 17/36 条调用先因 `length` 截断。建议后续将 MiniMax 请求上限从 800 调高到 1600～2048，并重新跑性能/成本对比。

## 回归验证

真实对局完成后执行后端全量测试：

```text
32 passed, 1 warning in 10.59s
```

唯一警告来自 FastAPI `TestClient` 对旧版 `httpx` 集成方式的弃用提示，不影响本次对局逻辑、持久化或 MiniMax 调用结果。

## 总体判断

当前版本已具备真实 MiniMax 驱动完整对局的能力，并能在模型结构化输出异常时恢复。MVP 的游戏状态机、真实 Provider 接入、六玩家会话隔离、持久化以及敏感数据过滤均在本次端到端测试中得到验证。

上线或开展批量对局前，优先建议把 MiniMax 输出上限参数化并调高，再统计修复率、平均耗时和 Token 成本；这将是当前最直接的稳定性与成本优化点。
