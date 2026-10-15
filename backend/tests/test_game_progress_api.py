from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import (
    DeathCause,
    GamePhase,
    PendingHunterAction,
    PlayerStatus,
    Role,
    SpeechAction,
    VoteAction,
    WolfDiscussionAction,
)
from app.main import create_app
from app.persistence import Database, Repository


RULESET = "classic-9-hunter-v1"


def _persist(database: Database, game) -> None:
    with database.session() as session:
        Repository(session).update_game_state(
            game.id,
            game.model_dump(mode="json"),
            phase=game.phase.value,
            status="ACTIVE",
            day=game.day,
        )


def _create_started_game(client: TestClient) -> str:
    created = client.post(
        "/api/games",
        json={
            "ruleset_version": RULESET,
            "player_count": 9,
            "seed": 41,
            "god_mode": True,
        },
    )
    assert created.status_code == 201
    game_id = created.json()["id"]
    started = client.post(
        f"/api/games/{game_id}/start",
        json={"command_id": "start-progress"},
    )
    assert started.status_code == 200
    return game_id


def test_nine_player_vote_runoff_and_hunter_progress_are_state_based(
    tmp_path: Path,
) -> None:
    database = Database.sqlite(tmp_path / "progress.db")
    app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(app) as client:
        game_id = _create_started_game(client)
        game = app.state.game_service.load_game(game_id)
        player_ids = [player.id for player in game.alive_players()]

        wolf_ids = [
            player_id
            for player_id, role in game.role_assignments.items()
            if role == Role.WOLF
        ]
        wolf_target_id = next(
            player_id
            for player_id in player_ids
            if player_id not in wolf_ids
        )
        game.phase = GamePhase.NIGHT_WOLF_DISCUSSION
        game.wolf_discussion = {
            wolf_ids[0]: WolfDiscussionAction(
                actor_id=wolf_ids[0],
                content="private wolf discussion",
                proposed_target_id=wolf_target_id,
                reason="private progress",
            )
        }
        _persist(database, game)
        safe_progress = client.get(f"/api/games/{game_id}").json()["progress"]
        assert safe_progress == {"completed": 0, "total": 1, "label": "夜间行动"}
        god_progress = client.get(
            f"/api/games/{game_id}?god_mode=true"
        ).json()["progress"]
        assert god_progress == {"completed": 1, "total": 3, "label": "狼人讨论"}

        game.phase = GamePhase.DAY_VOTING
        game.vote_round = 1
        game.votes = {
            actor_id: VoteAction(
                actor_id=actor_id,
                target_id=player_ids[-1] if actor_id != player_ids[-1] else player_ids[0],
                reason="partial vote",
            )
            for actor_id in player_ids[:4]
        }
        _persist(database, game)
        progress = client.get(f"/api/games/{game_id}").json()["progress"]
        assert progress == {"completed": 4, "total": 9, "label": "投票"}

        candidates = player_ids[:2]
        eligible_voters = player_ids[2:]
        game.vote_round = 2
        game.runoff_candidate_ids = candidates
        game.votes = {
            actor_id: VoteAction(
                actor_id=actor_id,
                target_id=candidates[0],
                reason="partial runoff vote",
            )
            for actor_id in eligible_voters[:3]
        }
        _persist(database, game)
        progress = client.get(f"/api/games/{game_id}").json()["progress"]
        assert progress == {"completed": 3, "total": 7, "label": "投票"}

        game.phase = GamePhase.DAY_RUNOFF_DISCUSSION
        game.runoff_speeches = {
            candidates[0]: SpeechAction(
                actor_id=candidates[0],
                content="PK statement",
                reason="runoff speech",
            )
        }
        _persist(database, game)
        progress = client.get(f"/api/games/{game_id}").json()["progress"]
        assert progress == {"completed": 1, "total": 2, "label": "PK 发言"}

        hunter_id = next(
            player_id
            for player_id, role in game.role_assignments.items()
            if role == Role.HUNTER
        )
        hunter = game.player(hunter_id)
        assert hunter is not None
        hunter.status = PlayerStatus.DEAD
        hunter.death_cause = DeathCause.EXILE
        hunter.death_causes = [DeathCause.EXILE]
        game.phase = GamePhase.HUNTER_REACTION
        game.pending_hunter_action = PendingHunterAction(
            hunter_id=hunter_id,
            trigger_causes=[DeathCause.EXILE],
            source_phase=GamePhase.DAY_RESOLUTION,
            resume_phase=GamePhase.NIGHT_SEER,
        )
        _persist(database, game)
        progress = client.get(f"/api/games/{game_id}").json()["progress"]
        assert progress == {"completed": 0, "total": 1, "label": "猎人反应"}


def test_timeout_runtime_error_exposes_retry_metadata(tmp_path: Path) -> None:
    database = Database.sqlite(tmp_path / "timeout.db")
    app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(app) as client:
        game_id = _create_started_game(client)
        game = app.state.game_service.load_game(game_id)
        with database.session() as session:
            repo = Repository(session)
            command = repo.create_phase_command(
                game_id,
                "timeout-progress",
                command_type="advance",
                phase=game.phase.value,
            )
            repo.update_phase_command(
                command,
                status="FAILED",
                error={"type": "LLMTimeoutError", "message": "模型请求超时"},
            )

        payload = client.get(f"/api/games/{game_id}").json()
        assert payload["error"] == "模型请求超时"
        assert payload["error_type"] == "timeout_error"
        assert payload["retryable"] is True


def test_budget_error_is_normalized_as_retryable(tmp_path: Path) -> None:
    database = Database.sqlite(tmp_path / "budget.db")
    app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(app) as client:
        game_id = _create_started_game(client)
        game = app.state.game_service.load_game(game_id)
        with database.session() as session:
            repo = Repository(session)
            command = repo.create_phase_command(
                game_id,
                "budget-progress",
                command_type="advance",
                phase=game.phase.value,
            )
            repo.update_phase_command(
                command,
                status="FAILED",
                error={"type": "AttemptBudgetExceeded", "message": "attempt budget exhausted"},
            )

        payload = client.get(f"/api/games/{game_id}").json()
        assert payload["error"] == "attempt budget exhausted"
        assert payload["error_type"] == "budget_exhausted"
        assert payload["retryable"] is True
