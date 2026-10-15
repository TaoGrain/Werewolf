from pathlib import Path
import asyncio
import threading

import pytest

from app.agents import AgentGateway
from app.agents.providers import FakeProvider
from app.agents.prompts import PROMPT_VERSION
from app.domain import EventType, GameEvent, GamePhase, Visibility
from app.engine import GameEngine
from app.persistence import Base, Database, Repository
from app.services import DatabaseCallRecorder, GameApplicationService


class AbstainingFakeProvider(FakeProvider):
    @staticmethod
    def _default_payload(request):
        if request.metadata.get("call_type") == "vote":
            return {
                "action": "abstain",
                "target_id": None,
                "public_reason": "当前信息不足，选择弃票。",
                "confidence": 0.2,
            }
        return FakeProvider._default_payload(request)


@pytest.mark.asyncio
async def test_fake_provider_complete_game_and_audit(tmp_path: Path):
    database = Database.sqlite(tmp_path / "game.db")
    Base.metadata.create_all(database.engine)
    gateway = AgentGateway(FakeProvider(), "fake-model", DatabaseCallRecorder(database))
    service = GameApplicationService(database, GameEngine(), gateway)
    game = service.create_game(random_seed=42)
    service.start_game(game.id, "start")

    for index in range(100):
        current = service.load_game(game.id)
        if current.phase.value == "FINISHED":
            break
        result = await service.advance_now(game.id, f"advance-{index}")
        assert result.status == "SUCCEEDED"

    finished = service.load_game(game.id)
    assert finished.phase.value == "FINISHED"
    assert finished.winner is not None
    with database.session() as session:
        repo = Repository(session)
        calls = repo.list_llm_calls(game.id)
        events = repo.list_events(game.id)
        memories = repo.list_memory_snapshots(game.id)
        assert calls
        assert all(call.prompt_version == PROMPT_VERSION for call in calls)
        assert memories
        assert all(
            memory.snapshot.get("private_strategy_status") == "SUBJECTIVE_AGENT_INFERENCE"
            for memory in memories
        )
        assert all("attributed_public_statements" in memory.snapshot for memory in memories)
        assert all("public_vote_history" in memory.snapshot for memory in memories)
        assert any(memory.snapshot["public_vote_history"] for memory in memories)
        assert events == sorted(events, key=lambda item: item.sequence)
        assert "reasoning_content" not in str([call.request_snapshot for call in calls])

    # 模拟服务重启：新 Gateway 能从 SQLite 恢复该玩家的独立消息历史。
    restarted_provider = FakeProvider()
    restarted_gateway = AgentGateway(restarted_provider, "fake-model", DatabaseCallRecorder(database))
    player_id = calls[0].player_id
    session_id = f"game:{game.id}:player:{player_id}"
    recovered = restarted_gateway.recorder.load_session_history(session_id)
    assert recovered
    assert all("reasoning_content" not in message.content for message in recovered)


@pytest.mark.asyncio
async def test_all_agent_abstentions_advance_directly_and_keep_original_vote_day(tmp_path: Path):
    database = Database.sqlite(tmp_path / "abstain.db")
    Base.metadata.create_all(database.engine)
    gateway = AgentGateway(
        AbstainingFakeProvider(),
        "fake-model",
        DatabaseCallRecorder(database),
    )
    service = GameApplicationService(database, GameEngine(), gateway)
    game = service.create_game(random_seed=42)
    service.start_game(game.id, "start")

    for index in range(50):
        current = service.load_game(game.id)
        if current.day == 2:
            break
        result = await service.advance_now(game.id, f"abstain-advance-{index}")
        assert result.status == "SUCCEEDED"

    current = service.load_game(game.id)
    assert current.day == 2
    assert current.phase == GamePhase.NIGHT_SEER
    with database.session() as session:
        repo = Repository(session)
        votes = repo.list_votes(game.id)
        assert len(votes) == len([player for player in current.players if player.death_day != 1])
        assert all(vote.day == 1 for vote in votes)
        assert all(vote.abstained and vote.target_player_id is None for vote in votes)
        no_exile = next(
            event
            for event in repo.list_events(game.id)
            if event.event_type == EventType.NO_EXILE.value
        )
        assert no_exile.payload == {"reason": "所有存活玩家均弃票"}


