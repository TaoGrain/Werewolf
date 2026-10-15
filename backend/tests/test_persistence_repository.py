from __future__ import annotations

from datetime import timezone
from uuid import UUID

import pytest
from sqlalchemy import select

from app.persistence import (
    Base,
    Database,
    Game,
    PersistenceService,
    Repository,
)


@pytest.fixture
def database(tmp_path):
    db = Database.sqlite(tmp_path / "repository.db")
    Base.metadata.create_all(db.engine)
    try:
        yield db
    finally:
        db.dispose()


def _create_game_and_players(database: Database):
    with database.session() as session:
        repo = Repository(session)
        game = repo.create_game(
            config={"player_count": 2},
            state_snapshot={"phase": "LOBBY", "counter": 0},
            random_seed=42,
        )
        players = repo.add_players(
            game.id,
            [
                {"seat": 1, "name": "一号", "personality": {"style": "冷静"}},
                {"seat": 2, "name": "二号", "personality": {"style": "直接"}},
            ],
        )
        return game.id, [player.id for player in players]


def test_repository_persists_complete_audit_graph_and_event_order(database: Database) -> None:
    game_id, (player_one, player_two) = _create_game_and_players(database)

    with database.session() as session:
        repo = Repository(session)
        repo.assign_role(game_id, player_one, "WOLF", private_state={"team": [player_one]})
        agent_session = repo.get_or_create_agent_session(
            game_id, player_one, state={"private": True}
        )
        call = repo.start_llm_call(
            request_id="application-request-1",
            provider="fake",
            model="fake-v1",
            adapter_version="1",
            game_id=game_id,
            player_id=player_one,
            agent_session_id=agent_session.id,
            day=1,
            phase="DAY_VOTING",
            call_type="vote",
            request_snapshot={
                "messages": [{"role": "user", "content": "投票"}],
                "authorization": "Bearer must-not-be-saved",
                "nested": {"reasoning_content": "hidden", "visible": "ok"},
            },
        )
        repo.add_llm_message(
            call.id,
            ordinal=0,
            role="user",
            content="请投票",
            message_metadata={"api_key": "secret", "visible": True},
        )
        repo.complete_llm_call(
            call,
            final_text='{"target": 2}',
            parsed_result={"target_player_id": player_two},
            response_snapshot={"reasoning_content": "hidden", "finish_reason": "stop"},
            usage={"input_tokens": 12, "output_tokens": 8},
            reasoning_tokens=3,
            latency_ms=25,
        )
        command = repo.create_phase_command(
            game_id,
            "vote-command",
            command_type="advance",
            phase="DAY_VOTING",
        )
        action = repo.add_phase_action(
            game_id,
            phase_command_id=command.id,
            player_id=player_one,
            llm_call_id=call.id,
            day=1,
            phase="DAY_VOTING",
            action_type="VOTE",
            payload={"target_player_id": player_two},
        )
        repo.add_vote(
            game_id,
            phase_action_id=action.id,
            voter_player_id=player_one,
            target_player_id=player_two,
            day=1,
            public_reason="二号发言可疑",
            confidence=0.8,
        )
        memory = repo.add_memory_snapshot(
            game_id,
            player_one,
            agent_session.id,
            llm_call_id=call.id,
            day=1,
            phase="DAY_VOTING",
            snapshot={"suspicions": {player_two: 0.8}},
        )
        first = repo.append_event(
            game_id,
            "SPEECH_PUBLISHED",
            {"text": "发言"},
            phase="DAY_DISCUSSION",
            day=1,
            actor_player_id=player_one,
        )
        second = repo.append_event(
            game_id,
            "VOTE_CAST",
            {"target_player_id": player_two},
            phase="DAY_VOTING",
            day=1,
            actor_player_id=player_one,
            llm_call_id=call.id,
        )

        assert [first.sequence, second.sequence] == [1, 2]
        assert memory.sequence == 1

    with database.session() as session:
        repo = Repository(session)
        game = repo.require_game(game_id)
        call = repo.list_llm_calls(game_id, player_id=player_one, call_type="vote")[0]
        message = repo.list_llm_messages(call.id)[0]

        UUID(game.id)
        assert game.created_at.tzinfo == timezone.utc
        assert [event.sequence for event in repo.list_events(game_id)] == [1, 2]
        assert call.status == "SUCCEEDED"
        assert call.reasoning_tokens == 3
        assert "authorization" not in call.request_snapshot
        assert call.request_snapshot["nested"] == {"visible": "ok"}
        assert call.response_snapshot == {"finish_reason": "stop"}
        assert message.message_metadata == {"visible": True}
        assert len(repo.list_role_assignments(game_id)) == 1
        assert len(repo.list_phase_actions(game_id, phase="DAY_VOTING")) == 1
        assert len(repo.list_votes(game_id, day=1)) == 1
        assert len(repo.list_memory_snapshots(game_id, player_id=player_one)) == 1


