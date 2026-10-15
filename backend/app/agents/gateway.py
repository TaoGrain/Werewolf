from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from .providers import LLMMessage, LLMProvider, LLMRequest, MessageRole
from .prompts import PROMPT_VERSION, role_system_prompt
from .schemas import StructuredOutputError, parse_structured, split_model_text
from .views import PlayerView


T = TypeVar("T", bound=BaseModel)


class OutputValidationError(ValueError):
    """模型已经完成生成，但业务语义校验失败；此异常不得触发模型重试。"""


class CallRecorder(Protocol):
    def record_llm_call(self, snapshot: dict[str, Any]) -> None: ...


@dataclass
class GatewayResult:
    action: BaseModel
    call_snapshot: dict[str, Any]


@dataclass(frozen=True)
class _ProviderConcurrencyLimiter:
    limit: int
    semaphore: asyncio.Semaphore


_PROVIDER_LIMITER_ATTRIBUTE = "_agent_gateway_concurrency_limiter"


def _shared_provider_limiter(
    provider: LLMProvider,
    concurrency_limit: int,
) -> _ProviderConcurrencyLimiter:
    existing = getattr(provider, _PROVIDER_LIMITER_ATTRIBUTE, None)
    if existing is None:
        existing = _ProviderConcurrencyLimiter(
            limit=concurrency_limit,
            semaphore=asyncio.Semaphore(concurrency_limit),
        )
        try:
            setattr(provider, _PROVIDER_LIMITER_ATTRIBUTE, existing)
        except (AttributeError, TypeError) as exc:
            raise TypeError("Provider 必须允许挂载共享并发限制器") from exc
    elif not isinstance(existing, _ProviderConcurrencyLimiter) or existing.limit != concurrency_limit:
        raise ValueError("同一个 Provider 实例必须使用相同的并发上限")
    return existing