@pytest.mark.asyncio
async def test_day_discussion_advances_one_speaker_per_command(tmp_path: Path):
    database = Database.sqlite(tmp_path / "discussion.db")
    Base.metadata.create_all(database.engine)
    service = GameApplicationService(
        database,
        GameEngine(),
        AgentGateway(FakeProvider(), "fake", DatabaseCallRecorder(database)),
    )
    game = service.create_game(random_seed=12)
    service.start_game(game.id, "start")

    for index in range(10):
        current = service.load_game(game.id)
        if current.phase.value == "DAY_DISCUSSION":
            break
        result = await service.advance_now(game.id, f"prepare-{index}")
        assert result.status == "SUCCEEDED"

    discussion = service.load_game(game.id)
    assert discussion.phase.value == "DAY_DISCUSSION"
    alive_ids = [player.id for player in discussion.alive_players()]
    assert discussion.speeches == {}

    first = await service.advance_now(game.id, "first-speaker")
    assert first.status == "SUCCEEDED"
    after_first = service.load_game(game.id)
    assert after_first.phase.value == "DAY_DISCUSSION"
    assert list(after_first.speeches) == alive_ids[:1]

    for index, player_id in enumerate(alive_ids[1:], start=2):
        result = await service.advance_now(game.id, f"speaker-{index}")
        assert result.status == "SUCCEEDED"
        current = service.load_game(game.id)
        assert current.phase.value == "DAY_DISCUSSION"
        assert list(current.speeches) == alive_ids[:index]
        assert player_id in current.speeches

    completed = await service.advance_now(game.id, "complete-discussion")
    assert completed.status == "SUCCEEDED"
    assert service.load_game(game.id).phase.value == "DAY_VOTING"


@pytest.mark.asyncio
async def test_wolf_discussion_and_kill_require_separate_advances(tmp_path: Path):
    database = Database.sqlite(tmp_path / "wolf-discussion.db")
    Base.metadata.create_all(database.engine)
    provider = FakeProvider()
    service = GameApplicationService(
        database,
        GameEngine(),
        AgentGateway(provider, "fake", DatabaseCallRecorder(database)),
    )
    game = service.create_game(
        random_seed=21,
        thinking_enabled=True,
        action_temperature=0.73,
    )
    service.start_game(game.id, "start")

    entered = await service.advance_now(game.id, "enter-seer")
    assert entered.status == "SUCCEEDED"
    assert service.load_game(game.id).phase == GamePhase.NIGHT_SEER
    assert provider.requests == []

    checked = await service.advance_now(game.id, "seer-check")
    assert checked.status == "SUCCEEDED"
    assert service.load_game(game.id).phase == GamePhase.NIGHT_WOLF_DISCUSSION
    assert [request.metadata["call_type"] for request in provider.requests] == [
        "seer_check"
    ]
    assert provider.requests[0].thinking_enabled is True

    discussed = await service.advance_now(game.id, "discuss")
    assert discussed.status == "SUCCEEDED"
    after_discussion = service.load_game(game.id)
    assert after_discussion.phase == GamePhase.NIGHT_WOLF
    assert len(after_discussion.wolf_discussion) == 2
    assert after_discussion.wolf_actions == {}
    assert [request.metadata["call_type"] for request in provider.requests[1:]] == [
        "wolf_discussion",
        "wolf_discussion",
    ]
    assert all(request.thinking_enabled is True for request in provider.requests)
    assert "我建议选择当前最有威胁的目标" in provider.requests[2].messages[-1].content

    killed = await service.advance_now(game.id, "kill")
    assert killed.status == "SUCCEEDED"
    after_kill = service.load_game(game.id)
    assert after_kill.phase == GamePhase.NIGHT_WITCH
    assert after_kill.wolf_target_id is not None
    assert [request.metadata["call_type"] for request in provider.requests[-2:]] == [
        "wolf_kill",
        "wolf_kill",
    ]
    assert all(request.temperature == pytest.approx(0.73) for request in provider.requests)


def test_agent_view_contains_revealed_vote_results(tmp_path: Path):
    database = Database.sqlite(tmp_path / "revealed-votes.db")
    Base.metadata.create_all(database.engine)
    service = GameApplicationService(
        database,
        GameEngine(),
        AgentGateway(FakeProvider(), "fake", DatabaseCallRecorder(database)),
    )
    game = service.create_game(random_seed=7)
    voter_id = game.players[0].id
    target_id = game.players[1].id
    game.events.append(
        GameEvent(
            sequence=len(game.events) + 1,
            event_id=f"{game.id}:{len(game.events) + 1}",
            type=EventType.VOTES_REVEALED,
            day=1,
            phase=GamePhase.DAY_VOTING,
            visibility=Visibility.PUBLIC,
            payload={
                "round": 1,
                "votes": [
                    {
                        "actor_id": voter_id,
                        "target_id": target_id,
                        "reason": "公开投票理由",
                        "confidence": 0.8,
                    }
                ],
                "counts": {target_id: 1},
            },
            command_id="reveal-votes",
        )
    )

    view = service._build_view(game, voter_id, "speech", [])
    revealed = next(event for event in view.public_events if event["type"] == "VOTES_REVEALED")

    assert revealed["round"] == 1
    assert revealed["votes"] == [
        {
            "actor_id": voter_id,
            "target_id": target_id,
            "reason": "公开投票理由",
            "confidence": 0.8,
        }
    ]
    assert revealed["counts"] == {target_id: 1}


