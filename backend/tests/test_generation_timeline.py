from __future__ import annotations

import pytest

from app.agents.gateway import AgentGateway
from app.agents.providers import FakeProvider
from app.agents.schemas import VoteOutput
from app.agents.views import PlayerViewBuilder


def _state() -> dict:
    return {
        "id": "timeline-game",
        "day": 2,
        "phase": "DAY_VOTING",
        "ruleset": {"version": "classic-9-hunter-v1"},
        "players": [
            {"id": "p6", "seat": 6, "name": "\u9646\u5ddd", "alive": True},
            {"id": "p7", "seat": 7, "name": "\u79e6\u91ce", "alive": True},
            {"id": "p8", "seat": 8, "name": "\u82cf\u79be", "alive": True},
        ],
        "role_assignments": {"p6": "SEER", "p7": "VILLAGER", "p8": "VILLAGER"},
    }


def _public_events() -> list[dict]:
    return [
        {
            "sequence": 38,
            "type": "DISCUSSION_ROUND_SUMMARY",
            "day": 1,
            "phase": "DAY_DISCUSSION",
            "summary_items": [
                {"event_sequence": 37, "player_id": "p6"},
                {"event_sequence": 38, "player_id": "p7"},
            ],
        },
        {
            "sequence": 75,
            "type": "SPEECH",
            "day": 2,
            "phase": "DAY_DISCUSSION",
            "player_id": "p6",
            "content": "6\u53f7\u5f53\u5929\u53d1\u8a00\u3002",
        },
        {
            "sequence": 76,
            "type": "SPEECH",
            "day": 2,
            "phase": "DAY_DISCUSSION",
            "player_id": "p7",
            "content": "7\u53f7\u5f53\u5929\u53d1\u8a00\u3002",
        },
    ]


def test_player_view_contains_authoritative_speech_timeline() -> None:
    view = PlayerViewBuilder().build(
        _state(),
        "p8",
        public_events=_public_events(),
        allowed_action="vote",
        legal_targets=["p6", "p7"],
    )

    assert [item["event_sequence"] for item in view.speech_timeline] == [37, 38, 75, 76]
    assert [item["seat"] for item in view.speech_timeline] == [6, 7, 6, 7]
    assert [item["round_position"] for item in view.speech_timeline] == [1, 2, 1, 2]
    assert all(item["name"] in {"\u9646\u5ddd", "\u79e6\u91ce"} for item in view.speech_timeline)


@pytest.mark.asyncio
async def test_semantic_timeline_error_does_not_trigger_regeneration() -> None:
    provider = FakeProvider([
        {
            "action": "vote",
            "target_id": "p7",
            "public_reason": "\u6211\u62957\u53f7\uff0c\u5e76\u8ba4\u4e3a7\u53f7\u57286\u53f7\u4e4b\u524d\u53d1\u8a00\u3002",
            "private_reason": "\u6b64\u5904\u6545\u610f\u6a21\u62df\u8bed\u4e49\u5224\u65ad\u9519\u8bef\u3002",
        }
    ])
    gateway = AgentGateway(provider, "fake-model")
    view = PlayerViewBuilder().build(
        _state(),
        "p8",
        public_events=_public_events(),
        allowed_action="vote",
        legal_targets=["p6", "p7"],
    )

    result = await gateway.generate_action(
        view,
        call_type="vote",
        output_model=VoteOutput,
        instruction="\u6839\u636e\u6743\u5a01\u53d1\u8a00\u65f6\u95f4\u7ebf\u5b8c\u6210\u6295\u7968\u3002",
    )

    assert result.action.target_id == "p7"
    assert len(provider.requests) == 1
    prompt = provider.requests[0].messages[-1].content
    assert "speech_timeline" in prompt
    assert '"event_sequence": 37' in prompt
    assert '"round_position": 2' in prompt