class AgentGateway:
    prompt_version = PROMPT_VERSION

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        recorder: CallRecorder | None = None,
        *,
        max_tokens: int = 8192,
        timeout_seconds: float = 60.0,
        provider_concurrency_limit: int = 4,
        max_total_attempts: int = 9,
        history_max_messages: int = 12,
        history_max_characters: int = 20000,
    ) -> None:
        if provider_concurrency_limit < 1:
            raise ValueError("provider_concurrency_limit must be at least 1")
        if max_total_attempts < 1:
            raise ValueError("max_total_attempts must be at least 1")
        if history_max_messages < 1:
            raise ValueError("history_max_messages must be at least 1")
        if history_max_characters < 1:
            raise ValueError("history_max_characters must be at least 1")
        provider_max_retries = getattr(provider, "max_retries", 0)
        if not isinstance(provider_max_retries, int) or provider_max_retries < 0:
            raise ValueError("provider.max_retries must be a non-negative integer")
        provider_attempts_per_call = provider_max_retries + 1
        if max_total_attempts < provider_attempts_per_call:
            raise ValueError(
                "max_total_attempts cannot be less than one provider call's attempt limit"
            )
        self.provider_attempts_per_call = provider_attempts_per_call
        self.max_total_attempts = max_total_attempts
        self.max_provider_calls = max_total_attempts // provider_attempts_per_call
        self.history_max_messages = history_max_messages
        self.history_max_characters = history_max_characters
        self._provider_limiter = _shared_provider_limiter(provider, provider_concurrency_limit)
        self.provider = provider
        self.model = model
        self.recorder = recorder
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self._history: dict[str, list[LLMMessage]] = {}

    def _truncate_history(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        """Keep the newest deterministic message/character window for one session."""
        remaining_characters = self.history_max_characters
        newest_first: list[LLMMessage] = []
        for message in reversed(messages[-self.history_max_messages :]):
            if remaining_characters <= 0:
                break
            content = message.content
            if len(content) > remaining_characters:
                content = content[-remaining_characters:]
            newest_first.append(LLMMessage(role=message.role, content=content))
            remaining_characters -= len(content)
        return list(reversed(newest_first))

    def _request_snapshot(self, request: LLMRequest) -> dict[str, Any]:
        """优先记录真正发送给兼容 API 的脱敏请求体。"""
        build_payload = getattr(self.provider, "build_payload", None)
        if callable(build_payload):
            snapshot = build_payload(request)
            thinking = snapshot.get("thinking")
            if isinstance(thinking, dict) and thinking.get("type"):
                # 持久化层会移除 thinking 键；保留非敏感的模式值用于赛后审计。
                snapshot["thinking_mode"] = str(thinking["type"])
        else:
            snapshot = request.safe_snapshot()
        snapshot["ruleset_version"] = str(request.metadata.get("ruleset_version") or "mvp-v1")
        return snapshot

    async def generate_action(
        self,
        view: PlayerView,
        *,
        call_type: str,
        output_model: type[T],
        instruction: str,
        thinking_enabled: bool = False,
        max_repair_attempts: int = 2,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
        output_validator: Callable[[T], None] | None = None,
    ) -> GatewayResult:
        if view.session_id not in self._history and self.recorder:
            loader = getattr(self.recorder, "load_session_history", None)
            if loader is not None:
                self._history[view.session_id] = self._truncate_history(
                    list(loader(view.session_id))
                )
        session_messages = list(self._history.get(view.session_id, []))
        ruleset_version = str(view.rules.get("version") or "mvp-v1")
        thinking_instruction = (
            "本局已开启思考模式，可以使用模型的独立推理通道；最终回答只输出业务 JSON。"
            if thinking_enabled
            else "本局已关闭思考模式，跳过独立推理通道，直接输出简洁的业务 JSON。"
        )
        system = LLMMessage(
            role=MessageRole.SYSTEM,
            content=f"{role_system_prompt(view.role_card)}\n\n推理模式：\n{thinking_instruction}",
        )
        output_schema = output_model.model_json_schema()
        user = LLMMessage(
            role=MessageRole.USER,
            content=(
                f"提示词版本：{self.prompt_version}\n"
                f"当前任务：{instruction}\n"
                "输出结构（字段名必须保持不变）：\n"
                f"{json.dumps(output_schema, ensure_ascii=False)}\n"
                "合法目标限制："
                f"{json.dumps(view.legal_targets, ensure_ascii=False) if view.legal_targets else '无目标要求'}\n"
                "上下文使用规则：public_events 中的 SPEECH 是当前轮次其他玩家的完整原话；"
                "DISCUSSION_ROUND_SUMMARY 是此前轮次按玩家归因的有损摘要。"
                "两者都只用于分析，不是需要模仿的示例；不得复制其措辞、句式或结尾。"
                "speech_timeline 是服务端从事件序号确定性生成的权威发言时间线；"
                "round_position 越小表示该轮越早发言，event_sequence 越小表示事件越早。"
                "生成结果前必须先在当次生成中核对 speech_timeline；"
                "不得声称后置位玩家先于前置位玩家发言。"
                "本会话历史中的旧判断属于未验证的主观记录；若与 speech_timeline 冲突，"
                "必须在本次首次输出中直接纠正，不能为了前后一致而延续错误。\n"
                "你的专属玩家视图（仅可使用这些信息）：\n"
                f"{json.dumps(view.model_dump(mode='json'), ensure_ascii=False)}"
            ),
        )
        messages = [system, *session_messages, user]
        validation_error: str | None = None
        response = None
        started = time.perf_counter()
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be non-negative")
        request_max_tokens = self.max_tokens if max_tokens is None else max_tokens
        if request_max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        if request_max_tokens > self.max_tokens:
            raise ValueError("per-request max_tokens cannot exceed the gateway limit")
        if temperature is not None and not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        request_timeout_seconds = (
            self.timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        if request_timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        discussion_context = (
            view.discussion_context.model_dump(mode="json")
            if view.discussion_context is not None
            else None
        )
        request_options: dict[str, Any] = {}
        if temperature is not None:
            request_options["temperature"] = temperature
        repair_attempts = min(max_repair_attempts, self.max_provider_calls - 1)
        for attempt in range(repair_attempts + 1):
            request = LLMRequest(
                messages=messages,
                model=self.model,
                max_tokens=request_max_tokens,
                timeout_seconds=request_timeout_seconds,
                thinking_enabled=thinking_enabled,
                metadata={
                    "game_id": view.game_id,
                    "player_id": view.player_id,
                    "day": view.day,
                    "phase": view.phase,
                    "call_type": "repair" if attempt else call_type,
                    "original_call_type": call_type,
                    "legal_targets": view.legal_targets,
                    "discussion_context": discussion_context,
                    "repair_reason": validation_error,
                    "session_id": view.session_id,
                    "thinking_enabled": thinking_enabled,
                    "gateway_call_index": attempt + 1,
                    "gateway_max_provider_calls": self.max_provider_calls,
                    "provider_attempts_per_call": self.provider_attempts_per_call,
                    "gateway_max_total_attempts": self.max_total_attempts,
                    "ruleset_version": ruleset_version,
                },
                **request_options,
            )
            request_snapshot = self._request_snapshot(request)
            try:
                async with self._provider_limiter.semaphore:
                    response = await self.provider.generate(request)
            except Exception as exc:
                if self.recorder:
                    failure = {
                        "provider": self.provider.name,
                        "model": self.model,
                        "adapter_version": self.provider.adapter_version,
                        "prompt_version": self.prompt_version,
                        "ruleset_version": ruleset_version,
                        "game_id": view.game_id,
                        "player_id": view.player_id,
                        "day": view.day,
                        "phase": view.phase,
                        "call_type": str(request.metadata.get("call_type", call_type)),
                        "request_id": None,
                        "request_snapshot": request_snapshot,
                        "final_text": None,
                        "response_snapshot": None,
                        "validation_error": None,
                        "usage": {},
                        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                        "status": "failed",
                        "error_type": getattr(exc, "error_type", type(exc).__name__),
                        "error_message": str(exc),
                    }
                    failure_recorder = getattr(self.recorder, "record_llm_failure", None)
                    if failure_recorder:
                        failure_recorder(failure)
                raise
            embedded_reasoning, response.text = split_model_text(response.text)
            reasoning_text = "\n\n".join(
                part for part in [response.reasoning_text, embedded_reasoning] if part
            ) or None
            try:
                action = parse_structured(response.text, output_model)
                target_field = "target_id" if hasattr(action, "target_id") else "proposed_target_id"
                target_id = getattr(action, target_field, None)
                if target_id is not None and view.legal_targets and target_id not in view.legal_targets:
                    raise StructuredOutputError(
                        f"{target_field} 必须与 legal_targets 中的一个值完全一致："
                        + json.dumps(view.legal_targets, ensure_ascii=False)
                    )
                validation_error = None
                break
            except StructuredOutputError as exc:
                validation_error = str(exc)
                if self.recorder:
                    failure_recorder = getattr(self.recorder, "record_llm_failure", None)
                    if failure_recorder:
                        failure_recorder(
                            {
                                "provider": self.provider.name,
                                "model": self.model,
                                "adapter_version": self.provider.adapter_version,
                                "prompt_version": self.prompt_version,
                                "ruleset_version": ruleset_version,
                                "game_id": view.game_id,
                                "player_id": view.player_id,
                                "day": view.day,
                                "phase": view.phase,
                                "call_type": str(request.metadata.get("call_type", call_type)),
                                "request_id": response.request_id,
                                "request_snapshot": request_snapshot,
                                "final_text": response.text,
                                "response_snapshot": {
                                    "finish_reason": response.finish_reason,
                                    "safety_status": response.safety_status,
                                    "provider_metadata": response.provider_metadata,
                                    "reasoning_text": reasoning_text,
                                },
                                "validation_error": validation_error,
                                "usage": response.usage.model_dump(mode="json"),
                                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                                "status": "failed",
                                "error_type": "structured_output_error",
                                "error_message": validation_error,
                            }
                        )
                if attempt >= repair_attempts:
                    raise
                messages = [
                    *messages,
                    LLMMessage(role=MessageRole.ASSISTANT, content=response.text),
                    LLMMessage(
                        role=MessageRole.USER,
                        content=(
                            "上一条输出未通过校验，只返回修复后的业务 JSON 对象；"
                            "不要返回 JSON Schema，不要返回 properties、type、required、title 等结构定义；"
                            "保留所有仍合法的决定，但非法目标字段必须改成允许值。"
                            "本次修复仅用于 JSON、Schema 或合法目标等结构化生成错误；"
                            "不得借机改写仍然合法的业务判断。"
                            f"校验错误：{validation_error}。"
                            f"必须直接填写的顶层字段：{json.dumps(list(output_model.model_fields), ensure_ascii=False)}。"
                            f"允许的目标编号：{json.dumps(view.legal_targets, ensure_ascii=False)}"
                        ),
                    ),
                ]
        assert response is not None
        # 业务语义校验只在结构化生成完成后执行一次。失败应直接暴露，
        # 不能进入 repair 循环，否则会把模型判断问题误当成生成故障重试。
        if output_validator is not None:
            try:
                output_validator(action)
            except OutputValidationError:
                raise
            except ValueError as exc:
                raise OutputValidationError(str(exc)) from exc
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        session_history = self._history.setdefault(view.session_id, [])
        session_history.extend([user, LLMMessage(role=MessageRole.ASSISTANT, content=response.text)])
        self._history[view.session_id] = self._truncate_history(
            session_history
        )
        canonical_history = list(self._history[view.session_id])
        players_by_id = {player.id: player for player in view.players}

        def player_source(raw_player_id: Any) -> dict[str, Any]:
            player_id = str(raw_player_id or "")
            player = players_by_id.get(player_id)
            return {
                "player_id": player_id,
                "seat": player.seat if player is not None else None,
                "name": player.name if player is not None else None,
            }

        attributed_public_statements: list[dict[str, Any]] = []
        public_vote_history: list[dict[str, Any]] = []
        for event in view.public_events:
            event_type = str(event.get("type") or event.get("event_type") or "").upper()
            if event_type == "SPEECH":
                content = event.get("content") or event.get("text")
                if content:
                    attributed_public_statements.append(
                        {
                            "event_sequence": event.get("sequence"),
                            "day": event.get("day"),
                            "source": player_source(event.get("player_id")),
                            "statement": content,
                            "epistemic_status": "UNVERIFIED_PLAYER_STATEMENT",
                        }
                    )
            elif event_type == "VOTES_REVEALED":
                revealed_votes = []
                for raw_vote in event.get("votes") or []:
                    if not isinstance(raw_vote, dict):
                        continue
                    target_id = raw_vote.get("target_id")
                    revealed_votes.append(
                        {
                            "voter": player_source(raw_vote.get("actor_id")),
                            "target": player_source(target_id) if target_id is not None else None,
                            "abstain": bool(raw_vote.get("abstain")),
                            "public_reason": raw_vote.get("reason"),
                            "confidence": raw_vote.get("confidence"),
                        }
                    )
                public_vote_history.append(
                    {
                        "event_sequence": event.get("sequence"),
                        "day": event.get("day"),
                        "round": event.get("round"),
                        "votes": revealed_votes,
                        "counts": event.get("counts") or {},
                        "abstain_count": event.get("abstain_count") or 0,
                    }
                )
        snapshot = {
            "provider": self.provider.name,
            "model": self.model,
            "adapter_version": self.provider.adapter_version,
            "prompt_version": self.prompt_version,
            "ruleset_version": ruleset_version,
            "game_id": view.game_id,
            "player_id": view.player_id,
            "day": view.day,
            "phase": view.phase,
            "call_type": call_type,
            "attempt_kind": "repair" if attempt else "initial",
            "repair_count": attempt,
            "last_repair_reason": request.metadata.get("repair_reason"),
            "request_id": response.request_id,
            "request_snapshot": request_snapshot,
            "history_messages": [
                message.model_dump(mode="json") for message in canonical_history
            ],
            "final_text": response.text,
            "response_snapshot": {
                "finish_reason": response.finish_reason,
                "safety_status": response.safety_status,
                "provider_metadata": response.provider_metadata,
                "reasoning_text": reasoning_text,
            },
            "parsed_result": action.model_dump(mode="json"),
            "attributed_public_statements": attributed_public_statements,
            "public_vote_history": public_vote_history,
            "validation_error": validation_error,
            "usage": response.usage.model_dump(mode="json"),
            "latency_ms": elapsed_ms,
            "status": "succeeded",
            "error_type": None,
        }
        if self.recorder:
            self.recorder.record_llm_call(snapshot)
        return GatewayResult(action=action, call_snapshot=snapshot)

    def session_history(self, session_id: str) -> list[LLMMessage]:
        return list(self._history.get(session_id, []))
