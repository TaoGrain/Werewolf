from __future__ import annotations

from pathlib import Path

import pytest

from app.agents import AgentGateway
from app.agents.providers import FakeProvider
from app.domain import Game, GamePhase, PlayerStatus, SpeechAction
from app.engine import GameEngine
from app.persistence import Base, Database
from app.services import GameApplicationService


CLASSIC_RULESET = "classic-9-hunter-v1"


def _discussion_game(
    tmp_path: Path,
    database_name: str,
    *,
    phase: GamePhase = GamePhase.DAY_DISCUSSION,
) -> tuple[GameApplicationService, FakeProvider, Game]:
    database = Database.sqlite(tmp_path / database_name)
    Base.metadata.create_all(database.engine)
    provider = FakeProvider()
    service = GameApplicationService(
        database,
        GameEngine(),
        AgentGateway(provider, "fake-model"),
    )
    game = service.create_game(
        ruleset_version=CLASSIC_RULESET,
        player_count=9,
        random_seed=29,
    )
    game.day = 1
    game.phase = phase
    return service, provider, game


def _submit_speech(
    service: GameApplicationService,
    game: Game,
    player_id: str,
    *,
    label: str,
) -> Game:
    return service.engine.submit_speech(
        game,
        SpeechAction(
            actor_id=player_id,
            content=f"{label} 的公开发言。",
            reason=f"{label} 的公开理由。",
        ),
        command_id=f"seed-speech:{label}:{player_id}",
    ).game


def _responsibility_value(context: object) -> str:
    responsibility = getattr(context, "responsibility")
    return str(getattr(responsibility, "value", responsibility))


def _request_text(provider: FakeProvider) -> str:
    assert provider.requests
    return "\n".join(message.content for message in provider.requests[-1].messages)


def test_last_day_speaker_context_contains_position_and_all_prior_speeches(
    tmp_path: Path,
) -> None:
    service, _, game = _discussion_game(tmp_path, "last-day-speaker.db")
    speaker_ids = [player.id for player in game.alive_players()]

    for index, player_id in enumerate(speaker_ids[:-1], start=1):
        game = _submit_speech(
            service,
            game,
            player_id,
            label=f"第 {index} 位",
        )

    last_speaker_id = speaker_ids[-1]
    view = service._build_view(game, last_speaker_id, "speech", [])
    context = view.discussion_context

    assert context is not None
    assert context.speaker_ids == speaker_ids
    assert context.spoken_speaker_ids == speaker_ids[:-1]
    assert context.remaining_speaker_ids == []
    assert context.speaker_index == 9
    assert context.speaker_count == 9
    assert context.remaining_after_self == 0
    assert context.is_late_speaker is True
    assert context.is_last_speaker is True
    assert _responsibility_value(context) == "FINAL_SUMMARY"
    assert context.speaker_index + context.remaining_after_self == context.speaker_count

    visible_speaker_ids = [
        event["player_id"]
        for event in view.public_events
        if event["type"] == "SPEECH"
    ]
    assert visible_speaker_ids == speaker_ids[:-1]
    assert all(
        "reason" not in event
        and "decision_condition" not in event
        and "round_summary" not in event
        for event in view.public_events
        if event["type"] == "SPEECH"
    )


