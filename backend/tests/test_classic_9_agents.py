from pathlib import Path

import pytest

from app.agents import AgentGateway
from app.agents.prompts import role_system_prompt
from app.agents.providers import FakeProvider
from app.agents.schemas import HunterOutput, LastWordsOutput
from app.agents.views import PlayerViewBuilder
from app.domain import (
    DeathCause,
    EngineResult,
    EventType,
    GamePhase,
    HunterAction,
    PendingHunterAction,
    PlayerStatus,
    Role,
    Visibility,
)
from app.engine import GameEngine
from app.persistence import Base, Database, Repository
from app.services import DatabaseCallRecorder, GameApplicationService


CLASSIC_RULESET = "classic-9-hunter-v1"


class FailingFakeProvider(FakeProvider):
    async def generate(self, request):
        self.requests.append(request)
        raise RuntimeError("模拟 Provider 失败")


class PoisonHunterFakeProvider(FakeProvider):
    poison_target_id: str | None = None

    def _default_payload(self, request):
        if request.metadata.get("call_type") == "witch_action":
            assert self.poison_target_id is not None
            return {
                "action": "poison",
                "target_id": self.poison_target_id,
                "private_reason": "服务层毒杀猎人回归测试。",
            }
        return FakeProvider._default_payload(request)
class PrematureNoShotFakeProvider(FakeProvider):
    def _default_payload(self, request):
        if request.metadata.get("call_type") == "last_words":
            return {
                "speech": "留下当前公开判断。",
                "public_reason": "整理公开票型。",
                "private_strategy": "我选择不开枪，等待后续信息。",
            }
        return FakeProvider._default_payload(request)


def _service(
    tmp_path: Path,
    database_name: str,
    provider: FakeProvider | None = None,
) -> tuple[Database, FakeProvider, GameApplicationService]:
    database = Database.sqlite(tmp_path / database_name)
    Base.metadata.create_all(database.engine)
    provider = provider or FakeProvider()
    gateway = AgentGateway(
        provider,
        "fake-model",
        DatabaseCallRecorder(database),
    )
    return database, provider, GameApplicationService(database, GameEngine(), gateway)


def _classic_view_state() -> dict:
    players = [
        {
            "id": f"p{seat}",
            "seat": seat,
            "name": f"{seat} 号玩家",
            "alive": seat != 1,
        }
        for seat in range(1, 10)
    ]
    return {
        "id": "classic-view-game",
        "day": 1,
        "phase": GamePhase.HUNTER_REACTION.value,
        "ruleset": {"version": CLASSIC_RULESET},
        "players": players,
        "role_assignments": {
            "p1": Role.HUNTER.value,
            "p2": Role.WOLF.value,
            "p3": Role.WOLF.value,
            "p4": Role.WOLF.value,
            "p5": Role.SEER.value,
            "p6": Role.WITCH.value,
            "p7": Role.VILLAGER.value,
            "p8": Role.VILLAGER.value,
            "p9": Role.VILLAGER.value,
        },
        "role_states": {
            "p1": {"hunter_shot_available": True},
        },
        "pending_hunter_action": {
            "hunter_id": "p1",
            "trigger_causes": [DeathCause.WOLF_ATTACK.value],
            "source_phase": GamePhase.NIGHT_RESOLUTION.value,
            "resume_phase": GamePhase.DAY_DISCUSSION.value,
        },
        "wolf_team_history": [
            {
                "sequence": 1,
                "actor_id": "p2",
                "content": "建议今晚优先处理五号。",
                "proposed_target_id": "p5",
            }
        ],
    }


def test_hunter_output_and_prompt_have_dedicated_contract() -> None:
    prompt = role_system_prompt({"role": Role.HUNTER.value})
    assert "你的身份是猎人" in prompt
    assert "shoot 和 pass" in prompt
    assert "已死亡并不妨碍" in prompt
    assert "选择 pass 后不会再有开枪机会" in prompt
    assert "只要有基于公开信息的合理可疑目标，就应优先选择 shoot" in prompt
    assert "遗言或其他旧任务里提前形成的开枪或放弃意向" in prompt

    last_words_schema = LastWordsOutput.model_json_schema()
    private_strategy_description = last_words_schema["properties"]["private_strategy"]["description"]
    assert "不得在此字段提前决定" in private_strategy_description
    assert "猎人稍后的开枪或放弃" in private_strategy_description

    shot = HunterOutput(
        action="shoot",
        target_id="p2",
        public_reason="根据公开票型选择二号。",
    )
    assert shot.target_id == "p2"
    passed = HunterOutput(
        action="pass",
        target_id=None,
        public_reason="放弃开枪。",
    )
    assert passed.target_id is None

    with pytest.raises(ValueError, match="开枪时必须指定"):
        HunterOutput(action="shoot", target_id=None, public_reason="字段冲突")
    with pytest.raises(ValueError, match="放弃开枪时"):
        HunterOutput(action="pass", target_id="p2", public_reason="字段冲突")