def test_database_session_rolls_back_every_write_on_error(database: Database) -> None:
    with pytest.raises(RuntimeError, match="force rollback"):
        with database.session() as session:
            repo = Repository(session)
            game = repo.create_game(config={}, state_snapshot={"phase": "LOBBY"})
            repo.append_event(game.id, "CREATED", {}, phase="LOBBY")
            raise RuntimeError("force rollback")

    with database.session() as session:
        assert list(session.scalars(select(Game))) == []


def test_repository_persists_an_abstention_without_a_target(database: Database) -> None:
    game_id, (player_one, _) = _create_game_and_players(database)

    with database.session() as session:
        vote = Repository(session).add_vote(
            game_id,
            voter_player_id=player_one,
            target_player_id=None,
            abstained=True,
            day=1,
            public_reason="信息不足，选择弃票",
        )
        assert vote.target_player_id is None
        assert vote.abstained is True

    with database.session() as session:
        stored = Repository(session).list_votes(game_id)
        assert len(stored) == 1
        assert stored[0].target_player_id is None
        assert stored[0].abstained is True


def test_phase_command_is_idempotent_and_failure_is_atomic(database: Database) -> None:
    game_id, _ = _create_game_and_players(database)
    service = PersistenceService(database)
    invocations = 0

    def settle(repo: Repository, command):
        nonlocal invocations
        invocations += 1
        game = repo.require_game(game_id)
        repo.update_game_state(
            game_id,
            {"phase": "NIGHT_WOLF", "counter": 1},
            phase="NIGHT_WOLF",
            expected_version=game.version,
        )
        repo.append_event(
            game_id,
            "PHASE_ADVANCED",
            {"to": "NIGHT_WOLF"},
            phase="NIGHT_WOLF",
        )
        return {"phase": "NIGHT_WOLF"}

    first = service.execute_phase_command(
        game_id=game_id,
        command_id="same-command",
        command_type="advance",
        phase="LOBBY",
        handler=settle,
    )
    replay = service.execute_phase_command(
        game_id=game_id,
        command_id="same-command",
        command_type="advance",
        phase="LOBBY",
        handler=settle,
    )

    assert first.status == "SUCCEEDED"
    assert replay.replayed is True
    assert replay.result == {"phase": "NIGHT_WOLF"}
    assert invocations == 1

    def broken_settlement(repo: Repository, command):
        game = repo.require_game(game_id)
        repo.update_game_state(
            game_id,
            {"phase": "BROKEN", "counter": 999},
            phase="BROKEN",
            expected_version=game.version,
        )
        repo.append_event(game_id, "MUST_ROLL_BACK", {}, phase="BROKEN")
        raise ValueError("invalid action")

    with pytest.raises(ValueError, match="invalid action"):
        service.execute_phase_command(
            game_id=game_id,
            command_id="broken-command",
            command_type="advance",
            phase="NIGHT_WOLF",
            handler=broken_settlement,
        )

    with database.session() as session:
        repo = Repository(session)
        game = repo.require_game(game_id)
        failed = repo.get_phase_command(game_id, "broken-command")
        assert game.phase == "NIGHT_WOLF"
        assert game.state_snapshot["counter"] == 1
        assert [event.event_type for event in repo.list_events(game_id)] == ["PHASE_ADVANCED"]
        assert failed is not None
        assert failed.status == "FAILED"
        assert failed.error == {"type": "ValueError", "message": "invalid action"}


def test_service_restart_recovers_json_state(tmp_path) -> None:
    database_path = tmp_path / "restart.db"
    first_database = Database.sqlite(database_path)
    Base.metadata.create_all(first_database.engine)
    with first_database.session() as session:
        repo = Repository(session)
        game = repo.create_game(
            config={"ruleset": "mvp-v1"},
            state_snapshot={"phase": "NIGHT_SEER", "seer_checks": [{"seat": 2}]},
            phase="NIGHT_SEER",
            day=2,
        )
        game_id = game.id
    first_database.dispose()

    restarted_database = Database.sqlite(database_path)
    try:
        with restarted_database.session() as session:
            repo = Repository(session)
            game = repo.require_game(game_id)
            assert game.phase == "NIGHT_SEER"
            assert game.day == 2
            assert repo.load_game_state(game_id) == {
                "phase": "NIGHT_SEER",
                "seer_checks": [{"seat": 2}],
            }
    finally:
        restarted_database.dispose()
