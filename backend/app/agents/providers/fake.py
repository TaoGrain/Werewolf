from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable

from .base import LLMRequest, LLMResponse, LLMUsage, ModelCapabilities


class FakeProvider:
    """可脚本化、无网络的 Provider；默认根据 call_type 返回稳定行动。"""

    name = "fake"
    adapter_version = "fake-v1"

    def __init__(self, responses: Iterable[str | dict] | None = None) -> None:
        self._responses = deque(
            json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item
            for item in (responses or [])
        )
        self.requests: list[LLMRequest] = []

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(native_structured_output=False)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self._responses:
            text = self._responses.popleft()
        else:
            text = json.dumps(self._default_payload(request), ensure_ascii=False)
        return LLMResponse(
            text=text,
            request_id=f"fake-{len(self.requests)}",
            finish_reason="stop",
            usage=LLMUsage(input_tokens=10, output_tokens=10, total_tokens=20),
        )

    @staticmethod
    def _default_payload(request: LLMRequest) -> dict:
        meta = request.metadata
        raw_call_type = str(meta.get("call_type") or "speech")
        call_type = (
            str(meta.get("original_call_type") or raw_call_type)
            if raw_call_type == "repair"
            else raw_call_type
        )
        legal_targets = [str(item) for item in (meta.get("legal_targets") or [])]
        target = legal_targets[0] if legal_targets else None
        if call_type == "last_words":
            player_id = str(meta.get("player_id") or "当前玩家")
            return {
                "speech": (
                    f"这是 {player_id} 的最后陈述：请核对我此前的公开判断，"
                    "并结合已经形成的票型继续追查矛盾。"
                ),
                "public_reason": "留下可供存活玩家复核的公开结论。",
                "private_strategy": "遗言不再假设自己仍有后续行动。",
            }
        if call_type in {"speech", "runoff_speech"}:
            raw_context = meta.get("discussion_context") or {}
            context = raw_context if isinstance(raw_context, dict) else {}
            player_id = str(meta.get("player_id") or "当前玩家")
            speaker_index = int(context.get("speaker_index") or 1)
            spoken_ids = [str(item) for item in context.get("spoken_speaker_ids") or []]
            cited_ids = spoken_ids[-2:]
            is_late = bool(context.get("is_late_speaker"))
            is_last = bool(context.get("is_last_speaker"))
            lenses = (
                "结论有没有对应的公开依据",
                "怀疑对象是否随着信息变化而合理调整",
                "对强弱发言的评价标准是否前后一致",
                "身份判断与预期票型是否能够相互印证",
                "关键指控是否给出了可复核的原话",
                "站边关系是否解释了场上的主要分歧",
                "防守内容是否正面回答了质疑",
                "归票方向是否符合此前表达的判断",
                "总结是否覆盖了本轮最关键的冲突",
            )
            lens = lenses[(speaker_index - 1) % len(lenses)]
            speech_parts = [
                f"我是 {player_id}，当前处在第 {speaker_index} 发言位；我重点核对{lens}。"
            ]
            if cited_ids:
                speech_parts.append(
                    f"本轮已发言的 {'、'.join(cited_ids)} 给出了可比较的信息，"
                    "我会把他们的理由与结论逐项对应，而不是只复述态度。"
                )
            if target is not None:
                speech_parts.append(
                    f"目前我把 {target} 列为首要核验对象，因为其公开立场最需要回应上述问题。"
                )
            if is_last and target is not None:
                speech_parts.append(
                    f"我是最后发言位，建议重点核验 {target}；"
                    f"当前依据是‘{lens}’，若证据仍不足，我保留未定票。"
                )
            elif is_late and target is not None:
                speech_parts.append(
                    f"作为后置位，我建议重点核验 {target}；若其现有解释无法闭合，"
                    f"第 {speaker_index} 位将以‘{lens}’作为形成票向的公开依据。"
                )
            elif target is not None:
                speech_parts.append(
                    f"我当前不锁死票，但若 {target} 不能用公开事实解释这一处问题，"
                    f"第 {speaker_index} 位会依据‘{lens}’决定是否把票投给 {target}。"
                )
            else:
                speech_parts.append("当前没有合法票选目标，我明确选择弃票。")

            return {
                "speech": "".join(speech_parts),
                "public_reason": f"第 {speaker_index} 位基于公开发言形成当下判断。",
                "private_strategy": "持续比较公开口径、票向与身份收益。",
                "cited_player_ids": cited_ids,
                "focus_player_ids": [target] if target is not None else [],
                "key_conflicts": [],
                "vote_intent": "abstain" if target is None else "undecided",
                "vote_target_id": None,
                "fallback_target_id": None,
                "decision_condition": (
                    f"只要 {target} 对该问题的公开解释仍不完整，就维持当前判断。"
                    if target is not None
                    else "合法目标列表为空，因此本轮维持弃票。"
                ),
            }
        if call_type == "vote":
            return {
                "target_id": target,
                "public_reason": "其公开发言中的判断依据较弱。",
                "private_reason": "已重新评估后置位公开信息以及本票的长期收益和暴露风险。",
                "confidence": 0.6,
            }
        if call_type == "wolf_discussion":
            return {
                "message": "我建议选择当前最有威胁的目标，队友可以结合场上信息补充。",
                "proposed_target_id": target,
            }
        if call_type in {"wolf_kill", "seer_check"}:
            return {"target_id": target, "private_reason": "固定 FakeProvider 策略。"}
        if call_type == "witch_action":
            return {"action": "pass", "target_id": None, "private_reason": "本轮保留药物。"}
        if call_type == "hunter_action":
            if target is None:
                return {
                    "action": "pass", "target_id": None, "public_reason": "当前没有合法开枪目标。"
                }
            return {
                "action": "shoot", "target_id": target, "public_reason": "选择当前首个合法目标开枪。"
            }
        if call_type == "ai_review":
            players = meta.get("players") or []
            evaluations = []
            for index, player in enumerate(players):
                player_id = str(player.get("id") if isinstance(player, dict) else player)
                score = 72 + (index % 5)
                evaluations.append(
                    {
                        "player_id": player_id,
                        "overall_score": score,
                        "dimensions": {
                            "role_performance": 7,
                            "reasoning": 7,
                            "communication": 7,
                            "adaptability": 7,
                        },
                        "summary": "能够围绕公开事实完成本局职责，整体决策较为稳定。",
                        "strengths": ["行动与公开立场基本一致"],
                        "improvements": ["可以更早说明判断发生变化的触发依据"],
                        "decisive_moment": "终局前的关键行动体现了其主要策略。",
                    }
                )
            return {
                "overall_summary": "本局胜负由信息利用、阵营协作与关键轮次决策共同决定。",
                "evaluations": evaluations,
            }
        if call_type == "repair":
            return {"error": "no scripted repair response"}
        return {"action": "pass", "target_id": target}