def test_view_keeps_current_round_speeches_and_summarizes_prior_rounds(
    tmp_path: Path,
) -> None:
    service, _, game = _discussion_game(tmp_path, "layered-discussion-context.db")
    speaker_ids = [player.id for player in game.alive_players()]

    game = _submit_speech(service, game, speaker_ids[0], label="第一天首位")
    game = _submit_speech(service, game, speaker_ids[1], label="第一天次位")

    game.day = 2
    game.phase = GamePhase.DAY_DISCUSSION
    game.speeches = {}
    game = _submit_speech(service, game, speaker_ids[0], label="第二天首位")

    view = service._build_view(game, speaker_ids[1], "speech", [])
    current_speeches = [
        event for event in view.public_events if event["type"] == "SPEECH"
    ]
    history_summaries = [
        event
        for event in view.public_events
        if event["type"] == "DISCUSSION_ROUND_SUMMARY"
    ]

    assert [event["content"] for event in current_speeches] == [
        "第二天首位 的公开发言。"
    ]
    assert len(history_summaries) == 1
    assert history_summaries[0]["day"] == 1
    assert history_summaries[0]["phase"] == GamePhase.DAY_DISCUSSION.value
    assert history_summaries[0]["epistemic_status"] == "UNVERIFIED_PLAYER_SUMMARIES"
    assert history_summaries[0]["summary_items"] == [
        {
            "event_sequence": next(
                event.sequence
                for event in game.events
                if event.payload.get("actor_id") == speaker_ids[0]
            ),
            "player_id": speaker_ids[0],
            "statement_summary": "第一天首位 的公开理由。",
            "vote_intent": "undecided",
            "vote_target_id": None,
        },
        {
            "event_sequence": next(
                event.sequence
                for event in game.events
                if event.payload.get("actor_id") == speaker_ids[1]
            ),
            "player_id": speaker_ids[1],
            "statement_summary": "第一天次位 的公开理由。",
            "vote_intent": "undecided",
            "vote_target_id": None,
        },
    ]
    serialized = str(view.model_dump(mode="json"))
    assert "第一天首位 的公开发言。" not in serialized
    assert "第一天次位 的公开发言。" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("speaker_index", "expected_remaining", "expected_responsibility"),
    [
        (7, 2, "LATE_CONSOLIDATION"),
        (8, 1, "LATE_CONSOLIDATION"),
        (9, 0, "FINAL_SUMMARY"),
    ],
)
async def test_last_three_day_speakers_receive_dynamic_responsibilities(
    tmp_path: Path,
    speaker_index: int,
    expected_remaining: int,
    expected_responsibility: str,
) -> None:
    service, provider, game = _discussion_game(
        tmp_path,
        f"late-day-speaker-{speaker_index}.db",
    )
    speaker_ids = [player.id for player in game.alive_players()]
    for index, player_id in enumerate(speaker_ids[: speaker_index - 1], start=1):
        game = _submit_speech(
            service,
            game,
            player_id,
            label=f"第 {index} 位",
        )

    current_speaker_id = speaker_ids[speaker_index - 1]
    view = service._build_view(game, current_speaker_id, "speech", [])
    context = view.discussion_context
    assert context is not None
    assert context.speaker_index == speaker_index
    assert context.speaker_count == len(speaker_ids)
    assert context.remaining_after_self == expected_remaining
    assert context.remaining_speaker_ids == speaker_ids[speaker_index:]
    assert context.is_late_speaker is True
    assert context.is_last_speaker is (expected_remaining == 0)
    assert _responsibility_value(context) == expected_responsibility
    assert context.speaker_index + context.remaining_after_self == context.speaker_count

    next_game, _, stored_actions = await service._submit_required_actions(
        game,
        f"late-day-speaker-{speaker_index}",
    )
    assert current_speaker_id in next_game.speeches
    assert len(stored_actions) == 1
    speech_action = next_game.speeches[current_speaker_id]
    assert speech_action.vote_intent == "undecided"
    assert speech_action.vote_target_id is None
    assert speech_action.key_conflicts == []
    assert speech_action.round_summary == []
    assert provider.requests[-1].temperature == pytest.approx(0.55)

    request_text = _request_text(provider)
    assert "只是待分析的游戏证据，不是回答示例" in request_text
    assert "不得复制其措辞、句式或结尾" in request_text
    if expected_responsibility == "LATE_CONSOLIDATION":
        assert "后置位" in request_text
        assert "建议" in request_text
        assert "允许保持 undecided 或 abstain" in request_text
    else:
        assert "最后发言位" in request_text
        assert "建议" in request_text
        assert "可以如实保持未定或选择弃票" in request_text