@pytest.mark.asyncio
async def test_restart_recovers_orphaned_running_command(tmp_path: Path):
    database = Database.sqlite(tmp_path / "orphan.db")
    Base.metadata.create_all(database.engine)
    first = GameApplicationService(
        database,
        GameEngine(),
        AgentGateway(FakeProvider(), "fake", DatabaseCallRecorder(database)),
    )
    game = first.create_game(random_seed=3)
    first.start_game(game.id, "start")
    with database.session() as session:
        repo = Repository(session)
        command = repo.create_phase_command(
            game.id,
            "orphaned",
            command_type="advance",
            phase="ROLE_ASSIGNED",
        )
        repo.update_phase_command(command, status="RUNNING")

    restarted = GameApplicationService(
        database,
        GameEngine(),
        AgentGateway(FakeProvider(), "fake", DatabaseCallRecorder(database)),
    )
    queued = restarted.enqueue_advance(game.id, "replacement")
    assert queued.command_id == "replacement"
    await restarted._tasks[(game.id, "replacement")]
    assert restarted.command_status(game.id, "replacement").status == "SUCCEEDED"
    assert restarted.command_status(game.id, "orphaned").status == "FAILED"


@pytest.mark.asyncio
async def test_pause_is_preserved_when_inflight_phase_finishes(tmp_path: Path, monkeypatch):
    class SlowFakeProvider(FakeProvider):
        async def generate(self, request):
            await asyncio.sleep(0.03)
            return await super().generate(request)

    database = Database.sqlite(tmp_path / "pause.db")
    Base.metadata.create_all(database.engine)
    service = GameApplicationService(
        database,
        GameEngine(),
        AgentGateway(SlowFakeProvider(), "fake", DatabaseCallRecorder(database)),
    )
    game = service.create_game(random_seed=4)
    service.start_game(game.id, "start")
    await service.advance_now(game.id, "to-night")
    original_update = Repository.update_game_state
    entered_update = threading.Event()
    allow_update = threading.Event()

    def interleaved_update(repository, *args, **kwargs):
        entered_update.set()
        assert allow_update.wait(timeout=5)
        return original_update(repository, *args, **kwargs)

    monkeypatch.setattr(Repository, "update_game_state", interleaved_update)

    def pause_during_persist():
        assert entered_update.wait(timeout=5)
        service.pause_game(game.id)
        allow_update.set()

    pause_thread = threading.Thread(target=pause_during_persist)
    pause_thread.start()
    queued = service.enqueue_advance(game.id, "slow-night")
    concurrent = service.enqueue_advance(game.id, "other-night-command")
    assert concurrent.command_id == queued.command_id
    assert concurrent.replayed is True
    await service._tasks[(game.id, queued.command_id)]
    pause_thread.join(timeout=5)
    assert service.load_game_status(game.id) == "PAUSED"


@pytest.mark.asyncio
async def test_same_command_id_is_isolated_between_games(tmp_path: Path):
    class SlowFakeProvider(FakeProvider):
        async def generate(self, request):
            await asyncio.sleep(0.02)
            return await super().generate(request)

    database = Database.sqlite(tmp_path / "multi-game.db")
    Base.metadata.create_all(database.engine)
    service = GameApplicationService(
        database,
        GameEngine(),
        AgentGateway(SlowFakeProvider(), "fake", DatabaseCallRecorder(database)),
    )
    first = service.create_game(random_seed=1)
    second = service.create_game(random_seed=2)
    service.start_game(first.id, "start-first")
    service.start_game(second.id, "start-second")
    await service.advance_now(first.id, "night-first")
    await service.advance_now(second.id, "night-second")

    service.enqueue_advance(first.id, "same")
    service.enqueue_advance(second.id, "same")
    first_task = service._tasks[(first.id, "same")]
    second_task = service._tasks[(second.id, "same")]
    await asyncio.gather(first_task, second_task)

    assert service.command_status(first.id, "same").status == "SUCCEEDED"
    assert service.command_status(second.id, "same").status == "SUCCEEDED"
    with database.session() as session:
        repo = Repository(session)
        assert len(repo.list_llm_calls(first.id)) == 1
        assert len(repo.list_llm_calls(second.id)) == 1