def test_hunter_private_view_and_three_wolf_views_are_isolated() -> None:
    builder = PlayerViewBuilder()
    state = _classic_view_state()

    hunter = builder.build(
        state,
        "p1",
        allowed_action="hunter_action",
        legal_targets=[f"p{seat}" for seat in range(2, 10)],
    )
    assert hunter.players[0].alive is False
    assert hunter.private_information["hunter_shot_available"] is True
    assert hunter.private_information["pending_hunter_action"] == {
        "trigger_causes": [DeathCause.WOLF_ATTACK.value],
        "source_phase": GamePhase.NIGHT_RESOLUTION.value,
        "resume_phase": GamePhase.DAY_DISCUSSION.value,
    }

    wolf_ids = {"p2", "p3", "p4"}
    for wolf_id in wolf_ids:
        wolf = builder.build(state, wolf_id)
        assert set(wolf.private_information["wolf_teammates"]) == wolf_ids - {wolf_id}
        assert wolf.private_information["wolf_team_history"][0]["actor_id"] == "p2"
        assert "pending_hunter_action" not in wolf.private_information
        assert "hunter_shot_available" not in wolf.private_information
        assert DeathCause.WOLF_ATTACK.value not in wolf.model_dump_json()

    villager = builder.build(state, "p7")
    assert villager.private_information == {}
    assert "pending_hunter_action" not in villager.model_dump_json()
    assert DeathCause.WOLF_ATTACK.value not in villager.model_dump_json()


@pytest.mark.asyncio
async def test_dead_hunter_is_called_with_legal_targets_and_actual_ruleset_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, provider, service = _service(tmp_path, "hunter-agent.db")
    created = service.create_game(
        ruleset_version=CLASSIC_RULESET,
        player_count=9,
        random_seed=17,
    )
    service.start_game(created.id, "start-hunter-agent")
    game = service.load_game(created.id)
    hunter_id = next(
        player_id
        for player_id, role in game.role_assignments.items()
        if role == Role.HUNTER
    )
    hunter = game.player(hunter_id)
    assert hunter is not None
    hunter.status = PlayerStatus.DEAD
    hunter.death_cause = DeathCause.WOLF_ATTACK
    hunter.death_causes = [DeathCause.WOLF_ATTACK]
    hunter.death_day = 1
    game.day = 1
    game.phase = GamePhase.HUNTER_REACTION
    game.pending_hunter_action = PendingHunterAction(
        hunter_id=hunter_id,
        trigger_causes=[DeathCause.WOLF_ATTACK],
        source_phase=GamePhase.NIGHT_RESOLUTION,
        resume_phase=GamePhase.DAY_DISCUSSION,
    )
    legal_targets = [player.id for player in game.alive_players()]
    captured: dict[str, object] = {}

    def submit_hunter_action(
        current_game,
        action: HunterAction,
        command_id: str,
    ) -> EngineResult:
        captured["hunter_was_dead"] = current_game.player(action.actor_id).status
        captured["action"] = action
        captured["command_id"] = command_id
        next_game = current_game.model_copy(deep=True)
        next_game.pending_hunter_action = None
        next_game.phase = GamePhase.DAY_DISCUSSION
        return EngineResult(game=next_game)

    monkeypatch.setattr(service.engine, "submit_hunter_action", submit_hunter_action)

    next_game, events, stored_actions = await service._submit_required_actions(
        game,
        "advance-hunter-agent",
    )

    assert next_game.phase == GamePhase.DAY_DISCUSSION
    assert events == []
    assert captured["hunter_was_dead"] == PlayerStatus.DEAD
    action = captured["action"]
    assert isinstance(action, HunterAction)
    assert action.actor_id == hunter_id
    assert action.action == "shoot"
    assert action.target_id == legal_targets[0]
    assert stored_actions == [action.model_dump(mode="json")]
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.metadata["call_type"] == "hunter_action"
    assert request.metadata["player_id"] == hunter_id
    assert request.metadata["legal_targets"] == legal_targets
    assert request.metadata["ruleset_version"] == CLASSIC_RULESET
    assert "你的身份是猎人" in request.messages[0].content
    current_task = request.messages[-1].content
    assert "最后且唯一一次开枪窗口" in current_task
    assert "不存在保留到后续" in current_task
    assert "只要存在基于公开信息的合理可疑目标，就优先选择 shoot" in current_task
    assert "遗言或其他旧任务里提前形成的开枪或放弃意向" in current_task

    with database.session() as session:
        calls = Repository(session).list_llm_calls(created.id, player_id=hunter_id)
        assert len(calls) == 1
        call = calls[0]
        assert call.rules_version == CLASSIC_RULESET
        assert call.request_snapshot["ruleset_version"] == CLASSIC_RULESET
        assert call.request_snapshot["metadata"]["ruleset_version"] == CLASSIC_RULESET


