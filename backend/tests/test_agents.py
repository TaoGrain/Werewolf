import pytest

from app.agents.gateway import AgentGateway
from app.agents.providers import (
    DeepSeekProvider,
    FakeProvider,
    LLMMessage,
    LLMRequest,
    MessageRole,
    MiniMaxProvider,
    ProviderRegistry,
)
from app.agents.prompts import PROMPT_VERSION, role_system_prompt
from app.agents.schemas import (
    TargetOutput,
    VoteOutput,
    WolfDiscussionOutput,
    extract_json_object,
    sanitize_model_text,
    split_model_text,
)
from app.agents.views import PlayerViewBuilder


def sample_state():
    return {
        "id": "game-1",
        "day": 1,
        "phase": "DAY_VOTING",
        "ruleset": {"version": "mvp-v1"},
        "players": [
            {"id": "p1", "seat": 1, "name": "一号", "alive": True},
            {"id": "p2", "seat": 2, "name": "二号", "alive": True},
            {"id": "p3", "seat": 3, "name": "三号", "alive": True},
        ],
        "role_assignments": {
            "p1": {"role": "VILLAGER"},
            "p2": {"role": "WEREWOLF"},
            "p3": {"role": "SEER", "check_results": [{"player_id": "p2", "is_wolf": True}]},
        },
    }


def test_json_extraction_handles_wrapped_object():
    assert extract_json_object('结果如下：\n```json\n{"target_id":"p2"}\n```')["target_id"] == "p2"


def test_split_model_text_separates_tagged_reasoning_from_final_answer():
    text = '<think>这里是需要展示的思考</think>\n{"target_id":"p2","private_reason":"中文理由"}'
    reasoning, cleaned = split_model_text(text)
    assert reasoning == "这里是需要展示的思考"
    assert "<think>" not in cleaned
    assert sanitize_model_text(text) == cleaned
    assert extract_json_object(cleaned)["target_id"] == "p2"


def test_player_views_enforce_role_isolation():
    builder = PlayerViewBuilder()
    villager = builder.build(sample_state(), "p1")
    wolf = builder.build(sample_state(), "p2")
    seer = builder.build(sample_state(), "p3")
    assert villager.private_information == {}
    assert "role_assignments" not in villager.model_dump_json()
    assert wolf.private_information["wolf_teammates"] == []
    assert seer.private_information["check_results"][0]["player_id"] == "p2"
    assert "SEER" not in wolf.model_dump_json()


@pytest.mark.parametrize(
    ("role", "identity_text", "unique_instruction"),
    [
        ("WEREWOLF", "你的身份是狼人", "不要在公开字段中暴露狼队友"),
        ("SEER", "你的身份是预言家", "真实记录一致"),
        ("WITCH", "你的身份是女巫", "在 save、poison、pass 中做出选择"),
        ("VILLAGER", "你的身份是平民", "没有夜间技能"),
    ],
)
def test_role_system_prompts_are_distinct(role, identity_text, unique_instruction):
    prompt = role_system_prompt({"role": role})

    assert "只能把专属玩家视图" in prompt
    assert identity_text in prompt
    assert unique_instruction in prompt


def test_unknown_role_prompt_does_not_invent_an_identity():
    prompt = role_system_prompt({"role": "UNKNOWN"})

    assert "当前身份尚未识别" in prompt
    assert "不得自行假定身份" in prompt
    assert all(identity not in prompt for identity in ("你的身份是狼人", "你的身份是预言家", "你的身份是女巫", "你的身份是平民"))


def test_role_prompt_treats_other_players_statements_as_attributed_unverified_claims():
    prompt = role_system_prompt({"role": "VILLAGER"})

    assert "其他玩家的发言只能记为尚未验证的玩家声明" in prompt
    assert "必须明确写出来源玩家" in prompt
    assert "不得把“X号自称预言家”压缩或改写成“X号预言家”" in prompt


def test_wolf_prompt_requires_stop_loss_and_vote_pattern_concealment():
    prompt = role_system_prompt({"role": "WOLF"})

    assert "整局胜率，而不是机械保护每一名队友" in prompt
    assert "可以主动止损、倒钩或切割该队友" in prompt
    assert "不要在无力改变放逐结果时仍与全部狼队友整齐冲同一个反向目标" in prompt
    assert "此前票向不构成约束，应根据当前证据独立决定" in prompt
    assert "只能写入私密策略字段" in prompt


