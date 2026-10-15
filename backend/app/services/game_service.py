from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.agents import AgentGateway, DiscussionContext, PlayerViewBuilder
from app.agents.providers import LLMMessage, LLMRequest, MessageRole
from app.agents.schemas import (
    AIReviewOutput,
    HunterOutput,
    LastWordsOutput,
    SpeechOutput,
    TargetOutput,
    VoteOutput,
    WitchOutput,
    WolfDiscussionOutput,
    parse_structured,
    split_model_text,
)
from app.domain import (
    EventType,
    Game,
    GameConfig,
    GameEvent,
    GamePhase,
    GameRuleError,
    HunterAction,
    LastWordsAction,
    Role,
    Ruleset,
    SeerAction,
    SpeechAction,
    VoteAction,
    Visibility,
    WitchAction,
    WolfAction,
    WolfDiscussionAction,
)
from app.engine import GameEngine
from app.persistence import Database, EntityNotFoundError, Repository
from app.persistence.models import utc_now


@dataclass(slots=True)
class CommandStatus:
    command_id: str
    status: str
    replayed: bool = False
    result: dict[str, Any] | None = None


class PhaseAdvanceTimeoutError(TimeoutError):
    """单次阶段推进超过配置的总耗时。"""


class PhaseTokenBudgetError(RuntimeError):
    """阶段输出 Token 预算不足，且尚未调用 Provider。"""


class PhaseAgentActionError(RuntimeError):
    """阶段内部分 Agent 行动失败；成功行动已安全检查点。"""


VOTE_TIMEOUT_SECONDS = 200.0


