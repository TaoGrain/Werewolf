"""面向应用层的 Repository 与幂等阶段命令服务。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from .database import Database
from .models import (
    AgentMemorySnapshot,
    AgentSession,
    Game,
    GameEvent,
    LLMCall,
    LLMMessage,
    PhaseAction,
    PhaseCommand,
    Player,
    RoleAssignment,
    Vote,
    new_uuid,
    utc_now,
)


class EntityNotFoundError(LookupError):
    pass


class StaleGameVersionError(RuntimeError):
    pass


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "reasoning_content",
        "thinking",
        "chain_of_thought",
    }
)


def sanitize_for_storage(value: Any) -> Any:
    """递归移除不得持久化的鉴权和内部推理字段。"""

    if isinstance(value, Mapping):
        return {
            str(key): sanitize_for_storage(item)
            for key, item in value.items()
            if str(key).lower() not in _SENSITIVE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_for_storage(item) for item in value]
    return value


class Repository:
    """单事务 Repository 门面；不会自行 commit。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_game(
        self,
        *,
        config: Mapping[str, Any],
        state_snapshot: Mapping[str, Any] | None = None,
        phase: str = "LOBBY",
        status: str = "ACTIVE",
        day: int = 0,
        rules_version: str = "mvp-v1",
        random_seed: int | None = None,
        game_id: str | None = None,
    ) -> Game:
        game = Game(
            id=game_id or new_uuid(),
            config=sanitize_for_storage(config),
            state_snapshot=sanitize_for_storage(state_snapshot or {}),
            phase=phase,
            status=status,
            day=day,
            rules_version=rules_version,
            random_seed=random_seed,
        )
        self.session.add(game)
        self.session.flush()
        return game

    def get_game(self, game_id: str, *, for_update: bool = False) -> Game | None:
        statement = select(Game).where(Game.id == game_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def require_game(self, game_id: str, *, for_update: bool = False) -> Game:
        game = self.get_game(game_id, for_update=for_update)
        if game is None:
            raise EntityNotFoundError(f"game not found: {game_id}")
        return game

    def list_games(self, *, status: str | None = None) -> list[Game]:
        statement = select(Game)
        if status is not None:
            statement = statement.where(Game.status == status)
        return list(self.session.scalars(statement.order_by(Game.created_at.desc())))

    def load_game_state(self, game_id: str) -> dict[str, Any] | None:
        game = self.get_game(game_id)
        if game is None:
            return None
        return sanitize_for_storage(game.state_snapshot)

    def update_game_state(
        self,
        game_id: str,
        state_snapshot: Mapping[str, Any],
        *,
        phase: str | None = None,
        status: str | None = None,
        day: int | None = None,
        expected_version: int | None = None,
    ) -> Game:
        values: dict[str, Any] = {
            "state_snapshot": sanitize_for_storage(state_snapshot),
            "version": Game.version + 1,
            "updated_at": utc_now(),
        }
        if phase is not None:
            values["phase"] = phase
        if status is not None:
            values["status"] = status
        if day is not None:
            values["day"] = day

        statement = update(Game).where(Game.id == game_id)
        if expected_version is not None:
            statement = statement.where(Game.version == expected_version)
        result = self.session.execute(statement.values(**values))
        if result.rowcount == 0:  # type: ignore[union-attr]
            if self.get_game(game_id) is None:
                raise EntityNotFoundError(f"game not found: {game_id}")
            raise StaleGameVersionError(f"stale game version: {game_id}")
        self.session.flush()
        return self.require_game(game_id)

    def add_player(
        self,
        game_id: str,
        *,
        seat: int,
        name: str,
        personality: Mapping[str, Any] | None = None,
        player_id: str | None = None,
    ) -> Player:
        player = Player(
            id=player_id or new_uuid(),
            game_id=game_id,
            seat=seat,
            name=name,
            personality=sanitize_for_storage(personality or {}),
        )
        self.session.add(player)
        self.session.flush()
        return player

    def add_players(self, game_id: str, players: Iterable[Mapping[str, Any]]) -> list[Player]:
        return [self.add_player(game_id, **dict(player)) for player in players]

    def list_players(self, game_id: str) -> list[Player]:
        return list(
            self.session.scalars(
                select(Player).where(Player.game_id == game_id).order_by(Player.seat)
            )
        )

    def assign_role(
        self,
        game_id: str,
        player_id: str,
        role: str,
        *,
        private_state: Mapping[str, Any] | None = None,
    ) -> RoleAssignment:
        assignment = RoleAssignment(
            game_id=game_id,
            player_id=player_id,
            role=role,
            private_state=sanitize_for_storage(private_state or {}),
        )
        self.session.add(assignment)
        self.session.flush()
        return assignment

    def list_role_assignments(self, game_id: str) -> list[RoleAssignment]:
        return list(
            self.session.scalars(
                select(RoleAssignment).where(RoleAssignment.game_id == game_id)
            )
        )

    def append_event(
        self,
        game_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        phase: str,
        day: int = 0,
        visibility: str = "PUBLIC",
        actor_player_id: str | None = None,
        llm_call_id: str | None = None,
    ) -> GameEvent:
        game = self.require_game(game_id, for_update=True)
        sequence = game.next_event_sequence
        game.next_event_sequence += 1
        event = GameEvent(
            game_id=game_id,
            sequence=sequence,
            event_type=event_type,
            payload=sanitize_for_storage(payload),
            rules_version=game.rules_version,
            phase=phase,
            day=day,
            visibility=visibility,
            actor_player_id=actor_player_id,
            llm_call_id=llm_call_id,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def list_events(
        self,
        game_id: str,
        *,
        after_sequence: int = 0,
        visibility: str | None = None,
    ) -> list[GameEvent]:
        statement = select(GameEvent).where(
            GameEvent.game_id == game_id, GameEvent.sequence > after_sequence
        )
        if visibility is not None:
            statement = statement.where(GameEvent.visibility == visibility)
        return list(self.session.scalars(statement.order_by(GameEvent.sequence)))

    def get_phase_command(self, game_id: str, command_id: str) -> PhaseCommand | None:
        return self.session.scalar(
            select(PhaseCommand).where(
                PhaseCommand.game_id == game_id, PhaseCommand.command_id == command_id
            )
        )

    def list_phase_commands(
        self, game_id: str, *, status: str | None = None
    ) -> list[PhaseCommand]:
        statement = select(PhaseCommand).where(PhaseCommand.game_id == game_id)
        if status is not None:
            statement = statement.where(PhaseCommand.status == status)
        return list(self.session.scalars(statement.order_by(PhaseCommand.created_at, PhaseCommand.id)))

    def create_phase_command(
        self,
        game_id: str,
        command_id: str,
        *,
        command_type: str,
        phase: str,
        request_payload: Mapping[str, Any] | None = None,
    ) -> PhaseCommand:
        command = PhaseCommand(
            game_id=game_id,
            command_id=command_id,
            command_type=command_type,
            phase=phase,
            request_payload=sanitize_for_storage(request_payload or {}),
            status="PENDING",
        )
        self.session.add(command)
        self.session.flush()
        return command

    def update_phase_command(
        self,
        command: PhaseCommand,
        *,
        status: str,
        result_payload: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> PhaseCommand:
        command.status = status
        command.result_payload = sanitize_for_storage(result_payload) if result_payload is not None else None
        command.error = sanitize_for_storage(error) if error is not None else None
        if status == "RUNNING":
            command.started_at = utc_now()
        if status in {"SUCCEEDED", "FAILED"}:
            command.finished_at = utc_now()
        self.session.flush()
        return command

    def add_phase_action(
        self,
        game_id: str,
        *,
        phase: str,
        action_type: str,
        payload: Mapping[str, Any],
        day: int = 0,
        phase_command_id: str | None = None,
        player_id: str | None = None,
        llm_call_id: str | None = None,
        is_valid: bool = True,
        validation_errors: Sequence[Any] = (),
    ) -> PhaseAction:
        game = self.require_game(game_id)
        action = PhaseAction(
            game_id=game_id,
            phase_command_id=phase_command_id,
            player_id=player_id,
            llm_call_id=llm_call_id,
            day=day,
            rules_version=game.rules_version,
            phase=phase,
            action_type=action_type,
            payload=sanitize_for_storage(payload),
            is_valid=is_valid,
            validation_errors=sanitize_for_storage(validation_errors),
        )
        self.session.add(action)
        self.session.flush()
        return action

    def list_phase_actions(
        self,
        game_id: str,
        *,
        phase: str | None = None,
        player_id: str | None = None,
    ) -> list[PhaseAction]:
        statement = select(PhaseAction).where(PhaseAction.game_id == game_id)
        if phase is not None:
            statement = statement.where(PhaseAction.phase == phase)
        if player_id is not None:
            statement = statement.where(PhaseAction.player_id == player_id)
        return list(self.session.scalars(statement.order_by(PhaseAction.created_at, PhaseAction.id)))

    def add_vote(
        self,
        game_id: str,
        *,
        voter_player_id: str,
        target_player_id: str | None,
        abstained: bool = False,
        day: int,
        public_reason: str,
        phase: str = "DAY_VOTING",
        round: int = 1,
        phase_action_id: str | None = None,
        confidence: float | None = None,
        is_valid: bool = True,
    ) -> Vote:
        vote = Vote(
            game_id=game_id,
            voter_player_id=voter_player_id,
            target_player_id=target_player_id,
            abstained=abstained,
            day=day,
            phase=phase,
            round=round,
            public_reason=public_reason,
            phase_action_id=phase_action_id,
            confidence=confidence,
            is_valid=is_valid,
        )
        self.session.add(vote)
        self.session.flush()
        return vote

    def list_votes(self, game_id: str, *, day: int | None = None) -> list[Vote]:
        statement = select(Vote).where(Vote.game_id == game_id)
        if day is not None:
            statement = statement.where(Vote.day == day)
        return list(self.session.scalars(statement.order_by(Vote.day, Vote.round, Vote.created_at)))

    def get_or_create_agent_session(
        self,
        game_id: str,
        player_id: str,
        *,
        state: Mapping[str, Any] | None = None,
    ) -> AgentSession:
        existing = self.session.scalar(
            select(AgentSession).where(
                AgentSession.game_id == game_id, AgentSession.player_id == player_id
            )
        )
        if existing is not None:
            return existing
        agent_session = AgentSession(
            game_id=game_id,
            player_id=player_id,
            session_key=f"game:{game_id}:player:{player_id}",
            state=sanitize_for_storage(state or {}),
        )
        self.session.add(agent_session)
        self.session.flush()
        return agent_session

    def add_memory_snapshot(
        self,
        game_id: str,
        player_id: str,
        agent_session_id: str,
        *,
        snapshot: Mapping[str, Any],
        phase: str,
        day: int = 0,
        llm_call_id: str | None = None,
    ) -> AgentMemorySnapshot:
        last_sequence = self.session.scalar(
            select(func.max(AgentMemorySnapshot.sequence)).where(
                AgentMemorySnapshot.agent_session_id == agent_session_id
            )
        )
        memory = AgentMemorySnapshot(
            game_id=game_id,
            player_id=player_id,
            agent_session_id=agent_session_id,
            llm_call_id=llm_call_id,
            sequence=(last_sequence or 0) + 1,
            day=day,
            phase=phase,
            snapshot=sanitize_for_storage(snapshot),
        )
        self.session.add(memory)
        self.session.flush()
        return memory

    def list_memory_snapshots(
        self, game_id: str, *, player_id: str | None = None
    ) -> list[AgentMemorySnapshot]:
        statement = select(AgentMemorySnapshot).where(AgentMemorySnapshot.game_id == game_id)
        if player_id is not None:
            statement = statement.where(AgentMemorySnapshot.player_id == player_id)
        return list(
            self.session.scalars(
                statement.order_by(AgentMemorySnapshot.player_id, AgentMemorySnapshot.sequence)
            )
        )

    def start_llm_call(
        self,
        *,
        request_id: str,
        provider: str,
        model: str,
        adapter_version: str,
        game_id: str,
        phase: str,
        call_type: str,
        request_snapshot: Mapping[str, Any],
        player_id: str | None = None,
        agent_session_id: str | None = None,
        day: int = 0,
        attempt: int = 1,
        prompt_version: str | None = None,
        rules_version: str | None = None,
        schema_version: str | None = None,
    ) -> LLMCall:
        game = self.require_game(game_id)
        call = LLMCall(
            request_id=request_id,
            provider=provider,
            model=model,
            adapter_version=adapter_version,
            game_id=game_id,
            player_id=player_id,
            agent_session_id=agent_session_id,
            day=day,
            phase=phase,
            call_type=call_type,
            attempt=attempt,
            request_snapshot=sanitize_for_storage(request_snapshot),
            prompt_version=prompt_version,
            rules_version=rules_version or game.rules_version,
            schema_version=schema_version,
            status="STARTED",
        )
        self.session.add(call)
        self.session.flush()
        return call

    def complete_llm_call(
        self,
        call: LLMCall,
        *,
        final_text: str,
        parsed_result: Mapping[str, Any] | None = None,
        response_snapshot: Mapping[str, Any] | None = None,
        provider_request_id: str | None = None,
        validation_errors: Sequence[Any] = (),
        usage: Mapping[str, Any] | None = None,
        reasoning_tokens: int | None = None,
        latency_ms: int | None = None,
        safety_flagged: bool = False,
    ) -> LLMCall:
        call.final_text = final_text
        call.parsed_result = sanitize_for_storage(parsed_result) if parsed_result is not None else None
        call.response_snapshot = (
            sanitize_for_storage(response_snapshot) if response_snapshot is not None else None
        )
        call.provider_request_id = provider_request_id
        call.validation_errors = sanitize_for_storage(validation_errors)
        call.usage = sanitize_for_storage(usage or {})
        call.reasoning_tokens = reasoning_tokens
        call.latency_ms = latency_ms
        call.safety_flagged = safety_flagged
        call.status = "SUCCEEDED"
        call.finished_at = utc_now()
        self.session.flush()
        return call

    def fail_llm_call(
        self,
        call: LLMCall,
        *,
        error_type: str,
        error_message: str,
        validation_errors: Sequence[Any] = (),
        latency_ms: int | None = None,
        response_snapshot: Mapping[str, Any] | None = None,
    ) -> LLMCall:
        call.status = "FAILED"
        call.error_type = error_type
        call.error_message = error_message
        call.validation_errors = sanitize_for_storage(validation_errors)
        call.latency_ms = latency_ms
        call.response_snapshot = (
            sanitize_for_storage(response_snapshot) if response_snapshot is not None else None
        )
        call.finished_at = utc_now()
        self.session.flush()
        return call

    def get_llm_call(self, call_id: str) -> LLMCall | None:
        return self.session.get(LLMCall, call_id)

    def list_llm_calls(
        self,
        game_id: str,
        *,
        player_id: str | None = None,
        phase: str | None = None,
        call_type: str | None = None,
    ) -> list[LLMCall]:
        statement: Select[tuple[LLMCall]] = select(LLMCall).where(LLMCall.game_id == game_id)
        if player_id is not None:
            statement = statement.where(LLMCall.player_id == player_id)
        if phase is not None:
            statement = statement.where(LLMCall.phase == phase)
        if call_type is not None:
            statement = statement.where(LLMCall.call_type == call_type)
        return list(self.session.scalars(statement.order_by(LLMCall.started_at, LLMCall.id)))

    def add_llm_message(
        self,
        llm_call_id: str,
        *,
        ordinal: int,
        role: str,
        content: str,
        name: str | None = None,
        message_metadata: Mapping[str, Any] | None = None,
    ) -> LLMMessage:
        message = LLMMessage(
            llm_call_id=llm_call_id,
            ordinal=ordinal,
            role=role,
            content=content,
            name=name,
            message_metadata=sanitize_for_storage(message_metadata or {}),
        )
        self.session.add(message)
        self.session.flush()
        return message

    def list_llm_messages(self, llm_call_id: str) -> list[LLMMessage]:
        return list(
            self.session.scalars(
                select(LLMMessage)
                .where(LLMMessage.llm_call_id == llm_call_id)
                .order_by(LLMMessage.ordinal)
            )
        )


@dataclass(frozen=True, slots=True)
class CommandExecution:
    command_id: str
    status: str
    result: dict[str, Any] | None
    replayed: bool


class PersistenceService:
    """在明确事务边界内执行阶段命令，并返回稳定的幂等结果。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    def execute_phase_command(
        self,
        *,
        game_id: str,
        command_id: str,
        command_type: str,
        phase: str,
        handler: Callable[[Repository, PhaseCommand], Mapping[str, Any] | None],
        request_payload: Mapping[str, Any] | None = None,
    ) -> CommandExecution:
        with self.database.session() as session:
            repo = Repository(session)
            existing = repo.get_phase_command(game_id, command_id)
            if existing is not None:
                return CommandExecution(
                    command_id=command_id,
                    status=existing.status,
                    result=existing.result_payload,
                    replayed=True,
                )
            command = repo.create_phase_command(
                game_id,
                command_id,
                command_type=command_type,
                phase=phase,
                request_payload=request_payload,
            )
            repo.update_phase_command(command, status="RUNNING")

        try:
            with self.database.session() as session:
                repo = Repository(session)
                command = repo.get_phase_command(game_id, command_id)
                if command is None:
                    raise EntityNotFoundError(f"phase command not found: {command_id}")
                result = sanitize_for_storage(handler(repo, command) or {})
                repo.update_phase_command(command, status="SUCCEEDED", result_payload=result)
                return CommandExecution(command_id, "SUCCEEDED", result, False)
        except BaseException as exc:
            with self.database.session() as session:
                repo = Repository(session)
                command = repo.get_phase_command(game_id, command_id)
                if command is not None:
                    repo.update_phase_command(
                        command,
                        status="FAILED",
                        error={"type": type(exc).__name__, "message": str(exc)},
                    )
            raise