@pytest.mark.asyncio
async def test_hunter_reassesses_after_premature_no_shot_last_words(
    tmp_path: Path,
) -> None:
    provider = PrematureNoShotFakeProvider()
    database, _, service = _service(tmp_path, "hunter-reassess-after-last-words.db", provider)
    created = service.create_game(
        ruleset_version=CLASSIC_RULESET,
        player_count=9,
        random_seed=41,
    )
    service.start_game(created.id, "start-hunter-reassessment")
    game = service.load_game(created.id)
    hunter_id = next(
        player_id
        for player_id, role in game.role_assignments.items()
        if role == Role.HUNTER
    )
    game.phase = GamePhase.DAY_RESOLUTION
    game.pending_exile_id = hunter_id
    game.last_words_submitted = False

    after_words, _, _ = await service._submit_required_actions(
        game,
        "hunter-premature-last-words",
    )

    assert after_words.last_words_submitted is True
    assert len(provider.requests) == 1
    last_words_task = provider.requests[0].messages[-1].content
    assert "当前任务只是遗言" in last_words_task
    assert "不得在 private_strategy 或其他字段中提前决定" in last_words_task


    resolved = service.engine.complete_phase(after_words, "resolve-exiled-hunter")
    assert resolved.game.phase == GamePhase.HUNTER_REACTION

    after_shot, _, stored_actions = await service._submit_required_actions(
        resolved.game,
        "hunter-reassess-and-shoot",
    )

    assert after_shot.pending_hunter_action is None
    assert stored_actions[0]["action"] == "shoot"
    assert stored_actions[0]["target_id"] is not None
    hunter_request = provider.requests[1]
    assert hunter_request.metadata["call_type"] == "hunter_action"
    assert "忽略会话历史" in hunter_request.messages[-1].content
    assert any(
        "我选择不开枪" in message.content
        for message in hunter_request.messages[:-1]
    )
@pytest.mark.asyncio
async def test_witch_poisoned_hunter_never_calls_hunter_agent_or_leaks_publicly(
    tmp_path: Path,
) -> None:
    provider = PoisonHunterFakeProvider()
    database, _, service = _service(tmp_path, "poisoned-hunter-agent.db", provider)
    created = service.create_game(
        ruleset_version=CLASSIC_RULESET,
        player_count=9,
        random_seed=29,
    )
    service.start_game(created.id, "start-poisoned-hunter")
    game = service.load_game(created.id)
    hunter_id = next(
        player_id
        for player_id, role in game.role_assignments.items()
        if role == Role.HUNTER
    )
    wolf_target_id = next(
        player_id
        for player_id, role in game.role_assignments.items()
        if role == Role.VILLAGER
    )
    provider.poison_target_id = hunter_id
    game.day = 1
    game.phase = GamePhase.NIGHT_WITCH
    game.wolf_target_id = wolf_target_id
    game.witch_action = None
    game.night_death_ids = []
    game.pending_hunter_action = None

    with database.session() as session:
        repo = Repository(session)
        row = repo.require_game(game.id)
        existing_events = repo.list_events(game.id)
        baseline_sequence = existing_events[-1].sequence if existing_events else 0
        repo.update_game_state(
            game.id,
            game.model_dump(mode="json"),
            phase=game.phase.value,
            day=game.day,
            expected_version=row.version,
        )

    for command_id in (
        "poison-hunter",
        "resolve-poisoned-hunter",
        "announce-poisoned-hunter",
    ):
        result = await service.advance_now(game.id, command_id)
        assert result.status == "SUCCEEDED", result

    current = service.load_game(game.id)
    hunter = current.player(hunter_id)
    assert hunter is not None
    assert current.phase == GamePhase.DAY_DISCUSSION
    assert hunter.death_causes == [DeathCause.WITCH_POISON]
    assert current.pending_hunter_action is None
    assert [
        request.metadata["call_type"] for request in provider.requests
    ] == ["witch_action"]

    with database.session() as session:
        repo = Repository(session)
        calls = repo.list_llm_calls(game.id)
        public_events = repo.list_events(
            game.id,
            after_sequence=baseline_sequence,
            visibility=Visibility.PUBLIC.value,
        )

    assert [call.call_type for call in calls] == ["witch_action"]
    assert EventType.HUNTER_ACTION_REQUESTED.value not in {
        event.event_type for event in public_events
    }
    announcement = next(
        event
        for event in public_events
        if event.event_type == EventType.NIGHT_ANNOUNCED.value
    )
    assert announcement.payload == {"death_ids": current.night_death_ids}
    public_payloads = str([event.payload for event in public_events])
    assert DeathCause.WITCH_POISON.value not in public_payloads
    assert "trigger_causes" not in public_payloads



