from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents import AgentGateway
from app.agents.providers import FakeProvider
from app.domain import GamePhase
from app.engine import GameEngine
from app.persistence import Base, Database, Repository
from app.services import DatabaseCallRecorder, GameApplicationService


class SlowProvider(FakeProvider):
    async def generate(self, request):
        self.requests.append(request)
        await asyncio.sleep(0.1)
        return await FakeProvider().generate(request)


class FailOnceVoteProvider(FakeProvider):
    def __init__(self, failed_player_id: str) -> None:
        super().__init__()
        self.failed_player_id = failed_player_id
        self.failed = False
        self.called_player_ids: list[str] = []

    async def generate(self, request):
        player_id = str(request.metadata.get("player_id"))
        self.called_player_ids.append(player_id)
        if player_id == self.failed_player_id and not self.failed:
            self.failed = True
            raise RuntimeError("planned single-player failure")
        return await super().generate(request)


class TrackingVoteProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.called_player_ids: list[str] = []

    async def generate(self, request):
        self.called_player_ids.append(str(request.metadata.get("player_id")))
        return await super().generate(request)


def make_service(
    database: Database,
    provider: FakeProvider,
    *,
    timeout: float = 120.0,
    token_budget: int = 1000000,
    max_total_attempts: int = 9,
) -> tuple[GameApplicationService, AgentGateway]:
    gateway = AgentGateway(
        provider,
        "fake-model",
        DatabaseCallRecorder(database),
        max_total_attempts=max_total_attempts,
    )
    return (
        GameApplicationService(
            database,
            GameEngine(),
            gateway,
            phase_timeout_seconds=timeout,
            phase_output_token_budget=token_budget,
        ),
        gateway,
    )


@pytest.mark.asyncio
async def test_vote_reconsiders_late_evidence_and_uses_dedicated_temperature(
    tmp_path: Path,
) -> None:
    database = Database.sqlite(tmp_path / "vote-strategy.db")
    Base.metadata.create_all(database.engine)
    game_id = await prepare_day_voting(database, vote_temperature=0.47)
    provider = TrackingVoteProvider()
    gateway = AgentGateway(provider, "fake-model", DatabaseCallRecorder(database))
    service = GameApplicationService(database, GameEngine(), gateway)

    result = await service.advance_now(game_id, "strategic-vote")

    assert result.status == "SUCCEEDED"
    vote_requests = [
        request
        for request in provider.requests
        if request.metadata.get("call_type") == "vote"
    ]
    assert vote_requests
    assert all(request.temperature == pytest.approx(0.47) for request in vote_requests)
    assert all(request.thinking_enabled is True for request in vote_requests)
    assert all(
        request.timeout_seconds == pytest.approx(200.0)
        for request in vote_requests
    )
    task = vote_requests[0].messages[-1].content
    assert "投票前必须重新评估全部公开发言" in task
    assert "此前的暂定票向不构成约束，应根据当前证据独立决定" in task
    assert "先在 private_reason 中比较当前选择的阵营长期收益" in task


@pytest.mark.asyncio
async def test_vote_uses_200_second_phase_timeout_even_when_default_is_short(
    tmp_path: Path,
) -> None:
    database = Database.sqlite(tmp_path / "vote-timeout.db")
    Base.metadata.create_all(database.engine)
    game_id = await prepare_day_voting(database)
    provider = SlowProvider()
    service, _ = make_service(database, provider, timeout=0.01)

    result = await service.advance_now(game_id, "slow-vote")

    assert result.status == "SUCCEEDED"
    vote_requests = [
        request
        for request in provider.requests
        if request.metadata.get("call_type") == "vote"
    ]
    assert vote_requests
    assert all(request.thinking_enabled is True for request in vote_requests)
    assert all(
        request.timeout_seconds == pytest.approx(200.0)
        for request in vote_requests
    )


async def prepare_night_seer(service: GameApplicationService) -> str:
    game = service.create_game(random_seed=42)
    service.start_game(game.id, "start")
    entered = await service.advance_now(game.id, "enter-night")
    assert entered.status == "SUCCEEDED"
    assert service.load_game(game.id).phase == GamePhase.NIGHT_SEER
    return game.id


async def prepare_day_voting(
    database: Database,
    *,
    ruleset_version: str = "mvp-v1",
    vote_temperature: float | None = None,
) -> str:
    bootstrap, _ = make_service(database, FakeProvider())
    game = bootstrap.create_game(
        random_seed=17,
        ruleset_version=ruleset_version,
        vote_temperature=vote_temperature,
    )
    bootstrap.start_game(game.id, "start")
    for index in range(40):
        if bootstrap.load_game(game.id).phase == GamePhase.DAY_VOTING:
            break
        status = await bootstrap.advance_now(game.id, f"prepare-{index}")
        assert status.status == "SUCCEEDED"
    assert bootstrap.load_game(game.id).phase == GamePhase.DAY_VOTING
    return game.id


