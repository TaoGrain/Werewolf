from __future__ import annotations

from functools import partial

import pytest

from app.agents.gateway import AgentGateway, OutputValidationError
from app.agents.providers import FakeProvider
from app.agents.schemas import SpeechOutput, StructuredOutputError
from app.agents.speech_quality import validate_speech_quality
from app.agents.views import DiscussionContext, PlayerViewBuilder
from app.domain import GameConfig, GamePhase, Ruleset
from app.engine import GameEngine
from app.services import GameApplicationService


PLAYER_ALIASES = {
    "p1": ("一号", "1号"),
    "p2": ("二号", "2号"),
    "p3": ("三号", "3号"),
    "p4": ("四号", "4号"),
    "p5": ("五号", "5号"),
}


def _final_context() -> DiscussionContext:
    return DiscussionContext(
        speaker_ids=["p1", "p2", "p3"],
        spoken_speaker_ids=["p1", "p2"],
        remaining_speaker_ids=[],
        speaker_index=3,
        speaker_count=3,
        remaining_after_self=0,
        is_late_speaker=True,
        is_last_speaker=True,
        responsibility="FINAL_SUMMARY",
    )


def _valid_final_output() -> dict:
    return {
        "speech": (
            "p1 的身份判断有公开依据，但 p2 的结论与理由没有闭合。"
            "综合本轮发言，我明确归票给 p2，这是我的最终结论。"
        ),
        "public_reason": "比较两名前位玩家后形成最终票向。",
        "private_strategy": "按公开证据推进。",
        "cited_player_ids": ["p1", "p2"],
        "focus_player_ids": ["p2"],
        "key_conflicts": ["p2 的结论缺少对应依据"],
        "vote_intent": "vote",
        "vote_target_id": "p2",
        "fallback_target_id": None,
        "decision_condition": "除非 p2 能用现有公开记录证明该矛盾不存在，否则维持归票。",
    }


def test_speech_schema_makes_conflicts_optional_and_omits_round_summary() -> None:
    schema = SpeechOutput.model_json_schema()

    assert "key_conflicts" not in schema["required"]
    assert "round_summary" not in schema["properties"]


def test_final_speaker_can_remain_undecided_without_conflict_or_summary() -> None:
    output = SpeechOutput(
        speech="p1 和 p2 的公开依据都不充分，我目前重点核验 p2。",
        public_reason="当前证据不足以形成可靠归票。",
        cited_player_ids=["p1"],
        focus_player_ids=["p2"],
        vote_intent="undecided",
        vote_target_id=None,
        fallback_target_id=None,
        decision_condition="如果 p2 给出可核对的新证据，我会重新形成判断。",
    )

    validate_speech_quality(
        output,
        discussion_context=_final_context(),
        previous_speeches=[],
        legal_target_ids={"p1", "p2"},
        player_aliases=PLAYER_ALIASES,
    )
    assert output.key_conflicts == []


def test_repeated_generic_ending_is_rejected_for_independent_speaker() -> None:
    context = DiscussionContext(
        speaker_ids=["p1", "p2", "p3", "p4", "p5"],
        spoken_speaker_ids=["p1"],
        remaining_speaker_ids=["p3", "p4", "p5"],
        speaker_index=2,
        speaker_count=5,
        remaining_after_self=3,
        is_late_speaker=False,
        is_last_speaker=False,
        responsibility="INDEPENDENT_ANALYSIS",
    )
    output = SpeechOutput(
        speech=(
            "p1 的判断缺少事实支撑，我目前重点核验 p3。"
            "我会仔细分析每个人的发言逻辑，找矛盾点。"
        ),
        public_reason="给出当前关注对象。",
        cited_player_ids=["p1"],
        focus_player_ids=["p3"],
        key_conflicts=[],
        vote_intent="undecided",
        vote_target_id=None,
        fallback_target_id=None,
        decision_condition="如果 p3 无法解释公开矛盾，我会形成明确票向。",
    )

    with pytest.raises(StructuredOutputError, match="SPEECH_REPEATED_ENDING"):
        validate_speech_quality(
            output,
            discussion_context=context,
            previous_speeches=[
                "前面的信息还不完整。我会仔细分析每个人的发言逻辑，找矛盾点。"
            ],
            legal_target_ids={"p1", "p3", "p4", "p5"},
            player_aliases=PLAYER_ALIASES,
        )


