from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents import AgentGateway
from app.agents.providers import FakeProvider
from app.domain import GamePhase
from app.engine import GameEngine
from app.persistence import Base, Database
from app.services import GameApplicationService


@pytest.mark.asyncio
async def test_structurally_valid_speech_is_persisted_without_semantic_repair(
    tmp_path: Path,
) -> None:
    database = Database.sqlite(tmp_path / "speech-passthrough.db")
    Base.metadata.create_all(database.engine)
    provider = FakeProvider()
    service = GameApplicationService(
        database,
        GameEngine(),
        AgentGateway(provider, "fake-model"),
    )
    game = service.create_game(
        ruleset_version="classic-9-hunter-v1",
        player_count=9,
        random_seed=91,
    )
    game.day = 2
    game.phase = GamePhase.DAY_DISCUSSION

    speaker_id = game.alive_players()[0].id
    not_yet_spoken_id = game.alive_players()[-1].id
    original_speech = "顾舟死亡与苏禾昨天的遗言矛盾，这是对前一天公开信息的复盘。"
    provider._responses.append(
        json.dumps(
            {
                "speech": original_speech,
                "public_reason": "保留模型给出的原始判断。",
                "private_strategy": "",
                "cited_player_ids": [not_yet_spoken_id],
                "focus_player_ids": [],
                "key_conflicts": ["顾舟死亡与苏禾遗言矛盾"],
                "vote_intent": "undecided",
                "vote_target_id": None,
                "fallback_target_id": None,
                "decision_condition": None,
            },
            ensure_ascii=False,
        )
    )

    next_game, _, stored_actions = await service._submit_required_actions(
        game,
        "speech-passthrough",
    )

    action = next_game.speeches[speaker_id]
    assert action.content == original_speech
    assert action.cited_player_ids == [not_yet_spoken_id]
    assert action.key_conflicts == ["顾舟死亡与苏禾遗言矛盾"]
    assert stored_actions[0]["content"] == original_speech
    assert len(provider.requests) == 1
    assert provider.requests[0].metadata["call_type"] == "speech"