@pytest.mark.asyncio
async def test_phase_timeout_marks_command_as_retryable_failure(tmp_path: Path):
    database = Database.sqlite(tmp_path / "timeout.db")
    Base.metadata.create_all(database.engine)
    provider = SlowProvider()
    service, _ = make_service(database, provider, timeout=0.01)
    game_id = await prepare_night_seer(service)

    status = await service.advance_now(game_id, "slow-seer")

    assert status.status == "FAILED"
    assert status.result == {
        "type": "PhaseAdvanceTimeoutError",
        "message": "阶段推进超过 0.01 秒总超时",
        "retryable": True,
    }
    assert service.load_game(game_id).phase == GamePhase.NIGHT_SEER
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_insufficient_phase_budget_fails_before_provider_call(tmp_path: Path):
    database = Database.sqlite(tmp_path / "budget-too-small.db")
    Base.metadata.create_all(database.engine)
    provider = FakeProvider()
    service, _ = make_service(database, provider, token_budget=8)
    game_id = await prepare_night_seer(service)

    status = await service.advance_now(game_id, "budget-failure")

    assert status.status == "FAILED"
    assert status.result["type"] == "PhaseTokenBudgetError"
    assert status.result["retryable"] is True
    assert provider.requests == []
    assert service.load_game(game_id).phase == GamePhase.NIGHT_SEER


@pytest.mark.asyncio
async def test_phase_budget_bounds_all_planned_vote_attempts(tmp_path: Path):
    database = Database.sqlite(tmp_path / "budget-bound.db")
    Base.metadata.create_all(database.engine)
    game_id = await prepare_day_voting(database)
    provider = TrackingVoteProvider()
    preliminary, gateway = make_service(
        database,
        provider,
        max_total_attempts=2,
    )
    eligible_count = len(preliminary._eligible_voters(preliminary.load_game(game_id)))
    per_request_tokens = 7
    budget = eligible_count * gateway.max_provider_calls * per_request_tokens
    service, gateway = make_service(
        database,
        provider,
        token_budget=budget,
        max_total_attempts=2,
    )

    status = await service.advance_now(game_id, "bounded-vote")

    assert status.status == "SUCCEEDED"
    assert len(provider.requests) == eligible_count
    assert {request.max_tokens for request in provider.requests} == {per_request_tokens}
    assert (
        eligible_count * gateway.max_provider_calls * max(r.max_tokens for r in provider.requests)
        <= budget
    )


@pytest.mark.asyncio
async def test_vote_checkpoint_keeps_successes_and_retry_calls_only_failure(tmp_path: Path):
    database = Database.sqlite(tmp_path / "vote-checkpoint.db")
    Base.metadata.create_all(database.engine)
    game_id = await prepare_day_voting(database)
    before = GameApplicationService(
        database,
        GameEngine(),
        AgentGateway(FakeProvider(), "unused"),
    ).load_game(game_id)
    eligible_ids = [player.id for player in before.alive_players()]
    failed_player_id = eligible_ids[1]
    provider = FailOnceVoteProvider(failed_player_id)
    service, _ = make_service(database, provider)

    failed = await service.advance_now(game_id, "partial-vote")

    assert failed.status == "FAILED"
    assert failed.result["type"] == "PhaseAgentActionError"
    checkpoint = service.load_game(game_id)
    assert checkpoint.phase == GamePhase.DAY_VOTING
    assert set(checkpoint.votes) == set(eligible_ids) - {failed_player_id}
    with database.session() as session:
        repo = Repository(session)
        assert len(repo.list_votes(game_id, day=checkpoint.day)) == len(eligible_ids) - 1
        assert len(
            [action for action in repo.list_phase_actions(game_id) if action.action_type == "vote"]
        ) == len(eligible_ids) - 1

    first_call_count = len(provider.called_player_ids)
    retried = await service.advance_now(game_id, "retry-partial-vote")

    assert retried.status == "SUCCEEDED"
    assert provider.called_player_ids[first_call_count:] == [failed_player_id]


@pytest.mark.asyncio
async def test_runoff_candidates_are_not_called_or_allowed_to_revote(tmp_path: Path):
    database = Database.sqlite(tmp_path / "runoff-voters.db")
    Base.metadata.create_all(database.engine)
    game_id = await prepare_day_voting(
        database, ruleset_version="classic-9-hunter-v1"
    )
    bootstrap, _ = make_service(database, FakeProvider())
    game = bootstrap.load_game(game_id)
    alive_ids = [player.id for player in game.alive_players()]
    candidate_ids = alive_ids[:2]
    game.vote_round = 2
    game.runoff_candidate_ids = candidate_ids
    game.votes = {}
    with database.session() as session:
        repo = Repository(session)
        db_game = repo.require_game(game_id)
        repo.update_game_state(
            game_id,
            game.model_dump(mode="json"),
            phase=GamePhase.DAY_VOTING.value,
            day=game.day,
            expected_version=db_game.version,
        )

    provider = TrackingVoteProvider()
    service, _ = make_service(database, provider)
    status = await service.advance_now(game_id, "runoff-vote")

    assert status.status == "SUCCEEDED"
    assert set(provider.called_player_ids) == set(alive_ids) - set(candidate_ids)
    assert not (set(provider.called_player_ids) & set(candidate_ids))
