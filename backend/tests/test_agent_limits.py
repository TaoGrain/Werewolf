from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from app.agents.gateway import AgentGateway
from app.agents.providers import LLMResponse
from app.agents.schemas import StructuredOutputError, TargetOutput
from app.agents.views import PlayerView
from app.config import Settings


def make_view(suffix: str = "1") -> PlayerView:
    return PlayerView(
        session_id=f"game:game-{suffix}:player:p1",
        game_id=f"game-{suffix}",
        player_id="p1",
        day=1,
        phase="NIGHT_SEER",
        rules={"version": "classic-9-hunter-v1"},
        players=[
            {"id": "p1", "seat": 1, "name": "one", "alive": True},
            {"id": "p2", "seat": 2, "name": "two", "alive": True},
        ],
        role_card={"role": "SEER"},
        allowed_action="seer_check",
        legal_targets=["p2"],
    )


class TrackingProvider:
    name = "tracking"
    adapter_version = "test-v1"
    max_retries = 0

    def __init__(self, *, delay: float = 0.0, text: str | None = None) -> None:
        self.delay = delay
        self.text = text or json.dumps({"target_id": "p2", "private_reason": "ok"})
        self.requests = []
        self.active = 0
        self.max_active = 0

    async def generate(self, request):
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return LLMResponse(text=self.text)
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_gateways_for_same_provider_share_one_concurrency_limit():
    provider = TrackingProvider(delay=0.02)
    first = AgentGateway(provider, "model-a", provider_concurrency_limit=2)
    second = AgentGateway(provider, "model-b", provider_concurrency_limit=2)

    await asyncio.gather(
        *(
            (first if index % 2 == 0 else second).generate_action(
                make_view(str(index)),
                call_type="seer_check",
                output_model=TargetOutput,
                instruction="check",
            )
            for index in range(8)
        )
    )

    assert provider.max_active == 2
    assert first._provider_limiter is second._provider_limiter
    with pytest.raises(ValueError):
        AgentGateway(provider, "model-c", provider_concurrency_limit=3)


@pytest.mark.asyncio
async def test_total_attempt_budget_caps_format_repairs_after_provider_retries():
    provider = TrackingProvider(text="not-json")
    provider.max_retries = 2
    gateway = AgentGateway(
        provider,
        "model",
        max_total_attempts=6,
    )

    with pytest.raises(StructuredOutputError):
        await gateway.generate_action(
            make_view(),
            call_type="seer_check",
            output_model=TargetOutput,
            instruction="check",
            max_repair_attempts=99,
        )

    assert len(provider.requests) == 2
    assert provider.requests[-1].metadata["gateway_max_provider_calls"] == 2
    assert provider.requests[-1].metadata["provider_attempts_per_call"] == 3
    assert provider.requests[-1].metadata["gateway_max_total_attempts"] == 6


@pytest.mark.asyncio
async def test_history_window_keeps_recent_context_and_sessions_separate():
    provider = TrackingProvider()
    gateway = AgentGateway(
        provider,
        "model",
        history_max_messages=2,
        history_max_characters=500,
    )
    view = make_view("history")

    for _ in range(3):
        await gateway.generate_action(
            view,
            call_type="seer_check",
            output_model=TargetOutput,
            instruction="check",
        )

    history = gateway.session_history(view.session_id)
    assert len(history) == 2
    assert sum(len(message.content) for message in history) <= 500
    assert history[-1].content == provider.text
    assert len(provider.requests[-1].messages) == 4
    assert gateway.session_history("game:other:player:p1") == []


def test_limit_configuration_validation_and_legacy_constructor_compatibility():
    legacy = AgentGateway(TrackingProvider(), "legacy-model")
    assert legacy.max_total_attempts == 9
    assert legacy.max_provider_calls == 9

    with pytest.raises(ValueError, match="provider_concurrency_limit"):
        AgentGateway(TrackingProvider(), "model", provider_concurrency_limit=0)

    retrying_provider = TrackingProvider()
    retrying_provider.max_retries = 2
    with pytest.raises(ValueError, match="one provider call"):
        AgentGateway(
            retrying_provider,
            "model",
            max_total_attempts=1,
        )

    with pytest.raises(ValidationError, match="must cover one provider call"):
        Settings(
            llm_provider_max_retries=2,
            llm_gateway_max_total_attempts=2,
        )