@pytest.mark.asyncio
async def test_failed_call_audit_keeps_actual_ruleset_version(tmp_path: Path) -> None:
    provider = FailingFakeProvider()
    database, _, service = _service(tmp_path, "failed-audit.db", provider)
    created = service.create_game(
        ruleset_version=CLASSIC_RULESET,
        player_count=9,
        random_seed=31,
    )
    service.start_game(created.id, "start-failed-audit")
    game = service.load_game(created.id)
    player_id = game.players[0].id
    view = service._build_view(game, player_id, "speech", [])

    with pytest.raises(RuntimeError, match="模拟 Provider 失败"):
        await service.gateway.generate_action(
            view,
            call_type="speech",
            output_model=HunterOutput,
            instruction="验证失败审计",
        )

    with database.session() as session:
        calls = Repository(session).list_llm_calls(created.id, player_id=player_id)
        assert len(calls) == 1
        assert calls[0].status == "FAILED"
        assert calls[0].rules_version == CLASSIC_RULESET
        assert calls[0].request_snapshot["ruleset_version"] == CLASSIC_RULESET


@pytest.mark.asyncio
async def test_runoff_discussion_generates_one_candidate_speech_per_command(
    tmp_path: Path,
) -> None:
    database, provider, service = _service(tmp_path, "runoff-agent.db")
    created = service.create_game(
        ruleset_version=CLASSIC_RULESET,
        player_count=9,
        random_seed=23,
    )
    service.start_game(created.id, "start-runoff-agent")
    game = service.load_game(created.id)
    candidate_ids = [player.id for player in game.alive_players()[:3]]
    game.day = 1
    game.phase = GamePhase.DAY_RUNOFF_DISCUSSION
    game.vote_round = 2
    game.runoff_candidate_ids = candidate_ids
    game.runoff_speeches = {}
    game.votes = {}
    with database.session() as session:
        repo = Repository(session)
        row = repo.require_game(game.id)
        repo.update_game_state(
            game.id,
            game.model_dump(mode="json"),
            phase=game.phase.value,
            day=game.day,
            expected_version=row.version,
        )

    for index, candidate_id in enumerate(candidate_ids, start=1):
        result = await service.advance_now(game.id, f"runoff-speech-{index}")
        assert result.status == "SUCCEEDED", result
        current = service.load_game(game.id)
        assert current.phase == GamePhase.DAY_RUNOFF_DISCUSSION
        assert list(current.runoff_speeches) == candidate_ids[:index]
        assert candidate_id in current.runoff_speeches

    completed = await service.advance_now(game.id, "complete-runoff-discussion")
    assert completed.status == "SUCCEEDED", completed
    assert service.load_game(game.id).phase == GamePhase.DAY_VOTING
    assert [request.metadata["call_type"] for request in provider.requests] == [
        "runoff_speech",
        "runoff_speech",
        "runoff_speech",
    ]


@pytest.mark.asyncio
async def test_fake_provider_can_complete_a_classic_9_game(tmp_path: Path) -> None:
    database, _, service = _service(tmp_path, "classic-full-agent.db")
    created = service.create_game(
        ruleset_version=CLASSIC_RULESET,
        player_count=9,
        random_seed=42,
    )
    service.start_game(created.id, "start-classic-full")

    for index in range(250):
        current = service.load_game(created.id)
        if current.phase == GamePhase.FINISHED:
            break
        result = await service.advance_now(created.id, f"classic-step-{index}")
        assert result.status == "SUCCEEDED", result
    else:
        pytest.fail("经典 9 人 FakeProvider 对局未能在 250 步内结束")

    finished = service.load_game(created.id)
    assert finished.phase == GamePhase.FINISHED
    assert finished.winner is not None
    with database.session() as session:
        calls = Repository(session).list_llm_calls(created.id)
        assert calls
        assert all(call.rules_version == CLASSIC_RULESET for call in calls)