class GameApplicationService:
    """连接确定性规则引擎、独立 Agent 会话与事务持久化。"""

    def __init__(
        self,
        database: Database,
        engine: GameEngine,
        gateway: AgentGateway,
        view_builder: PlayerViewBuilder | None = None,
        gateways: dict[str, AgentGateway] | None = None,
        phase_timeout_seconds: float = 120.0,
        phase_output_token_budget: int = 1000000,
        speech_temperature: float = 0.55,
        vote_temperature: float = 0.35,
    ) -> None:
        if phase_timeout_seconds <= 0:
            raise ValueError("phase_timeout_seconds must be positive")
        if phase_output_token_budget < 1:
            raise ValueError("phase_output_token_budget must be at least 1")
        if not 0 <= speech_temperature <= 2:
            raise ValueError("speech_temperature must be between 0 and 2")
        if not 0 <= vote_temperature <= 2:
            raise ValueError("vote_temperature must be between 0 and 2")
        self.database = database
        self.engine = engine
        self.gateway = gateway
        self.gateways = dict(gateways or {})
        self.view_builder = view_builder or PlayerViewBuilder()
        self.phase_timeout_seconds = phase_timeout_seconds
        self.phase_output_token_budget = phase_output_token_budget
        self.speech_temperature = speech_temperature
        self.vote_temperature = vote_temperature
        self._tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._command_lock = threading.RLock()

    def create_game(
        self,
        *,
        player_names: list[str] | None = None,
        player_count: int | None = None,
        ruleset_version: str = "mvp-v1",
        random_seed: int = 0,
        god_view: bool = False,
        allow_private_inspection: bool = False,
        model_key: str | None = None,
        thinking_enabled: bool = False,
        speech_temperature: float | None = None,
        vote_temperature: float | None = None,
        action_temperature: float = 0.5,
    ) -> Game:
        if ruleset_version == "mvp-v1":
            ruleset = Ruleset.mvp_v1()
        elif ruleset_version == "classic-9-hunter-v1":
            ruleset = Ruleset.classic_9_hunter_v1()
        else:
            raise ValueError(f"不支持的规则版本：{ruleset_version}")

        if player_count is not None and player_count != ruleset.player_count:
            raise ValueError(
                f"规则 {ruleset.version} 要求 {ruleset.player_count} 名玩家，"
                f"收到 {player_count} 名"
            )
        if player_names is not None and len(player_names) != ruleset.player_count:
            raise ValueError(
                f"规则 {ruleset.version} 要求 {ruleset.player_count} 个玩家姓名，"
                f"收到 {len(player_names)} 个"
            )
        if model_key is not None and model_key not in self.gateways:
            available = "、".join(self.gateways) or "无"
            raise ValueError(f"不支持的模型选项：{model_key}；可用选项：{available}")
        effective_speech_temperature = (
            self.speech_temperature
            if speech_temperature is None
            else speech_temperature
        )
        effective_vote_temperature = (
            self.vote_temperature
            if vote_temperature is None
            else vote_temperature
        )
        for field_name, temperature in (
            ("speech_temperature", effective_speech_temperature),
            ("vote_temperature", effective_vote_temperature),
            ("action_temperature", action_temperature),
        ):
            if not 0 <= temperature <= 2:
                raise ValueError(f"{field_name} must be between 0 and 2")
        default_names = [
            "沈砚", "林雾", "周烬", "许昼", "白露", "陆川", "苏禾", "程野", "江澜"
        ]
        names = list(player_names) if player_names is not None else default_names[: ruleset.player_count]
        game_id = str(uuid4())
        result = self.engine.create_game(
            names,
            config=GameConfig(
                random_seed=random_seed,
                ruleset=ruleset,
                god_view=god_view,
                allow_private_inspection=allow_private_inspection,
                model_key=model_key,
                thinking_enabled=thinking_enabled,
                speech_temperature=effective_speech_temperature,
                vote_temperature=effective_vote_temperature,
                action_temperature=action_temperature,
            ),
            game_id=game_id,
            command_id=f"create:{game_id}",
        )
        game = result.game
        personalities = [
            {"style": "沉稳分析", "aggression": 0.35, "logic": 0.9},
            {"style": "直觉敏锐", "aggression": 0.5, "logic": 0.6},
            {"style": "强势进攻", "aggression": 0.9, "logic": 0.65},
            {"style": "谨慎保守", "aggression": 0.2, "logic": 0.75},
            {"style": "善于共情", "aggression": 0.3, "logic": 0.55},
            {"style": "逻辑优先", "aggression": 0.45, "logic": 0.95},
            {"style": "灵活试探", "aggression": 0.55, "logic": 0.7},
            {"style": "果断直率", "aggression": 0.8, "logic": 0.6},
            {"style": "耐心复盘", "aggression": 0.25, "logic": 0.85},
        ]
        for player, personality in zip(game.players, personalities):
            player.personality = dict(personality)
        with self.database.session() as session:
            repo = Repository(session)
            repo.create_game(
                game_id=game.id,
                config=game.config.model_dump(mode="json"),
                state_snapshot=game.model_dump(mode="json"),
                phase=game.phase.value,
                day=game.day,
                rules_version=game.config.ruleset.version,
                random_seed=random_seed,
            )
            for player in game.players:
                repo.add_player(
                    game.id,
                    player_id=player.id,
                    seat=player.seat,
                    name=player.name,
                    personality=player.personality,
                )
            self._append_events(repo, game.id, result.events)
        return game

    def list_games(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            return [self._db_game_summary(item) for item in Repository(session).list_games()]

    def load_game(self, game_id: str) -> Game:
        with self.database.session() as session:
            state = Repository(session).load_game_state(game_id)
        if state is None:
            raise EntityNotFoundError(f"game not found: {game_id}")
        return Game.model_validate(state)

    def load_game_status(self, game_id: str) -> str:
        with self.database.session() as session:
            return Repository(session).require_game(game_id).status

    def load_game_runtime(self, game_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            repo = Repository(session)
            game = repo.require_game(game_id)
            commands = [
                command
                for command in repo.list_phase_commands(game_id)
                if command.command_type != "ai_review"
            ]
            running = [command for command in commands if command.status == "RUNNING"]
            last_command = commands[-1] if commands else None
            return {
                "status": game.status,
                "is_processing": bool(running),
                "active_command_id": running[-1].command_id if running else None,
                "error": last_command.error if last_command and last_command.status == "FAILED" else None,
            }

    async def generate_ai_review(
        self,
        game_id: str,
        *,
        command_id: str,
        model_key: str | None = None,
    ) -> dict[str, Any]:
        """按用户主动选择生成并持久化一次赛后 AI 评价。"""

        game = self.load_game(game_id)
        if game.phase != GamePhase.FINISHED:
            raise GameRuleError("GAME_NOT_FINISHED", "只有已结束的对局可以生成 AI 复盘")

        if model_key is not None:
            try:
                gateway = self.gateways[model_key]
            except KeyError as exc:
                raise GameRuleError(
                    "MODEL_NOT_AVAILABLE",
                    f"AI 复盘模型当前不可用：{model_key}",
                ) from exc
            selected_model_key = model_key
        else:
            gateway = self._gateway_for(game)
            selected_model_key = game.config.model_key or "game-default"

        with self.database.session() as session:
            repo = Repository(session)
            existing = repo.get_phase_command(game_id, command_id)
            if existing is not None:
                if existing.status == "SUCCEEDED" and existing.result_payload is not None:
                    return dict(existing.result_payload)
                if existing.status == "RUNNING":
                    raise GameRuleError("AI_REVIEW_RUNNING", "该 AI 复盘请求仍在处理中")
                raise GameRuleError("AI_REVIEW_FAILED", "该 AI 复盘请求已失败，请重新点击生成")

            command = repo.create_phase_command(
                game_id,
                command_id,
                command_type="ai_review",
                phase=GamePhase.FINISHED.value,
                request_payload={"model": selected_model_key},
            )
            repo.update_phase_command(command, status="RUNNING")

            events = repo.list_events(game_id)
            actions = repo.list_phase_actions(game_id)
            votes = repo.list_votes(game_id)
            gameplay_calls = [
                call for call in repo.list_llm_calls(game_id) if call.call_type != "ai_review"
            ]

        player_call_metrics: dict[str, dict[str, Any]] = {}
        for player in game.players:
            player_calls = [call for call in gameplay_calls if call.player_id == player.id]
            player_call_metrics[player.id] = {
                "call_count": len(player_calls),
                "failed_call_count": sum(call.status == "FAILED" for call in player_calls),
                "call_types": [call.call_type for call in player_calls],
                "total_tokens": sum(
                    int((call.usage or {}).get("total_tokens") or 0) for call in player_calls
                ),
            }

        facts = {
            "game": {
                "ruleset_version": game.config.ruleset.version,
                "day": game.day,
                "winner": game.winner.model_dump(mode="json") if game.winner else None,
            },
            "players": [
                {
                    "id": player.id,
                    "seat": player.seat,
                    "name": player.name,
                    "role": game.role_assignments.get(player.id).value
                    if player.id in game.role_assignments
                    else None,
                    "status": player.status.value,
                    "death_day": player.death_day,
                    "death_causes": [cause.value for cause in player.death_causes],
                    "personality": player.personality,
                    "model_call_metrics": player_call_metrics[player.id],
                }
                for player in sorted(game.players, key=lambda item: item.seat)
            ],
            "events": [
                {
                    "sequence": event.sequence,
                    "day": event.day,
                    "phase": event.phase,
                    "type": event.event_type,
                    "actor_player_id": event.actor_player_id,
                    "visibility": event.visibility,
                    "payload": event.payload,
                }
                for event in events
            ],
            "actions": [
                {
                    "day": action.day,
                    "phase": action.phase,
                    "player_id": action.player_id,
                    "action_type": action.action_type,
                    "payload": action.payload,
                    "is_valid": action.is_valid,
                    "validation_errors": action.validation_errors,
                }
                for action in actions
            ],
            "votes": [
                {
                    "day": vote.day,
                    "round": vote.round,
                    "voter_player_id": vote.voter_player_id,
                    "target_player_id": vote.target_player_id,
                    "abstained": vote.abstained,
                    "public_reason": vote.public_reason,
                    "confidence": vote.confidence,
                }
                for vote in votes
            ],
        }
        player_ids = [player.id for player in sorted(game.players, key=lambda item: item.seat)]
        system_message = LLMMessage(
            role=MessageRole.SYSTEM,
            content=(
                "你是狼人杀赛后教练。只能依据提供的完整赛事实录评价各 Agent，"
                "不得编造未发生的事件。评分要同时考虑身份职责、逻辑与信息利用、"
                "沟通协作和根据新信息调整策略的能力；不能只按胜负或存活与否评分。"
                "最终只返回符合指定结构的 JSON 对象，不要输出 Markdown 或隐藏思维过程。"
            ),
        )
        user_message = LLMMessage(
            role=MessageRole.USER,
            content=(
                "请对每一名参局 Agent 给出评价与评分。evaluations 必须恰好覆盖下面所有 "
                "player_id，各出现一次，不能遗漏或增加：\n"
                f"{json.dumps(player_ids, ensure_ascii=False)}\n"
                "输出结构：\n"
                f"{json.dumps(AIReviewOutput.model_json_schema(), ensure_ascii=False)}\n"
                "赛事实录：\n"
                f"{json.dumps(facts, ensure_ascii=False)}"
            ),
        )
        request = LLMRequest(
            messages=[system_message, user_message],
            model=gateway.model,
            temperature=0.2,
            max_tokens=gateway.max_tokens,
            timeout_seconds=gateway.timeout_seconds,
            thinking_enabled=game.config.thinking_enabled,
            metadata={
                "game_id": game_id,
                "day": game.day,
                "phase": GamePhase.FINISHED.value,
                "call_type": "ai_review",
                "players": facts["players"],
                "ruleset_version": game.config.ruleset.version,
                "model_key": selected_model_key,
            },
        )
        request_snapshot = request.safe_snapshot()
        request_snapshot["ruleset_version"] = game.config.ruleset.version
        with self.database.session() as session:
            repo = Repository(session)
            call = repo.start_llm_call(
                request_id=f"{game_id}:ai-review:{uuid4()}",
                provider=gateway.provider.name,
                model=gateway.model,
                adapter_version=gateway.provider.adapter_version,
                game_id=game_id,
                phase=GamePhase.FINISHED.value,
                call_type="ai_review",
                request_snapshot=request_snapshot,
                day=game.day,
                prompt_version="ai-review-v1",
                rules_version=game.config.ruleset.version,
                schema_version="ai-review-v1",
            )
            call_id = call.id
            for ordinal, message in enumerate(request.messages):
                repo.add_llm_message(
                    call.id,
                    ordinal=ordinal,
                    role=message.role.value,
                    content=message.content,
                )

        started = time.perf_counter()
        try:
            response = await gateway.provider.generate(request)
            reasoning_text, final_text = split_model_text(response.text)
            parsed = parse_structured(final_text, AIReviewOutput)
            returned_ids = [item.player_id for item in parsed.evaluations]
            if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != set(player_ids):
                raise ValueError("AI 复盘结果未恰好覆盖全部参局 Agent")

            generated_at = utc_now().isoformat()
            result = {
                **parsed.model_dump(mode="json"),
                "model_key": selected_model_key,
                "model": gateway.model,
                "provider": gateway.provider.name,
                "generated_at": generated_at,
                "llm_call_id": call_id,
                "usage": response.usage.model_dump(mode="json"),
            }
            with self.database.session() as session:
                repo = Repository(session)
                stored_call = repo.get_llm_call(call_id)
                if stored_call is None:
                    raise EntityNotFoundError(f"llm call not found: {call_id}")
                repo.add_llm_message(
                    stored_call.id,
                    ordinal=len(request.messages),
                    role=MessageRole.ASSISTANT.value,
                    content=final_text,
                )
                repo.complete_llm_call(
                    stored_call,
                    provider_request_id=response.request_id,
                    final_text=final_text,
                    parsed_result=parsed.model_dump(mode="json"),
                    response_snapshot={
                        "finish_reason": response.finish_reason,
                        "safety_status": response.safety_status,
                        "had_reasoning": bool(reasoning_text),
                    },
                    usage=response.usage.model_dump(mode="json"),
                    reasoning_tokens=response.usage.reasoning_tokens,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
                command = repo.get_phase_command(game_id, command_id)
                if command is None:
                    raise EntityNotFoundError(f"command not found: {command_id}")
                repo.update_phase_command(command, status="SUCCEEDED", result_payload=result)
            return result
        except Exception as exc:
            with self.database.session() as session:
                repo = Repository(session)
                stored_call = repo.get_llm_call(call_id)
                if stored_call is not None and stored_call.status == "STARTED":
                    repo.fail_llm_call(
                        stored_call,
                        error_type=getattr(exc, "error_type", type(exc).__name__),
                        error_message=str(exc),
                        latency_ms=round((time.perf_counter() - started) * 1000),
                    )
                command = repo.get_phase_command(game_id, command_id)
                if command is not None:
                    repo.update_phase_command(
                        command,
                        status="FAILED",
                        error={"type": type(exc).__name__, "message": str(exc)},
                    )
            raise

    def start_game(self, game_id: str, command_id: str) -> CommandStatus:
        existing = self._existing_command(game_id, command_id)
        if existing:
            return existing
        game = self.load_game(game_id)
        result = self.engine.start_game(game, command_id=command_id)
        actions: list[dict[str, Any]] = []
        self._persist_engine_result(game_id, result.game, result.events, command_id, "start", actions)
        return CommandStatus(
            command_id,
            "SUCCEEDED",
            result={"game_id": result.game.id, "phase": result.game.phase.value, "day": result.game.day},
        )

    def enqueue_advance(self, game_id: str, command_id: str | None = None) -> CommandStatus:
        with self._command_lock:
            command_id = command_id or str(uuid4())
            existing = self._existing_command(game_id, command_id)
            if existing:
                return existing
            with self.database.session() as session:
                repo = Repository(session)
                db_game = repo.require_game(game_id)
                if db_game.status == "PAUSED":
                    raise GameRuleError("GAME_PAUSED", "游戏已暂停，请先重试或恢复")
                running = repo.list_phase_commands(game_id, status="RUNNING")
                active = []
                for candidate in running:
                    task = self._tasks.get((game_id, candidate.command_id))
                    if task is not None and not task.done():
                        active.append(candidate)
                    else:
                        repo.update_phase_command(
                            candidate,
                            status="FAILED",
                            error={
                                "type": "InterruptedPhaseCommand",
                                "message": "服务重启或任务中断，已允许安全重试",
                            },
                        )
                if active:
                    current = active[-1]
                    return CommandStatus(
                        current.command_id,
                        current.status,
                        replayed=True,
                        result=current.result_payload,
                    )
                game = Game.model_validate(db_game.state_snapshot)
                command = repo.create_phase_command(
                    game_id,
                    command_id,
                    command_type="advance",
                    phase=game.phase.value,
                )
                repo.update_phase_command(command, status="RUNNING")
            task_key = (game_id, command_id)
            task = asyncio.create_task(self._run_advance(game_id, command_id))
            self._tasks[task_key] = task

            def clear_finished(finished: asyncio.Task[None]) -> None:
                with self._command_lock:
                    if self._tasks.get(task_key) is finished:
                        self._tasks.pop(task_key, None)

            task.add_done_callback(clear_finished)
            return CommandStatus(command_id, "RUNNING")

    async def advance_now(self, game_id: str, command_id: str | None = None) -> CommandStatus:
        """测试和 CLI 使用的同步边界；HTTP 默认走 enqueue_advance。"""
        command_id = command_id or str(uuid4())
        existing = self._existing_command(game_id, command_id)
        if existing:
            return existing
        game = self.load_game(game_id)
        with self.database.session() as session:
            repo = Repository(session)
            command = repo.create_phase_command(game_id, command_id, command_type="advance", phase=game.phase.value)
            repo.update_phase_command(command, status="RUNNING")
        await self._run_advance(game_id, command_id)
        return self._existing_command(game_id, command_id) or CommandStatus(command_id, "FAILED")

    async def _run_advance(self, game_id: str, command_id: str) -> None:
        game = self.load_game(game_id)
        timeout_seconds = (
            VOTE_TIMEOUT_SECONDS
            if game.phase == GamePhase.DAY_VOTING
            else self.phase_timeout_seconds
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                await self._execute_advance(game_id, command_id)
        except TimeoutError:
            self._mark_command_failed(
                game_id,
                command_id,
                error={
                    "type": PhaseAdvanceTimeoutError.__name__,
                    "message": f"阶段推进超过 {timeout_seconds:g} 秒总超时",
                    "retryable": True,
                },
            )
        except Exception as exc:
            self._mark_command_failed(
                game_id,
                command_id,
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "retryable": True,
                },
            )

    async def _execute_advance(self, game_id: str, command_id: str) -> None:
        actions: list[dict[str, Any]] = []
        events: list[GameEvent] = []
        with self.database.session() as session:
            db_game = Repository(session).require_game(game_id)
            initial_version = db_game.version
            game = Game.model_validate(db_game.state_snapshot)
        phase_before = game.phase
        game, submitted_events, actions = await self._submit_required_actions(
            game,
            command_id,
            expected_version=initial_version,
        )
        events.extend(submitted_events)
        # 白天发言由主持人逐位触发：本次生成了一条发言时只保存结果，
        # 不在同一条命令里继续调用其他 Agent 或自动结束讨论阶段。
        should_complete_phase = game.phase == phase_before and not (
            phase_before in (GamePhase.DAY_DISCUSSION, GamePhase.DAY_RUNOFF_DISCUSSION)
            and bool(actions)
        )
        if should_complete_phase:
            completed = self.engine.advance(game, command_id=f"{command_id}:complete")
            game = completed.game
            events.extend(completed.events)
        self._persist_engine_result(
            game_id,
            game,
            events,
            command_id,
            "advance",
            actions,
            command_exists=True,
            expected_version=initial_version,
        )

    def _mark_command_failed(
        self, game_id: str, command_id: str, *, error: dict[str, Any]
    ) -> None:
        with self.database.session() as session:
            repo = Repository(session)
            command = repo.get_phase_command(game_id, command_id)
            if command is not None:
                repo.update_phase_command(command, status="FAILED", error=error)

    @staticmethod
    def _eligible_voters(game: Game) -> list[Any]:
        voters = [player for player in game.alive_players() if player.id not in game.votes]
        if (
            game.vote_round > 1
            and game.config.ruleset.runoff_has_discussion
            and game.runoff_candidate_ids
        ):
            voters = [
                player
                for player in voters
                if player.id not in game.runoff_candidate_ids
            ]
        return voters

    @staticmethod
    def _discussion_context(
        game: Game,
        player_id: str,
        action: str,
    ) -> DiscussionContext:
        is_runoff = action == "runoff_speech"
        speakers = [
            player
            for player in game.alive_players()
            if not is_runoff or player.id in game.runoff_candidate_ids
        ]
        speaker_ids = [player.id for player in speakers]
        if player_id not in speaker_ids:
            raise GameRuleError(
                "INVALID_DISCUSSION_SPEAKER",
                f"玩家 {player_id} 不在当前发言序列中",
            )
        speaker_offset = speaker_ids.index(player_id)
        speeches = game.runoff_speeches if is_runoff else game.speeches
        spoken_speaker_ids = [
            speaker_id
            for speaker_id in speaker_ids[:speaker_offset]
            if speaker_id in speeches
        ]
        remaining_speaker_ids = speaker_ids[speaker_offset + 1 :]
        remaining_after_self = len(remaining_speaker_ids)
        is_last_speaker = remaining_after_self == 0
        is_late_speaker = remaining_after_self <= 2
        responsibility = (
            "FINAL_SUMMARY"
            if is_last_speaker
            else "LATE_CONSOLIDATION"
            if is_late_speaker
            else "INDEPENDENT_ANALYSIS"
        )
        return DiscussionContext(
            speaker_ids=speaker_ids,
            spoken_speaker_ids=spoken_speaker_ids,
            remaining_speaker_ids=remaining_speaker_ids,
            speaker_index=speaker_offset + 1,
            speaker_count=len(speaker_ids),
            remaining_after_self=remaining_after_self,
            is_late_speaker=is_late_speaker,
            is_last_speaker=is_last_speaker,
            responsibility=responsibility,
        )

    @staticmethod
    def _discussion_instruction(
        context: DiscussionContext,
        *,
        runoff: bool,
    ) -> str:
        phase_label = "PK 发言" if runoff else "白天公开发言"
        position = (
            f"当前是{phase_label}，你是第 {context.speaker_index}/{context.speaker_count} 位；"
            f"你之前已有 {len(context.spoken_speaker_ids)} 人发言，"
            f"你之后还有 {context.remaining_after_self} 人。"
        )
        vote_semantics = (
            "PK 候选人不参与重投；这里的 vote_intent 表示你公开建议其他存活玩家如何归票，"
            "不是声称自己会实际投票。"
            if runoff
            else "vote_intent 表示你本人当前公开票向。"
        )
        structured_fields = (
            "speech 必须公开表达当下判断；同时显式填写 cited_player_ids、"
            "focus_player_ids、vote_intent、vote_target_id、fallback_target_id 和 "
            "decision_condition。若公开信息中确有关键分歧或矛盾，可以填写 key_conflicts；"
            "没有则省略或填写空数组。"
            + vote_semantics
        )
        if context.is_last_speaker:
            required = min(2, len(context.spoken_speaker_ids))
            return (
                position
                + "你是本轮最后发言位，建议结合完整发言做简要收束；"
                f"可以比较至多 {required} 名最相关的玩家，并在证据足够时明确首选归票目标。"
                "若当前信息不足，可以如实保持未定或选择弃票，不必为了收束而制造矛盾或强行归票。"
                + structured_fields
            )
        if context.is_late_speaker:
            required = min(2, len(context.spoken_speaker_ids))
            return (
                position
                + "你处于后置位，建议结合已有发言做简要收束，并在证据足够时给出初步归票。"
                f"可以比较至多 {required} 名最相关的玩家；若信息不足，允许保持 undecided 或 abstain。"
                + structured_fields
            )
        return (
            position
            + "请做独立分析，不要复述通用模板。已有前位发言时至少引用一名玩家，"
            "给出具体关注对象；如果暂未定票，必须写明形成或改变判断的可验证条件。"
            + structured_fields
        )

    @staticmethod
    def _speech_action(actor_id: str, output: SpeechOutput) -> SpeechAction:
        return SpeechAction(
            actor_id=actor_id,
            reason=output.public_reason,
            content=output.speech,
            private_summary=output.private_strategy,
            cited_player_ids=list(output.cited_player_ids),
            focus_player_ids=list(output.focus_player_ids),
            key_conflicts=list(output.key_conflicts),
            vote_intent=output.vote_intent,
            vote_target_id=output.vote_target_id,
            fallback_target_id=output.fallback_target_id,
            decision_condition=output.decision_condition,
        )

    def _planned_agent_calls(self, game: Game) -> int:
        phase = game.phase
        alive = game.alive_players()
        if phase == GamePhase.NIGHT_WOLF_DISCUSSION:
            return sum(
                game.role_assignments.get(player.id) == Role.WOLF
                and player.id not in game.wolf_discussion
                for player in alive
            )
        if phase == GamePhase.NIGHT_WOLF:
            return sum(
                game.role_assignments.get(player.id) == Role.WOLF
                and player.id not in game.wolf_actions
                for player in alive
            )
        if phase == GamePhase.NIGHT_SEER:
            return int(any(game.role_assignments.get(player.id) == Role.SEER for player in alive))
        if phase == GamePhase.NIGHT_WITCH:
            return int(any(game.role_assignments.get(player.id) == Role.WITCH for player in alive))
        if phase == GamePhase.HUNTER_REACTION:
            return int(game.pending_hunter_action is not None)
        if phase == GamePhase.DAY_DISCUSSION:
            return int(any(player.id not in game.speeches for player in alive))
        if phase == GamePhase.DAY_RUNOFF_DISCUSSION:
            return int(
                any(
                    player.id in game.runoff_candidate_ids
                    and player.id not in game.runoff_speeches
                    for player in alive
                )
            )
        if phase == GamePhase.DAY_VOTING:
            return len(self._eligible_voters(game))
        if phase == GamePhase.DAY_RESOLUTION:
            return int(bool(game.pending_exile_id and not game.last_words_submitted))
        return 0

    def _phase_request_max_tokens(self, game: Game, gateway: AgentGateway) -> int | None:
        planned_calls = self._planned_agent_calls(game)
        if planned_calls == 0:
            return None
        potential_calls = planned_calls * gateway.max_provider_calls
        allocated = self.phase_output_token_budget // potential_calls
        if allocated < 1:
            raise PhaseTokenBudgetError(
                "阶段输出 Token 预算不足："
                f"预算 {self.phase_output_token_budget}，潜在 Provider 调用 {potential_calls}"
            )
        return min(gateway.max_tokens, allocated)

    async def _submit_required_actions(
        self, game: Game, command_id: str, *, expected_version: int | None = None
    ) -> tuple[Game, list[GameEvent], list[dict[str, Any]]]:
        events: list[GameEvent] = []
        stored_actions: list[dict[str, Any]] = []
        # 旧对局可能在规则升级前已进入本不该到达的后续阶段。先让引擎
        # 结算终局，避免继续调用 Agent 生成发言、投票或夜间动作。
        if (
            len(game.role_assignments) == len(game.players)
            and self.engine.check_win(game) is not None
        ):
            return game, events, stored_actions
        phase = game.phase
        gateway = self._gateway_for(game)
        request_max_tokens = self._phase_request_max_tokens(game, gateway)

        if phase == GamePhase.NIGHT_WOLF_DISCUSSION:
            wolves = [
                player
                for player in game.alive_players()
                if game.role_assignments.get(player.id) == Role.WOLF
                and player.id not in game.wolf_discussion
            ]
            target_ids = self._wolf_target_ids(game)
            for player in wolves:
                view = self._build_view(game, player.id, "wolf_discussion", target_ids)
                generated = await gateway.generate_action(
                    view,
                    call_type="wolf_discussion",
                    output_model=WolfDiscussionOutput,
                    instruction=(
                        "与存活狼队友交流今晚的击杀计划。明确提出建议目标和团队可见理由；"
                        "认真参考玩家视图中的狼队交流记录，但不要提交最终刀票。"
                    ),
                    thinking_enabled=game.config.thinking_enabled,
                    temperature=game.config.action_temperature,
                    max_tokens=request_max_tokens,
                )
                output = WolfDiscussionOutput.model_validate(generated.action)
                if output.proposed_target_id not in target_ids:
                    raise GameRuleError("INVALID_MODEL_TARGET", "模型建议了非法狼人目标")
                action = WolfDiscussionAction(
                    actor_id=player.id,
                    reason=output.message,
                    content=output.message,
                    proposed_target_id=output.proposed_target_id,
                )
                result = self.engine.submit_wolf_discussion(
                    game,
                    action,
                    command_id=f"{command_id}:wolf-discussion:{player.id}",
                )
                game = result.game
                events.extend(result.events)
                stored_actions.append(action.model_dump(mode="json"))

        elif phase == GamePhase.NIGHT_WOLF:
            wolves = [p for p in game.alive_players() if game.role_assignments.get(p.id) == Role.WOLF and p.id not in game.wolf_actions]
            for player in wolves:
                target_ids = self._wolf_target_ids(game)
                output = await self._target_action(
                    game,
                    player.id,
                    "wolf_kill",
                    target_ids,
                    "根据完整狼队交流记录，独立提交今晚的最终击杀目标；不要假定能看到队友的最终票。",
                    max_tokens=request_max_tokens,
                )
                action = WolfAction(actor_id=player.id, target_id=output.target_id, reason=output.private_reason or "狼人行动")
                result = self.engine.submit_wolf_action(game, action, command_id=f"{command_id}:wolf:{player.id}")
                game = result.game
                events.extend(result.events)
                stored_actions.append(action.model_dump(mode="json"))

        elif phase == GamePhase.NIGHT_SEER:
            seer = next((p for p in game.alive_players() if game.role_assignments.get(p.id) == Role.SEER), None)
            if seer:
                targets = [p.id for p in game.alive_players() if p.id != seer.id]
                output = await self._target_action(
                    game,
                    seer.id,
                    "seer_check",
                    targets,
                    "选择今晚要查验的玩家",
                    max_tokens=request_max_tokens,
                )
                action = SeerAction(actor_id=seer.id, target_id=output.target_id, reason=output.private_reason or "预言家查验")
                result = self.engine.submit_seer_action(game, action, command_id=f"{command_id}:seer:{seer.id}")
                game = result.game
                events.extend(result.events)
                stored_actions.append(action.model_dump(mode="json"))

        elif phase == GamePhase.NIGHT_WITCH:
            witch = next((p for p in game.alive_players() if game.role_assignments.get(p.id) == Role.WITCH), None)
            if witch:
                targets = [p.id for p in game.alive_players()]
                view = self._build_view(game, witch.id, "witch_action", targets)
                generated = await gateway.generate_action(
                    view,
                    call_type="witch_action",
                    output_model=WitchOutput,
                    instruction="决定使用解药、毒药或跳过；同夜不能使用两瓶药。",
                    thinking_enabled=game.config.thinking_enabled,
                    temperature=game.config.action_temperature,
                    max_tokens=request_max_tokens,
                )
                output = WitchOutput.model_validate(generated.action)
                action = WitchAction(
                    actor_id=witch.id,
                    reason=output.private_reason or "女巫行动",
                    rescue_target_id=game.wolf_target_id if output.action == "save" else None,
                    poison_target_id=output.target_id if output.action == "poison" else None,
                )
                result = self.engine.submit_witch_action(game, action, command_id=f"{command_id}:witch:{witch.id}")
                game = result.game
                events.extend(result.events)
                stored_actions.append(action.model_dump(mode="json"))
        elif phase == GamePhase.HUNTER_REACTION:
            pending = game.pending_hunter_action
            if pending is None:
                raise GameRuleError(
                    "MISSING_HUNTER_ACTION", "猎人反应阶段缺少待处理动作"
                )

            hunter_id = pending.hunter_id
            targets = [
                player.id
                for player in game.alive_players()
                if player.id != hunter_id
            ]
            view = self._build_view(game, hunter_id, "hunter_action", targets)
            generated = await gateway.generate_action(
                view,
                call_type="hunter_action",
                output_model=HunterOutput,
                instruction=(
                    "这是最后且唯一一次开枪窗口：shoot 和 pass 都会永久消耗枪权，不存在保留到后续。"
                    "忽略会话历史中在遗言或其他旧任务里提前形成的开枪或放弃意向，"
                    "必须根据最新公开发言、票型和死亡信息重新评估并排序合法目标。"
                    "只要存在基于公开信息的合理可疑目标，就优先选择 shoot，不要求已经确认其狼人身份；"
                    "优先关注带头归票、直接投票、协同跟票、立场突变和逻辑矛盾者。"
                    "只有完全无法形成有意义的嫌疑排序，或所有合法目标都有充分理由判断为好人时，才选择 pass；"
                    "公开理由不得泄露玩家视图中的私有触发信息。"
                ),
                thinking_enabled=game.config.thinking_enabled,
                temperature=game.config.action_temperature,
                max_tokens=request_max_tokens,
            )
            output = HunterOutput.model_validate(generated.action)
            if output.action == "shoot" and output.target_id not in targets:
                raise GameRuleError("INVALID_MODEL_TARGET", "模型选择了非法猎人开枪目标")
            action = HunterAction(
                actor_id=hunter_id,
                reason=output.public_reason,
                action=output.action,
                target_id=output.target_id,
            )
            result = self.engine.submit_hunter_action(
                game, action, command_id=f"{command_id}:hunter:{hunter_id}"
            )
            game = result.game
            events.extend(result.events)
            stored_actions.append(action.model_dump(mode="json"))

        elif phase == GamePhase.DAY_DISCUSSION:
            player = next(
                (player for player in game.alive_players() if player.id not in game.speeches),
                None,
            )
            if player is not None:
                targets = [
                    candidate.id
                    for candidate in game.alive_players()
                    if candidate.id != player.id
                ]
                view = self._build_view(game, player.id, "speech", targets)
                context = view.discussion_context
                assert context is not None
                generated = await gateway.generate_action(
                    view,
                    call_type="speech",
                    output_model=SpeechOutput,
                    instruction=self._discussion_instruction(context, runoff=False),
                    thinking_enabled=game.config.thinking_enabled,
                    max_tokens=request_max_tokens,
                    temperature=game.config.speech_temperature,
                )
                output = SpeechOutput.model_validate(generated.action)
                action = self._speech_action(player.id, output)
                result = self.engine.submit_speech(
                    game,
                    action,
                    command_id=f"{command_id}:speech:{player.id}",
                )
                game = result.game
                events.extend(result.events)
                stored_actions.append(action.model_dump(mode="json"))

        elif phase == GamePhase.DAY_RUNOFF_DISCUSSION:
            candidates = [
                player
                for player in game.alive_players()
                if player.id in game.runoff_candidate_ids
            ]
            player = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.id not in game.runoff_speeches
                ),
                None,
            )
            if player is not None:
                targets = [candidate.id for candidate in candidates if candidate.id != player.id]
                view = self._build_view(game, player.id, "runoff_speech", targets)
                context = view.discussion_context
                assert context is not None
                generated = await gateway.generate_action(
                    view,
                    call_type="runoff_speech",
                    output_model=SpeechOutput,
                    instruction=self._discussion_instruction(context, runoff=True),
                    thinking_enabled=game.config.thinking_enabled,
                    max_tokens=request_max_tokens,
                    temperature=game.config.speech_temperature,
                )
                output = SpeechOutput.model_validate(generated.action)
                action = self._speech_action(player.id, output)
                result = self.engine.submit_speech(
                    game,
                    action,
                    command_id=f"{command_id}:runoff-speech:{player.id}",
                )
                game = result.game
                events.extend(result.events)
                stored_actions.append(action.model_dump(mode="json"))

        elif phase == GamePhase.DAY_VOTING:
            voters = self._eligible_voters(game)
            frozen = game.model_copy(deep=True)

            async def make_vote(player_id: str) -> tuple[str, VoteOutput]:
                candidates = game.runoff_candidate_ids or [p.id for p in frozen.alive_players()]
                candidates = [candidate for candidate in candidates if candidate != player_id] or list(candidates)
                view = self._build_view(frozen, player_id, "vote", candidates)
                generated = await gateway.generate_action(
                    view,
                    call_type="vote",
                    output_model=VoteOutput,
                    instruction=(
                        "可以从合法候选人中投票，也可以选择弃票，并给出公开理由。"
                        "投票时 action 为 vote 且 target_id 必须是合法目标；"
                        "弃票时 action 为 abstain 且 target_id 必须为 null。"
                        "投票前必须重新评估全部公开发言，尤其是自己发言后的后置位新证据；"
                        "此前的暂定票向不构成约束，应根据当前证据独立决定并允许修正旧判断。"
                        "先在 private_reason 中比较当前选择的阵营长期收益、短期收益和身份暴露风险，"
                        "再给出不泄露任何私有信息的 public_reason。"
                    ),
                    # 投票是最终阵营决策，始终启用独立推理，不继承房间的思考开关。
                    thinking_enabled=True,
                    max_tokens=request_max_tokens,
                    temperature=game.config.vote_temperature,
                    timeout_seconds=VOTE_TIMEOUT_SECONDS,
                )
                return player_id, VoteOutput.model_validate(generated.action)

            vote_results = await asyncio.gather(
                *(make_vote(player.id) for player in voters),
                return_exceptions=True,
            )
            generated_votes: list[tuple[str, VoteOutput]] = []
            failures: list[tuple[str, BaseException]] = []
            for player, outcome in zip(voters, vote_results):
                if isinstance(outcome, BaseException):
                    failures.append((player.id, outcome))
                else:
                    generated_votes.append(outcome)

            seat = {player.id: player.seat for player in game.players}
            for player_id, output in sorted(generated_votes, key=lambda item: seat[item[0]]):
                action = VoteAction(
                    actor_id=player_id,
                    target_id=output.target_id,
                    abstain=output.action == "abstain",
                    reason=output.public_reason,
                    confidence=output.confidence,
                )
                result = self.engine.submit_vote(
                    game,
                    action,
                    command_id=f"{command_id}:vote:{game.vote_round}:{player_id}",
                )
                game = result.game
                events.extend(result.events)
                stored_actions.append(
                    action.model_dump(mode="json")
                    | {"vote_round": game.vote_round, "action_day": game.day}
                )

            if failures:
                if stored_actions and expected_version is not None:
                    self._persist_partial_checkpoint(
                        game.id,
                        game,
                        events,
                        command_id,
                        stored_actions,
                        expected_version=expected_version,
                    )
                failed_players = ", ".join(
                    f"{player_id}({type(error).__name__})"
                    for player_id, error in failures
                )
                raise PhaseAgentActionError(f"投票 Agent 失败：{failed_players}")

        elif phase == GamePhase.DAY_RESOLUTION and game.pending_exile_id and not game.last_words_submitted:
            player_id = game.pending_exile_id
            view = self._build_view(game, player_id, "last_words", [])
            last_words_instruction = "发表最后一轮遗言，给出当前公开结论，不要声称还有后续发言机会。"
            if game.role_assignments.get(player_id) == Role.HUNTER:
                last_words_instruction += (
                    "当前任务只是遗言，不得在 private_strategy 或其他字段中提前决定、承诺或暗示"
                    "稍后的猎人开枪或放弃；若规则随后进入 hunter_action，必须留到该阶段根据最新公开票型重新决策。"
                )
            generated = await gateway.generate_action(
                view,
                call_type="last_words",
                output_model=LastWordsOutput,
                instruction=last_words_instruction,
                thinking_enabled=game.config.thinking_enabled,
                temperature=game.config.speech_temperature,
                max_tokens=request_max_tokens,
            )
            output = LastWordsOutput.model_validate(generated.action)
            action = LastWordsAction(actor_id=player_id, reason=output.public_reason, content=output.speech)
            result = self.engine.submit_last_words(game, action, command_id=f"{command_id}:last-words:{player_id}")
            game = result.game
            events.extend(result.events)
            stored_actions.append(action.model_dump(mode="json"))

        return game, events, stored_actions

    async def _target_action(
        self,
        game: Game,
        player_id: str,
        call_type: str,
        targets: list[str],
        instruction: str,
        *,
        max_tokens: int | None = None,
    ) -> TargetOutput:
        view = self._build_view(game, player_id, call_type, targets)
        generated = await self._gateway_for(game).generate_action(
            view,
            call_type=call_type,
            output_model=TargetOutput,
            instruction=instruction,
            thinking_enabled=game.config.thinking_enabled,
            temperature=game.config.action_temperature,
            max_tokens=max_tokens,
        )
        output = TargetOutput.model_validate(generated.action)
        if output.target_id not in targets:
            raise GameRuleError("INVALID_MODEL_TARGET", "模型选择了非法目标")
        return output

    @staticmethod
    def _wolf_target_ids(game: Game) -> list[str]:
        return [
            player.id
            for player in game.alive_players()
            if game.config.ruleset.wolves_can_target_wolves
            or game.role_assignments.get(player.id) != Role.WOLF
        ]

    def _gateway_for(self, game: Game) -> AgentGateway:
        if game.config.model_key is None:
            return self.gateway
        try:
            return self.gateways[game.config.model_key]
        except KeyError as exc:
            raise GameRuleError(
                "MODEL_NOT_AVAILABLE",
                f"对局配置的模型当前不可用：{game.config.model_key}",
            ) from exc

    @staticmethod
    def _current_discussion_round(game: Game) -> tuple[int, str] | None:
        """返回当前需要保留完整原话的公开讨论轮次。"""

        if game.phase == GamePhase.DAY_DISCUSSION:
            return game.day, GamePhase.DAY_DISCUSSION.value
        if game.phase == GamePhase.DAY_RUNOFF_DISCUSSION:
            return game.day, GamePhase.DAY_RUNOFF_DISCUSSION.value
        if game.phase == GamePhase.DAY_VOTING:
            phase = (
                GamePhase.DAY_RUNOFF_DISCUSSION
                if game.runoff_speeches
                else GamePhase.DAY_DISCUSSION
            )
            return game.day, phase.value
        return None

    @staticmethod
    def _layer_discussion_events(
        public_events: list[dict[str, Any]],
        *,
        current_round: tuple[int, str] | None,
    ) -> list[dict[str, Any]]:
        """当前轮次保留原话，历史轮次用已有公开理由做归因式摘要。"""

        layered: list[dict[str, Any]] = []
        summary_indexes: dict[tuple[int, str], int] = {}
        for event in public_events:
            if str(event.get("type") or "").upper() != EventType.SPEECH.value:
                layered.append(event)
                continue

            round_key = (int(event.get("day") or 0), str(event.get("phase") or ""))
            if round_key == current_round:
                layered.append(
                    {
                        key: event[key]
                        for key in (
                            "sequence",
                            "type",
                            "day",
                            "phase",
                            "player_id",
                            "content",
                            "vote_intent",
                            "vote_target_id",
                            "fallback_target_id",
                        )
                        if key in event
                    }
                )
                continue

            summary_index = summary_indexes.get(round_key)
            if summary_index is None:
                summary_index = len(layered)
                summary_indexes[round_key] = summary_index
                layered.append(
                    {
                        "sequence": event.get("sequence"),
                        "type": "DISCUSSION_ROUND_SUMMARY",
                        "day": round_key[0],
                        "phase": round_key[1],
                        "epistemic_status": "UNVERIFIED_PLAYER_SUMMARIES",
                        "summary_items": [],
                    }
                )

            summary_text = str(event.get("reason") or "").strip()
            if not summary_text:
                content = str(event.get("content") or event.get("text") or "").strip()
                summary_text = content[:300] + ("……" if len(content) > 300 else "")
            summary_item = {
                "event_sequence": event.get("sequence"),
                "player_id": event.get("player_id"),
                "statement_summary": summary_text,
                "vote_intent": event.get("vote_intent"),
                "vote_target_id": event.get("vote_target_id"),
            }
            if event.get("fallback_target_id") is not None:
                summary_item["fallback_target_id"] = event.get("fallback_target_id")
            layered[summary_index]["summary_items"].append(summary_item)
            layered[summary_index]["sequence"] = event.get("sequence")

        return layered

    def _build_view(self, game: Game, player_id: str, action: str, targets: list[str]):
        state = game.model_dump(mode="json")
        state["ruleset"] = state.get("config", {}).get("ruleset", {})
        state["witch_visible_victim"] = game.wolf_target_id
        if game.role_assignments.get(player_id) == Role.WOLF:
            state["wolf_team_history"] = [
                {
                    "sequence": event.sequence,
                    "day": event.day,
                    "round": event.payload.get("round", 1),
                    "actor_id": event.payload.get("actor_id"),
                    "content": event.payload.get("content"),
                    "proposed_target_id": event.payload.get("proposed_target_id"),
                }
                for event in game.events
                if event.type == EventType.WOLF_DISCUSSION_MESSAGE
                and player_id in event.audience_player_ids
            ]
        public_events = []
        public_payload_keys = {
            "text",
            "content",
            "reason",
            "target_id",
            "hunter_id",
            "candidate_ids",
            "cited_player_ids",
            "focus_player_ids",
            "key_conflicts",
            "vote_intent",
            "vote_target_id",
            "fallback_target_id",
            "decision_condition",
            "round_summary",
            # 投票全部收齐后，VOTES_REVEALED 会公开以下结构化结果。
            "round",
            "votes",
            "counts",
            "abstain",
            "abstain_count",
        }
        for event in game.events:
            if event.visibility != Visibility.PUBLIC:
                continue
            item = {
                "sequence": event.sequence,
                "type": event.type.value,
                "day": event.day,
                "phase": event.phase.value,
                "player_id": event.payload.get("player_id") or event.payload.get("actor_id") or event.payload.get("hunter_id"),
                **{
                    key: value
                    for key, value in event.payload.items()
                    if key in public_payload_keys
                },
            }
            public_events.append(item)
        public_events = self._layer_discussion_events(
            public_events,
            current_round=self._current_discussion_round(game),
        )
        return self.view_builder.build(
            state,
            player_id,
            public_events=public_events,
            private_memory={},
            allowed_action=action,
            legal_targets=targets,
            discussion_context=(
                self._discussion_context(game, player_id, action)
                if action in {"speech", "runoff_speech"}
                else None
            ),
        )

    def pause_game(self, game_id: str) -> None:
        with self.database.session() as session:
            game = Repository(session).require_game(game_id)
            if game.status == "FINISHED" or game.phase == GamePhase.FINISHED.value:
                raise GameRuleError("GAME_FINISHED", "已结束游戏不能暂停")
            game.status = "PAUSED"

    def resume_game(self, game_id: str) -> None:
        with self.database.session() as session:
            game = Repository(session).require_game(game_id)
            game.status = "ACTIVE"

    def command_status(self, game_id: str, command_id: str) -> CommandStatus | None:
        return self._existing_command(game_id, command_id)

    def _existing_command(self, game_id: str, command_id: str) -> CommandStatus | None:
        with self.database.session() as session:
            command = Repository(session).get_phase_command(game_id, command_id)
            if command is None:
                return None
            return CommandStatus(command_id, command.status, replayed=True, result=command.result_payload or command.error)

    def _persist_partial_checkpoint(
        self,
        game_id: str,
        game: Game,
        events: list[GameEvent],
        command_id: str,
        actions: list[dict[str, Any]],
        *,
        expected_version: int,
    ) -> None:
        """保存成功玩家的部分行动，但保持阶段命令为 RUNNING。"""
        with self.database.session() as session:
            repo = Repository(session)
            db_game = repo.require_game(game_id, for_update=True)
            command = repo.get_phase_command(game_id, command_id)
            if command is None:
                raise EntityNotFoundError(f"phase command not found: {command_id}")
            repo.update_game_state(
                game_id,
                game.model_dump(mode="json"),
                phase=game.phase.value,
                day=game.day,
                expected_version=expected_version,
            )
            self._append_events(repo, game_id, events)
            self._persist_actions(
                repo,
                game_id,
                game,
                actions,
                phase=command.phase,
                phase_command_id=command.id,
            )

    def _persist_engine_result(
        self,
        game_id: str,
        game: Game,
        events: list[GameEvent],
        command_id: str,
        command_type: str,
        actions: list[dict[str, Any]],
        *,
        command_exists: bool = False,
        expected_version: int | None = None,
    ) -> None:
        with self.database.session() as session:
            repo = Repository(session)
            db_game = repo.require_game(game_id, for_update=True)
            command = repo.get_phase_command(game_id, command_id)
            if command is None:
                command = repo.create_phase_command(
                    game_id,
                    command_id,
                    command_type=command_type,
                    phase=db_game.phase,
                )
                repo.update_phase_command(command, status="RUNNING")
            repo.update_game_state(
                game_id,
                game.model_dump(mode="json"),
                phase=game.phase.value,
                # 普通结算不写运行状态，避免覆盖并发 pause；结束时才显式终结。
                status="FINISHED" if game.phase == GamePhase.FINISHED else None,
                day=game.day,
                expected_version=expected_version if expected_version is not None else db_game.version,
            )
            self._append_events(repo, game_id, events)
            self._persist_actions(
                repo,
                game_id,
                game,
                actions,
                phase=command.phase,
                phase_command_id=command.id,
            )
            if command_type == "start":
                existing = {assignment.player_id for assignment in repo.list_role_assignments(game_id)}
                for player_id, role in game.role_assignments.items():
                    if player_id not in existing:
                        private_state = game.role_states.get(player_id)
                        repo.assign_role(
                            game_id,
                            player_id,
                            role.value,
                            private_state=private_state.model_dump(mode="json") if private_state else {},
                        )
                        repo.get_or_create_agent_session(game_id, player_id)
            repo.update_phase_command(
                command,
                status="SUCCEEDED",
                result_payload={"game_id": game.id, "phase": game.phase.value, "day": game.day},
            )

    @staticmethod
    def _persist_actions(
        repo: Repository,
        game_id: str,
        game: Game,
        actions: list[dict[str, Any]],
        *,
        phase: str,
        phase_command_id: str,
    ) -> None:
        for raw_action in actions:
            actor_id = raw_action.get("actor_id")
            action_type = str(raw_action.get("action_type", "unknown"))
            action_day = int(raw_action.get("action_day", game.day))
            action_payload = {
                key: value for key, value in raw_action.items() if key != "action_day"
            }
            row = repo.add_phase_action(
                game_id,
                phase=phase,
                action_type=action_type,
                payload=action_payload,
                day=action_day,
                phase_command_id=phase_command_id,
                player_id=actor_id,
            )
            if action_type == "vote":
                repo.add_vote(
                    game_id,
                    voter_player_id=actor_id,
                    target_player_id=raw_action.get("target_id"),
                    abstained=bool(raw_action.get("abstain")),
                    day=max(1, action_day),
                    public_reason=raw_action["reason"],
                    round=int(raw_action.get("vote_round", game.vote_round)),
                    phase_action_id=row.id,
                    confidence=raw_action.get("confidence"),
                )

    @staticmethod
    def _append_events(repo: Repository, game_id: str, events: list[GameEvent]) -> None:
        for event in events:
            payload = dict(event.payload)
            if event.audience_player_ids:
                payload["audience_player_ids"] = list(event.audience_player_ids)
            repo.append_event(
                game_id,
                event.type.value,
                payload,
                phase=event.phase.value,
                day=event.day,
                visibility=event.visibility.value,
                actor_player_id=event.payload.get("player_id") or event.payload.get("actor_id"),
            )

    @staticmethod
    def _db_game_summary(game: Any) -> dict[str, Any]:
        snapshot = game.state_snapshot or {}
        snapshot_players = snapshot.get("players") or []
        players = [
            {
                "id": item.get("id"),
                "seat": item.get("seat"),
                "name": item.get("name"),
                "alive": item.get("status", "ALIVE") == "ALIVE",
                "is_alive": item.get("status", "ALIVE") == "ALIVE",
            }
            for item in snapshot_players
        ]
        ruleset = (snapshot.get("config") or {}).get("ruleset") or {}
        player_count = (
            len(snapshot_players)
            or ruleset.get("player_count")
            or len(game.players)
        )
        alive_player_count = sum(1 for player in players if player["alive"])
        if not snapshot_players:
            alive_player_count = sum(1 for player in game.players if player.alive)
        return {
            "id": game.id,
            "status": game.status,
            "phase": game.phase,
            "day": game.day,
            "rules_version": game.rules_version,
            "random_seed": game.random_seed,
            "ruleset_version": game.rules_version,
            "player_count": player_count,
            "alive_player_count": alive_player_count,
            "name": "狼人杀对局",
            "players": players,
            "winner": snapshot.get("winner"),
            "created_at": game.created_at.isoformat(),
            "updated_at": game.updated_at.isoformat(),
        }