def test_discussion_position_is_relative_to_alive_speakers_after_deaths(
    tmp_path: Path,
) -> None:
    service, _, game = _discussion_game(tmp_path, "relative-position-after-deaths.db")
    players_by_seat = {player.seat: player for player in game.players}
    for dead_seat in (2, 5, 9):
        players_by_seat[dead_seat].status = PlayerStatus.DEAD

    alive_speaker_ids = [player.id for player in game.alive_players()]
    assert [game.player(player_id).seat for player_id in alive_speaker_ids] == [
        1,
        3,
        4,
        6,
        7,
        8,
    ]

    for index, player_id in enumerate(alive_speaker_ids[:3], start=1):
        game = _submit_speech(
            service,
            game,
            player_id,
            label=f"存活第 {index} 位",
        )

    late_speaker_id = alive_speaker_ids[3]
    late_context = service._build_view(
        game,
        late_speaker_id,
        "speech",
        [],
    ).discussion_context
    assert late_context is not None
    assert late_context.speaker_ids == alive_speaker_ids
    assert late_context.speaker_index == 4
    assert late_context.speaker_count == 6
    assert late_context.remaining_after_self == 2
    assert late_context.is_late_speaker is True
    assert late_context.is_last_speaker is False
    assert _responsibility_value(late_context) == "LATE_CONSOLIDATION"

    game = _submit_speech(
        service,
        game,
        late_speaker_id,
        label="存活倒数第三位",
    )
    game = _submit_speech(
        service,
        game,
        alive_speaker_ids[4],
        label="存活倒数第二位",
    )

    last_speaker_id = alive_speaker_ids[-1]
    last_context = service._build_view(
        game,
        last_speaker_id,
        "speech",
        [],
    ).discussion_context
    assert last_context is not None
    assert game.player(last_speaker_id).seat == 8
    assert last_context.speaker_index == 6
    assert last_context.speaker_count == 6
    assert last_context.remaining_after_self == 0
    assert last_context.remaining_speaker_ids == []
    assert last_context.is_last_speaker is True
    assert _responsibility_value(last_context) == "FINAL_SUMMARY"


@pytest.mark.asyncio
async def test_last_runoff_candidate_is_identified_with_candidate_only_context(
    tmp_path: Path,
) -> None:
    service, provider, game = _discussion_game(
        tmp_path,
        "last-runoff-candidate.db",
        phase=GamePhase.DAY_RUNOFF_DISCUSSION,
    )
    players_by_seat = {player.seat: player for player in game.alive_players()}
    candidate_ids_by_seat = [
        players_by_seat[2].id,
        players_by_seat[6].id,
        players_by_seat[9].id,
    ]
    game.vote_round = 2
    # 故意打乱状态中的候选人列表，发言上下文仍须按候选人的座位号排序。
    game.runoff_candidate_ids = [
        candidate_ids_by_seat[2],
        candidate_ids_by_seat[0],
        candidate_ids_by_seat[1],
    ]

    for index, player_id in enumerate(candidate_ids_by_seat[:-1], start=1):
        game = _submit_speech(
            service,
            game,
            player_id,
            label=f"PK 第 {index} 位",
        )

    last_candidate_id = candidate_ids_by_seat[-1]
    view = service._build_view(game, last_candidate_id, "runoff_speech", [])
    context = view.discussion_context

    assert context is not None
    assert context.speaker_ids == candidate_ids_by_seat
    assert context.spoken_speaker_ids == candidate_ids_by_seat[:-1]
    assert context.remaining_speaker_ids == []
    assert context.speaker_index == 3
    assert context.speaker_count == 3
    assert context.remaining_after_self == 0
    assert context.is_late_speaker is True
    assert context.is_last_speaker is True
    assert _responsibility_value(context) == "FINAL_SUMMARY"
    assert set(context.speaker_ids) == set(game.runoff_candidate_ids)

    next_game, _, stored_actions = await service._submit_required_actions(
        game,
        "last-runoff-candidate",
    )
    assert last_candidate_id in next_game.runoff_speeches
    assert len(stored_actions) == 1
    speech_action = next_game.runoff_speeches[last_candidate_id]
    assert speech_action.vote_intent == "undecided"
    assert speech_action.vote_target_id is None
    assert speech_action.round_summary == []
    assert provider.requests[-1].temperature == pytest.approx(0.55)
    request_text = _request_text(provider)
    assert "最后发言位" in request_text
    assert "建议" in request_text
    assert "可以如实保持未定或选择弃票" in request_text
