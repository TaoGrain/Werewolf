from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from app.domain import Game, GamePhase, GameRuleError, Role, Visibility
from app.agents.schemas import split_model_text
from app.persistence import EntityNotFoundError, Repository
from app.services import GameApplicationService

from .schemas import AIReviewRequest, CommandRequest, CommandResponse, CreateGameRequest


router = APIRouter()


def service(request: Request) -> GameApplicationService:
    return request.app.state.game_service


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _can_reveal(game: Game, requested: bool) -> bool:
    return game.phase == GamePhase.FINISHED or bool(requested and game.config.god_view)


def _role_history(game: Game, player_id: str) -> list[dict[str, Any]]:
    """整理角色的历史行动，供获准的上帝视角和赛后复盘展示。"""
    role = game.role_assignments.get(player_id)
    if role is None or role.value not in {"SEER", "WITCH", "HUNTER"}:
        return []

    event_types = {
        "SEER": {"SEER_INSPECTION"},
        "WITCH": {"WITCH_ACTION"},
        "HUNTER": {
            "HUNTER_ACTION_REQUESTED",
            "HUNTER_ACTION_SUBMITTED",
            "HUNTER_SHOT",
            "HUNTER_PASSED",
        },
    }[role.value]
    history: list[dict[str, Any]] = []
    for event in game.events:
        if event.type.value not in event_types:
            continue
        actor_id = (
            event.payload.get("actor_id")
            or event.payload.get("player_id")
            or event.payload.get("hunter_id")
        )
        if str(actor_id) != str(player_id):
            continue
        history.append(
            {
                "day": event.day,
                "phase": event.phase.value,
                "event_type": event.type.value,
                **event.payload,
            }
        )
    return history


def _serialize_winner(game: Game) -> dict[str, Any] | None:
    if game.winner is None:
        return None
    winner = game.winner.model_dump(mode="json")
    alive_roles = [
        game.role_assignments.get(player.id)
        for player in game.alive_players()
    ]
    winner["alive_wolves"] = int(winner.get("alive_wolves") or 0)
    winner["alive_good"] = int(winner.get("alive_good") or 0)
    if winner.get("alive_gods") is None:
        winner["alive_gods"] = sum(
            role in {Role.SEER, Role.WITCH, Role.HUNTER} for role in alive_roles
        )
    if winner.get("alive_villagers") is None:
        winner["alive_villagers"] = sum(role == Role.VILLAGER for role in alive_roles)
    return winner

def _phase_progress(game: Game, *, reveal: bool) -> dict[str, int | str] | None:
    """Return stable, public-safe progress for phases that wait on player actions."""
    alive_ids = {player.id for player in game.alive_players()}

    private_night_phases = {
        GamePhase.NIGHT_SEER,
        GamePhase.NIGHT_WOLF_DISCUSSION,
        GamePhase.NIGHT_WOLF,
        GamePhase.NIGHT_WITCH,
    }
    if not reveal and game.phase in private_night_phases:
        return {
            "completed": 0,
            "total": 1,
            "label": "夜间行动",
        }

    def alive_role_ids(role: Role) -> set[str]:
        return {
            player_id
            for player_id in alive_ids
            if game.role_assignments.get(player_id) == role
        }

    def progress(completed: int, total: int, label: str) -> dict[str, int | str] | None:
        if total <= 0:
            return None
        return {
            "completed": min(max(completed, 0), total),
            "total": total,
            "label": label,
        }

    if game.phase == GamePhase.NIGHT_SEER:
        seers = alive_role_ids(Role.SEER)
        return progress(1 if game.seer_action is not None else 0, len(seers), "预言家查验")

    if game.phase == GamePhase.NIGHT_WOLF_DISCUSSION:
        wolves = alive_role_ids(Role.WOLF)
        completed = len(wolves.intersection(game.wolf_discussion))
        return progress(completed, len(wolves), "狼人讨论")

    if game.phase == GamePhase.NIGHT_WOLF:
        wolves = alive_role_ids(Role.WOLF)
        completed = len(wolves.intersection(game.wolf_actions))
        return progress(completed, len(wolves), "狼人行动")

    if game.phase == GamePhase.NIGHT_WITCH:
        witches = alive_role_ids(Role.WITCH)
        return progress(1 if game.witch_action is not None else 0, len(witches), "女巫行动")

    if game.phase == GamePhase.HUNTER_REACTION:
        completed = 0 if game.pending_hunter_action is not None else 1
        return progress(completed, 1, "猎人反应")

    if game.phase == GamePhase.DAY_DISCUSSION:
        completed = len(alive_ids.intersection(game.speeches))
        return progress(completed, len(alive_ids), "白天发言")

    if game.phase == GamePhase.DAY_RUNOFF_DISCUSSION:
        candidates = alive_ids.intersection(game.runoff_candidate_ids)
        completed = len(candidates.intersection(game.runoff_speeches))
        return progress(completed, len(candidates), "PK 发言")

    if game.phase == GamePhase.DAY_VOTING:
        eligible_voters = set(alive_ids)
        if game.vote_round > 1 and game.config.ruleset.runoff_has_discussion:
            eligible_voters.difference_update(game.runoff_candidate_ids)
        completed = len(eligible_voters.intersection(game.votes))
        return progress(completed, len(eligible_voters), "投票")

    if game.phase == GamePhase.DAY_RESOLUTION:
        if (
            game.pending_exile_id is None
            or not game.config.ruleset.exile_has_last_words
        ):
            return None
        return progress(1 if game.last_words_submitted else 0, 1, "遗言")

    return None