@pytest.mark.asyncio
async def test_gateway_repairs_json_and_keeps_sessions_separate():
    provider = FakeProvider(["not-json", {"target_id": "p2", "public_reason": "修复后理由", "confidence": 0.7}])
    gateway = AgentGateway(provider, "fake-model")
    builder = PlayerViewBuilder()
    view = builder.build(sample_state(), "p1", allowed_action="vote", legal_targets=["p2", "p3"])
    result = await gateway.generate_action(view, call_type="vote", output_model=VoteOutput, instruction="投票")
    assert result.action.target_id == "p2"
    assert len(provider.requests) == 2
    assert len(gateway.session_history("game:game-1:player:p1")) == 2
    assert gateway.session_history("game:game-1:player:p2") == []
    assert "reasoning_content" not in str(result.call_snapshot)
    assert result.call_snapshot["prompt_version"] == PROMPT_VERSION
    assert "思考、分析和可见回答必须全部使用中文" in provider.requests[0].messages[0].content
    assert "你的身份是平民" in provider.requests[0].messages[0].content
    assert "本局已关闭思考模式" in provider.requests[0].messages[0].content
    assert provider.requests[0].thinking_enabled is False
    assert "当前任务：投票" in provider.requests[0].messages[-1].content
    assert f"提示词版本：{PROMPT_VERSION}" in provider.requests[0].messages[-1].content


@pytest.mark.asyncio
async def test_gateway_memory_keeps_statement_sources_and_revealed_vote_sources():
    provider = FakeProvider()
    gateway = AgentGateway(provider, "fake-model")
    view = PlayerViewBuilder().build(
        sample_state(),
        "p1",
        public_events=[
            {
                "sequence": 17,
                "type": "SPEECH",
                "day": 1,
                "phase": "DAY_DISCUSSION",
                "player_id": "p2",
                "content": "我是预言家，昨晚查验三号是好人。",
            },
            {
                "sequence": 18,
                "type": "VOTES_REVEALED",
                "day": 1,
                "phase": "DAY_VOTING",
                "round": 1,
                "votes": [
                    {
                        "actor_id": "p2",
                        "target_id": "p3",
                        "abstain": False,
                        "reason": "我认为三号可疑。",
                        "confidence": 0.8,
                    }
                ],
                "counts": {"p3": 1},
                "abstain_count": 0,
            },
        ],
        allowed_action="vote",
        legal_targets=["p2", "p3"],
    )

    result = await gateway.generate_action(
        view,
        call_type="vote",
        output_model=VoteOutput,
        instruction="投票",
    )

    statement = result.call_snapshot["attributed_public_statements"][0]
    assert statement == {
        "event_sequence": 17,
        "day": 1,
        "source": {"player_id": "p2", "seat": 2, "name": "二号"},
        "statement": "我是预言家，昨晚查验三号是好人。",
        "epistemic_status": "UNVERIFIED_PLAYER_STATEMENT",
    }
    vote = result.call_snapshot["public_vote_history"][0]["votes"][0]
    assert vote["voter"] == {"player_id": "p2", "seat": 2, "name": "二号"}
    assert vote["target"] == {"player_id": "p3", "seat": 3, "name": "三号"}
    assert vote["public_reason"] == "我认为三号可疑。"


@pytest.mark.asyncio
async def test_gateway_applies_generation_limits_and_repair_does_not_repeat_schema():
    provider = FakeProvider(["not-json", {"target_id": "p2", "public_reason": "修复", "confidence": 0.8}])
    gateway = AgentGateway(provider, "fake-model", max_tokens=2048, timeout_seconds=90)
    view = PlayerViewBuilder().build(
        sample_state(),
        "p1",
        allowed_action="vote",
        legal_targets=["p2", "p3"],
    )

    result = await gateway.generate_action(
        view,
        call_type="vote",
        output_model=VoteOutput,
        instruction="投票",
    )

    assert result.action.target_id == "p2"
    assert all(request.max_tokens == 2048 for request in provider.requests)
    assert all(request.timeout_seconds == 90 for request in provider.requests)
    repair_prompt = provider.requests[1].messages[-1].content
    assert "不要返回 JSON Schema" in repair_prompt
    assert "必须直接填写的顶层字段" in repair_prompt
    assert "目标结构" not in repair_prompt