def test_optional_conflicts_reject_punctuation_only_items() -> None:
    payload = _valid_final_output()
    payload["key_conflicts"] = ["。"]
    output = SpeechOutput.model_validate(payload)

    with pytest.raises(StructuredOutputError) as exc_info:
        validate_speech_quality(
            output,
            discussion_context=_final_context(),
            previous_speeches=[],
            legal_target_ids={"p1", "p2"},
            player_aliases=PLAYER_ALIASES,
        )

    error = str(exc_info.value)
    assert "SPEECH_LOW_INFORMATION_CONFLICT" in error


def test_completed_comparison_with_first_then_second_wording_is_not_deferred() -> None:
    payload = _valid_final_output()
    payload["speech"] = (
        "先看 p1 给出的公开证据，再看 p2 没有回应关键质疑；"
        "比较完成后，我明确归票给 p2。"
    )
    output = SpeechOutput.model_validate(payload)

    validate_speech_quality(
        output,
        discussion_context=_final_context(),
        previous_speeches=[],
        legal_target_ids={"p1", "p2"},
        player_aliases=PLAYER_ALIASES,
    )


@pytest.mark.asyncio
async def test_gateway_does_not_repair_semantic_validation_failure() -> None:
    repeated = _valid_final_output()
    repeated["speech"] = (
        "p1 的判断有公开依据，p2 的理由与结论没有闭合，我明确归票给 p2。"
        "我会仔细分析每个人的发言逻辑，找矛盾点。"
    )
    provider = FakeProvider([repeated, _valid_final_output()])
    gateway = AgentGateway(provider, "fake-model")
    state = {
        "id": "speech-repair-game",
        "day": 1,
        "phase": "DAY_DISCUSSION",
        "ruleset": {"version": "classic-9-hunter-v1"},
        "players": [
            {"id": "p1", "seat": 1, "name": "一号", "alive": True},
            {"id": "p2", "seat": 2, "name": "二号", "alive": True},
            {"id": "p3", "seat": 3, "name": "三号", "alive": True},
        ],
        "role_assignments": {"p1": "VILLAGER", "p2": "VILLAGER", "p3": "VILLAGER"},
    }
    context = _final_context()
    view = PlayerViewBuilder().build(
        state,
        "p3",
        allowed_action="speech",
        legal_targets=["p1", "p2"],
        discussion_context=context,
    )

    with pytest.raises(OutputValidationError, match="SPEECH_REPEATED_ENDING"):
        await gateway.generate_action(
            view,
            call_type="speech",
            output_model=SpeechOutput,
            instruction="完成末位总结并明确归票。",
            temperature=0.55,
            output_validator=partial(
                validate_speech_quality,
                discussion_context=context,
                previous_speeches=[
                    "前位已经给出判断。我会仔细分析每个人的发言逻辑，找矛盾点。"
                ],
                legal_target_ids={"p1", "p2"},
                player_aliases=PLAYER_ALIASES,
            ),
        )

    assert len(provider.requests) == 1
    assert provider.requests[0].temperature == 0.55
    assert provider.requests[0].metadata["original_call_type"] == "speech"
    assert provider.requests[0].metadata["call_type"] == "speech"
    assert gateway.session_history(view.session_id) == []


def test_structured_speech_fields_are_emitted_in_public_event() -> None:
    engine = GameEngine()
    game = engine.create_game(
        [f"玩家{seat}" for seat in range(1, 10)],
        config=GameConfig(
            random_seed=73,
            ruleset=Ruleset.classic_9_hunter_v1(),
        ),
        game_id="structured-speech-event",
    ).game
    game = engine.start_game(game, "start").game
    game.day = 1
    game.phase = GamePhase.DAY_DISCUSSION
    actor_id = game.alive_players()[0].id
    focus_id = game.alive_players()[1].id
    output = SpeechOutput(
        speech=f"我当前重点核验 {focus_id} 的公开逻辑。",
        public_reason="形成可追踪的公开判断。",
        cited_player_ids=[],
        focus_player_ids=[focus_id],
        key_conflicts=["身份收益与公开结论需要对应"],
        vote_intent="undecided",
        vote_target_id=None,
        fallback_target_id=None,
        decision_condition=f"如果 {focus_id} 无法解释该问题，我会投给该目标。",
    )

    action = GameApplicationService._speech_action(actor_id, output)
    result = engine.submit_speech(game, action, "structured-speech")
    payload = result.events[-1].payload

    assert payload["focus_player_ids"] == [focus_id]
    assert payload["key_conflicts"] == ["身份收益与公开结论需要对应"]
    assert payload["vote_intent"] == "undecided"
    assert payload["decision_condition"] == output.decision_condition
    assert "private_summary" not in payload