def _runtime_error_fields(
    error: dict[str, Any] | None,
) -> tuple[str | None, str | None, bool]:
    if not error:
        return None, None, False
    message = str(error.get("message") or error.get("detail") or "阶段执行失败")
    raw_type = str(error.get("error_type") or error.get("type") or "phase_error")
    searchable = f"{raw_type} {message}".lower()
    is_timeout = "timeout" in searchable or "超时" in searchable
    is_budget = any(
        marker in searchable
        for marker in ("budget", "attempt limit", "attempt budget", "预算", "尝试次数")
    )
    if is_timeout:
        error_type = "timeout_error"
    elif is_budget:
        error_type = "budget_exhausted"
    else:
        error_type = raw_type
    retryable = bool(error.get("retryable", is_timeout or is_budget))
    return message, error_type, retryable



def _serialize_game(
    game: Game,
    reveal: bool = False,
    persisted_status: str | None = None,
    is_processing: bool = False,
    active_command_id: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    publicly_dead: set[str] = set()
    for event in game.events:
        if event.visibility != Visibility.PUBLIC:
            continue
        publicly_dead.update(str(item) for item in event.payload.get("death_ids", []))
        if event.type.value == "PLAYER_DIED" and event.payload.get("player_id"):
            publicly_dead.add(str(event.payload["player_id"]))

    def visible_alive(player: Any) -> bool:
        actually_alive = player.status.value == "ALIVE"
        return actually_alive if reveal else player.id not in publicly_dead

    discussion_players = sorted(game.alive_players(), key=lambda item: item.seat)
    discussion_completed = [
        player.id for player in discussion_players if player.id in game.speeches
    ]
    next_speaker = next(
        (player for player in discussion_players if player.id not in game.speeches),
        None,
    )

    role_counts = {
        role.value: count for role, count in game.config.ruleset.role_counts.items()
    }
    error_message, error_type, retryable = _runtime_error_fields(error)

    return {
        "id": game.id,
        "status": "FINISHED" if game.phase == GamePhase.FINISHED else (persisted_status or "ACTIVE"),
        "phase": game.phase.value,
        "day": game.day,
        "rules_version": game.config.ruleset.version,
        "ruleset_version": game.config.ruleset.version,
        "player_count": len(game.players),
        "alive_player_count": sum(1 for player in game.players if visible_alive(player)),
        "lineup": role_counts,
        "role_counts": role_counts,
        "god_mode_enabled": bool(game.config.god_view),
        "god_mode": bool(game.config.god_view and reveal),
        "allow_private_inspection": bool(game.config.allow_private_inspection),
        "model": game.config.model_key,
        "thinking_enabled": game.config.thinking_enabled,
        "speech_temperature": game.config.speech_temperature,
        "vote_temperature": game.config.vote_temperature,
        "action_temperature": game.config.action_temperature,
        "is_paused": persisted_status == "PAUSED",
        "is_processing": is_processing,
        "active_command_id": active_command_id,
        "error": error_message,
        "error_type": error_type,
        "retryable": retryable,
        "progress": _phase_progress(game, reveal=reveal),
        "winner": _serialize_winner(game),
        "name": "狼人杀对局",
        "players": [
            {
                "id": player.id,
                "seat": player.seat,
                "name": player.name,
                "personality": player.personality,
                "alive": visible_alive(player),
                "is_alive": visible_alive(player),
                "role": game.role_assignments.get(player.id).value if reveal and player.id in game.role_assignments else None,
                "death_cause": player.death_cause.value if player.death_cause and reveal else None,
                "death_causes": (
                    [cause.value for cause in player.death_causes]
                    if reveal
                    else []
                ),
                "role_history": _role_history(game, player.id) if reveal else [],
            }
            for player in sorted(game.players, key=lambda item: item.seat)
        ],
        "discussion": {
            "completed": len(discussion_completed),
            "total": len(discussion_players),
            "completed_player_ids": discussion_completed,
            "next_player_id": next_speaker.id if next_speaker else None,
        } if game.phase == GamePhase.DAY_DISCUSSION else None,
    }


def _serialize_event(event: Any, *, reveal_private: bool = True) -> dict[str, Any]:
    payload = dict(event.payload or {})
    if not reveal_private and event.event_type == "HUNTER_ACTION_REQUESTED":
        payload.pop("trigger_causes", None)
    return {
        "id": event.id,
        "sequence": event.sequence,
        "event_type": event.event_type,
        "type": event.event_type,
        "day": event.day,
        "phase": event.phase,
        "visibility": event.visibility,
        "actor_player_id": event.actor_player_id,
        "player_id": event.actor_player_id or payload.get("player_id") or payload.get("actor_id"),
        "content": payload.get("content") or payload.get("reason"),
        "rules_version": event.rules_version,
        "ruleset_version": event.rules_version,
        "payload": payload,
        "created_at": event.created_at.isoformat(),
    }


def _serialize_call(row: Any) -> dict[str, Any]:
    usage = row.usage or {}
    response_snapshot = row.response_snapshot or {}
    embedded_reasoning, final_text = split_model_text(row.final_text or "")
    reasoning_text = response_snapshot.get("reasoning_text") or embedded_reasoning
    return {
        "id": row.id,
        "player_id": row.player_id,
        "provider": row.provider,
        "model": row.model,
        "adapter_version": row.adapter_version,
        "day": row.day,
        "phase": row.phase,
        "call_type": row.call_type,
        "request_id": row.provider_request_id,
        "request_snapshot": row.request_snapshot,
        "response_snapshot": response_snapshot,
        "final_text": final_text,
        "response_text": final_text,
        "reasoning_text": reasoning_text,
        "parsed_result": row.parsed_result,
        "validation_errors": row.validation_errors,
        "usage": usage,
        "rules_version": row.rules_version,
        "ruleset_version": row.rules_version,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "reasoning_tokens": row.reasoning_tokens or 0,
        "latency_ms": row.latency_ms,
        "status": row.status,
        "error_type": row.error_type,
    }

def _serialize_action(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "player_id": row.player_id,
        "day": row.day,
        "phase": row.phase,
        "action_type": row.action_type,
        "payload": row.payload,
        "is_valid": row.is_valid,
        "validation_errors": row.validation_errors,
        "rules_version": row.rules_version,
        "ruleset_version": row.rules_version,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/api/games", status_code=status.HTTP_201_CREATED)
def create_game(payload: CreateGameRequest, request: Request) -> dict[str, Any]:
    try:
        game = service(request).create_game(
            player_names=payload.player_names,
            player_count=payload.player_count,
            ruleset_version=payload.ruleset_version,
            random_seed=payload.random_seed if payload.random_seed is not None else (payload.seed or 0),
            god_view=payload.god_mode,
            allow_private_inspection=payload.allow_private_inspection,
            model_key=payload.model,
            thinking_enabled=payload.thinking_enabled,
            speech_temperature=payload.speech_temperature,
            vote_temperature=payload.vote_temperature,
            action_temperature=payload.action_temperature,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _serialize_game(game)


@router.get("/api/games")
def list_games(request: Request) -> list[dict[str, Any]]:
    return service(request).list_games()


@router.get("/api/games/{game_id}")
def get_game(game_id: str, request: Request, god_mode: bool = False) -> dict[str, Any]:
    try:
        game = service(request).load_game(game_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    runtime = service(request).load_game_runtime(game_id)
    return _serialize_game(
        game,
        _can_reveal(game, god_mode),
        runtime["status"],
        runtime["is_processing"],
        runtime["active_command_id"],
        runtime["error"],
    )


@router.post("/api/games/{game_id}/start", response_model=CommandResponse)
def start_game(
    game_id: str,
    request: Request,
    payload: CommandRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CommandResponse:
    try:
        result = service(request).start_game(
            game_id,
            (payload.command_id if payload else None) or idempotency_key or str(uuid4()),
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CommandResponse(**asdict(result))


@router.post("/api/games/{game_id}/advance", response_model=CommandResponse, status_code=202)
async def advance_game(
    game_id: str,
    request: Request,
    payload: CommandRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CommandResponse:
    try:
        result = service(request).enqueue_advance(
            game_id,
            (payload.command_id if payload else None) or idempotency_key,
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CommandResponse(**asdict(result))


@router.get("/api/games/{game_id}/commands/{command_id}", response_model=CommandResponse)
def command_status(game_id: str, command_id: str, request: Request) -> CommandResponse:
    result = service(request).command_status(game_id, command_id)
    if result is None:
        raise HTTPException(status_code=404, detail="命令不存在")
    return CommandResponse(**asdict(result))


@router.post("/api/games/{game_id}/pause", status_code=204)
def pause_game(game_id: str, request: Request) -> Response:
    try:
        service(request).pause_game(game_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    return Response(status_code=204)


@router.post("/api/games/{game_id}/retry", response_model=CommandResponse, status_code=202)
async def retry_game(
    game_id: str,
    request: Request,
    payload: CommandRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CommandResponse:
    try:
        service(request).resume_game(game_id)
        result = service(request).enqueue_advance(
            game_id,
            (payload.command_id if payload else None) or idempotency_key or str(uuid4()),
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    return CommandResponse(**asdict(result))


@router.get("/api/games/{game_id}/events")
def game_events(
    game_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0, alias="after"),
    god_mode: bool = False,
) -> list[dict[str, Any]]:
    try:
        game = service(request).load_game(game_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    reveal = _can_reveal(game, god_mode)
    with service(request).database.session() as session:
        rows = Repository(session).list_events(game_id, after_sequence=after_sequence)
        allowed = rows if reveal else [row for row in rows if row.visibility == Visibility.PUBLIC.value]
        if game.phase == GamePhase.FINISHED:
            allowed = rows
        return [_serialize_event(row, reveal_private=reveal) for row in allowed]


@router.get("/api/games/{game_id}/stream")
async def game_stream(
    game_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0, alias="after"),
    god_mode: bool = False,
) -> StreamingResponse:
    try:
        game = service(request).load_game(game_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    reveal = _can_reveal(game, god_mode)

    async def stream():
        cursor = after_sequence
        heartbeat = 0
        while not await request.is_disconnected():
            with service(request).database.session() as session:
                rows = Repository(session).list_events(game_id, after_sequence=cursor)
            for row in rows:
                cursor = max(cursor, row.sequence)
                if reveal or row.visibility == Visibility.PUBLIC.value:
                    payload = json.dumps(
                        _serialize_event(row, reveal_private=reveal),
                        ensure_ascii=False,
                    )
                    yield f"id: {row.sequence}\ndata: {payload}\n\n"
            heartbeat += 1
            if heartbeat % 40 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _require_private_access(game: Game, god_mode: bool) -> None:
    if game.phase != GamePhase.FINISHED and (
        not _can_reveal(game, god_mode) or not game.config.allow_private_inspection
    ):
        raise HTTPException(status_code=403, detail="安全观战模式不允许读取 Agent 私有记录")


@router.get("/api/games/{game_id}/players/{player_id}/calls")
def player_calls(game_id: str, player_id: str, request: Request, god_mode: bool = False) -> list[dict[str, Any]]:
    try:
        game = service(request).load_game(game_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    _require_private_access(game, god_mode)
    with service(request).database.session() as session:
        rows = Repository(session).list_llm_calls(game_id, player_id=player_id)
        return [_serialize_call(row) for row in rows]


@router.get("/api/games/{game_id}/players/{player_id}/memory")
def player_memory(game_id: str, player_id: str, request: Request, god_mode: bool = False) -> dict[str, Any]:
    try:
        game = service(request).load_game(game_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    _require_private_access(game, god_mode)
    with service(request).database.session() as session:
        rows = Repository(session).list_memory_snapshots(game_id, player_id=player_id)
        snapshots = [
            {"sequence": row.sequence, "day": row.day, "phase": row.phase, "snapshot": row.snapshot, "created_at": row.created_at.isoformat()}
            for row in rows
        ]
        return {
            "player_id": player_id,
            "current": snapshots[-1]["snapshot"] if snapshots else None,
            "snapshots": snapshots,
        }


@router.get("/api/games/{game_id}/review")
def game_review(
    game_id: str,
    request: Request,
    god_mode: bool = False,
    player_id: str | None = None,
) -> dict[str, Any]:
    try:
        game = service(request).load_game(game_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    reveal = _can_reveal(game, god_mode)
    with service(request).database.session() as session:
        repo = Repository(session)
        events = repo.list_events(game_id)
        votes = repo.list_votes(game_id)
        calls = repo.list_llm_calls(game_id, player_id=player_id)
        actions = repo.list_phase_actions(game_id)
        ai_review = next(
            (
                command.result_payload
                for command in reversed(repo.list_phase_commands(game_id))
                if command.command_type == "ai_review"
                and command.status == "SUCCEEDED"
                and command.result_payload is not None
            ),
            None,
        )
    gameplay_calls = [call for call in calls if call.call_type != "ai_review"]
    visible_events = events if reveal else [event for event in events if event.visibility == Visibility.PUBLIC.value]
    if player_id:
        role = game.role_assignments.get(player_id)

        def includes_player(event: Any) -> bool:
            audience = event.payload.get("audience_player_ids")
            return not audience or player_id in {str(item) for item in audience}

        visible_events = [
            event
            for event in events
            if event.visibility == Visibility.PUBLIC.value
            or (
                event.visibility == Visibility.PLAYER_PRIVATE.value
                and (
                    event.actor_player_id == player_id
                    or event.payload.get("player_id") == player_id
                    or (role is not None and role.value == "WITCH" and "target_id" in event.payload)
                )
            )
            or (
                event.visibility == Visibility.WOLF_TEAM_PRIVATE.value
                and role is not None
                and role.value == "WOLF"
                and includes_player(event)
            )
        ]
    serialized_events = [
        _serialize_event(event, reveal_private=reveal)
        for event in visible_events
    ]
    serialized_game = _serialize_game(
        game,
        reveal and player_id is None,
        service(request).load_game_status(game_id),
    )
    if player_id and player_id in game.role_assignments:
        allowed_role_ids = {player_id}
        if game.role_assignments[player_id].value == "WOLF":
            allowed_role_ids.update(
                pid for pid, assigned in game.role_assignments.items() if assigned.value == "WOLF"
            )
        for player in serialized_game["players"]:
            if player["id"] in allowed_role_ids:
                player["role"] = game.role_assignments[player["id"]].value
    return {
        "game": serialized_game,
        "players": serialized_game["players"],
        "events": serialized_events,
        "rules_version": game.config.ruleset.version,
        "ruleset_version": game.config.ruleset.version,
        "timeline": serialized_events,
        "winner": _serialize_winner(game),
        "calls": [_serialize_call(call) for call in gameplay_calls] if reveal else [],
        "actions": [_serialize_action(action) for action in actions] if reveal and player_id is None else [],
        "metrics": {
            "vote_count": len(votes),
            "invalid_action_count": 0,
            "llm_call_count": len(gameplay_calls) if reveal else None,
            "total_tokens": sum(int((call.usage or {}).get("total_tokens") or 0) for call in gameplay_calls) if reveal else None,
            "average_latency_ms": (round(sum(call.latency_ms or 0 for call in gameplay_calls) / len(gameplay_calls), 2) if reveal and gameplay_calls else None),
        },
        "fact_based": True,
        "ai_review": ai_review,
    }


@router.post("/api/games/{game_id}/review/ai")
async def generate_game_ai_review(
    game_id: str,
    request: Request,
    payload: AIReviewRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    body = payload or AIReviewRequest()
    try:
        return await service(request).generate_ai_review(
            game_id,
            command_id=body.command_id or idempotency_key or str(uuid4()),
            model_key=body.model,
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except GameRuleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI 复盘生成失败：{exc}") from exc