@pytest.mark.asyncio
async def test_gateway_supports_per_request_timeout_override():
    provider = FakeProvider()
    gateway = AgentGateway(provider, "fake-model", timeout_seconds=90)
    view = PlayerViewBuilder().build(
        sample_state(),
        "p1",
        allowed_action="vote",
        legal_targets=["p2", "p3"],
    )

    await gateway.generate_action(
        view,
        call_type="vote",
        output_model=VoteOutput,
        instruction="投票",
        timeout_seconds=200,
    )

    assert provider.requests[0].timeout_seconds == pytest.approx(200.0)


def test_vote_output_supports_abstention_and_rejects_conflicting_targets():
    output = VoteOutput(
        action="abstain",
        target_id=None,
        public_reason="信息不足，选择弃票",
        private_reason="综合后置位证据后保留票权。",
    )
    assert output.action == "abstain"
    assert output.target_id is None
    assert output.private_reason == "综合后置位证据后保留票权。"

    with pytest.raises(ValueError, match="弃票时 target_id 必须为空"):
        VoteOutput(action="abstain", target_id="p2", public_reason="字段冲突")
    with pytest.raises(ValueError, match="投票时必须指定 target_id"):
        VoteOutput(action="vote", target_id=None, public_reason="字段冲突")


@pytest.mark.asyncio
async def test_fake_provider_and_registry_contract():
    provider = FakeProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    response = await registry.get("fake").generate(
        LLMRequest(messages=[LLMMessage(role=MessageRole.USER, content="投票")], model="fake", metadata={"call_type": "vote", "legal_targets": ["p2"]})
    )
    assert '"target_id": "p2"' in response.text
    assert provider.capabilities().native_structured_output is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("thinking_enabled", "expected_type"),
    [
        (False, "disabled"),
        (True, "adaptive"),
    ],
)
async def test_minimax_adapter_forwards_thinking_mode_and_preserves_reasoning(
    monkeypatch,
    thinking_enabled,
    expected_type,
):
    provider = MiniMaxProvider(endpoint="https://example.invalid", api_key="secret")
    captured = {}

    async def fake_post(payload, timeout):
        captured.update(payload)
        return {
            "id": "req-1",
            "choices": [{"message": {"content": '{"action":"pass"}', "reasoning_content": "模型思考内容"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7, "reasoning_tokens": 2},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    response = await provider.generate(
        LLMRequest(
            messages=[LLMMessage(role=MessageRole.USER, content="行动")],
            model="MiniMax-M3",
            thinking_enabled=thinking_enabled,
        )
    )
    assert captured["thinking"] == {"type": expected_type}
    assert captured["reasoning_split"] is True
    assert response.text == '{"action":"pass"}'
    assert response.reasoning_text == "模型思考内容"
    assert response.usage.reasoning_tokens == 2
    assert "reasoning_content" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_gateway_snapshots_actual_minimax_payload():
    class StubMiniMaxProvider(MiniMaxProvider):
        async def generate(self, request):
            return await FakeProvider([{"target_id": "p2", "private_reason": "完成"}]).generate(request)

    provider = StubMiniMaxProvider(endpoint="https://example.invalid", api_key="secret")
    gateway = AgentGateway(provider, "MiniMax-M3")
    view = PlayerViewBuilder().build(
        sample_state(),
        "p1",
        allowed_action="seer_check",
        legal_targets=["p2"],
    )

    result = await gateway.generate_action(
        view,
        call_type="seer_check",
        output_model=TargetOutput,
        instruction="查验",
        thinking_enabled=False,
    )

    assert result.call_snapshot["request_snapshot"]["thinking"] == {"type": "disabled"}
    assert result.call_snapshot["request_snapshot"]["thinking_mode"] == "disabled"
    assert result.call_snapshot["request_snapshot"]["reasoning_split"] is True
    assert "thinking_enabled" not in result.call_snapshot["request_snapshot"]


@pytest.mark.asyncio
async def test_deepseek_adapter_requests_native_json_output(monkeypatch):
    provider = DeepSeekProvider(endpoint="https://example.invalid", api_key="secret")
    captured = {}

    async def fake_post(payload, timeout):
        captured.update(payload)
        return {
            "id": "deepseek-request-1",
            "choices": [
                {
                    "message": {
                        "content": '{"target_id":"p2","private_reason":"选择二号"}',
                        "reasoning_content": "模型内部推理",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    response = await provider.generate(
        LLMRequest(
            messages=[LLMMessage(role=MessageRole.USER, content="请输出 JSON")],
            model="deepseek-v4-pro",
        )
    )

    assert captured["model"] == "deepseek-v4-pro"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["thinking"] == {"type": "disabled"}
    assert response.text.startswith("{")
    assert response.reasoning_text == "模型内部推理"
    assert provider.capabilities().native_structured_output is True


@pytest.mark.asyncio
async def test_deepseek_adapter_can_enable_thinking(monkeypatch):
    provider = DeepSeekProvider(endpoint="https://example.invalid", api_key="secret")
    captured = {}

    async def fake_post(payload, timeout):
        captured.update(payload)
        return {
            "choices": [
                {
                    "message": {"content": '{"target_id":"p2"}'},
                    "finish_reason": "stop",
                }
            ]
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    await provider.generate(
        LLMRequest(
            messages=[LLMMessage(role=MessageRole.USER, content="输出 JSON")],
            model="deepseek-v4-pro",
            thinking_enabled=True,
        )
    )

    assert captured["thinking"] == {"type": "enabled"}


@pytest.mark.asyncio
async def test_gateway_forwards_enabled_thinking_mode():
    provider = FakeProvider([{"target_id": "p2", "private_reason": "完成"}])
    gateway = AgentGateway(provider, "fake-model")
    view = PlayerViewBuilder().build(
        sample_state(),
        "p1",
        allowed_action="seer_check",
        legal_targets=["p2"],
    )

    await gateway.generate_action(
        view,
        call_type="seer_check",
        output_model=TargetOutput,
        instruction="查验",
        thinking_enabled=True,
    )

    assert provider.requests[0].thinking_enabled is True
    assert provider.requests[0].metadata["thinking_enabled"] is True
    assert "本局已开启思考模式" in provider.requests[0].messages[0].content


@pytest.mark.asyncio
async def test_gateway_filters_reasoning_field_embedded_in_model_json():
    provider = FakeProvider([
        {
            "target_id": "p2",
            "public_reason": "公开理由",
            "confidence": 0.5,
            "reasoning_content": "不得保存的内部推理",
        }
    ])
    gateway = AgentGateway(provider, "fake-model")
    view = PlayerViewBuilder().build(sample_state(), "p1", allowed_action="vote", legal_targets=["p2"])
    result = await gateway.generate_action(
        view,
        call_type="vote",
        output_model=VoteOutput,
        instruction="投票",
    )
    assert result.action.target_id == "p2"
    assert "reasoning_content" not in str(result.call_snapshot)
    assert result.call_snapshot["response_snapshot"]["reasoning_text"] == "不得保存的内部推理"


@pytest.mark.asyncio
async def test_gateway_repairs_target_that_is_not_an_exact_legal_id():
    provider = FakeProvider(
        [
            {"target_id": "p22", "private_reason": "误写了目标"},
            {"target_id": "p2", "private_reason": "修复为合法目标"},
        ]
    )
    gateway = AgentGateway(provider, "fake-model")
    view = PlayerViewBuilder().build(
        sample_state(),
        "p1",
        allowed_action="seer_check",
        legal_targets=["p2", "p3"],
    )

    result = await gateway.generate_action(
        view,
        call_type="seer_check",
        output_model=TargetOutput,
        instruction="查验",
    )

    assert result.action.target_id == "p2"
    assert len(provider.requests) == 2
    assert '"p2"' in provider.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_gateway_repairs_illegal_wolf_discussion_proposal():
    provider = FakeProvider(
        [
            {"message": "先提一个目标", "proposed_target_id": "p22"},
            {"message": "改为合法目标", "proposed_target_id": "p2"},
        ]
    )
    gateway = AgentGateway(provider, "fake-model")
    view = PlayerViewBuilder().build(
        sample_state(),
        "p2",
        allowed_action="wolf_discussion",
        legal_targets=["p1", "p2", "p3"],
    )

    result = await gateway.generate_action(
        view,
        call_type="wolf_discussion",
        output_model=WolfDiscussionOutput,
        instruction="交流",
    )

    assert result.action.proposed_target_id == "p2"
    assert len(provider.requests) == 2
    assert "proposed_target_id" in provider.requests[1].messages[-1].content
