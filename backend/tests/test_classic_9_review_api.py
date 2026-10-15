from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import (
    DeathCause,
    EventType,
    GameEvent,
    GamePhase,
    PlayerStatus,
    Role,
    Visibility,
    Winner,
    WinReason,
    WinResult,
)
from app.main import create_app
from app.persistence import Database, Repository


RULESET = "classic-9-hunter-v1"


def _append_domain_event(
    game,
    *,
    event_type: EventType,
    visibility: Visibility,
    payload: dict,
) -> GameEvent:
    event = GameEvent(
        sequence=len(game.events) + 1,
        event_id=str(uuid4()),
        type=event_type,
        day=game.day,
        phase=game.phase,
        visibility=visibility,
        payload=payload,
        command_id=f"test:{event_type.value.lower()}",
    )
    game.events.append(event)
    return event


def _persist_test_state(database: Database, game, events: list[GameEvent]) -> None:
    with database.session() as session:
        repo = Repository(session)
        repo.update_game_state(
            game.id,
            game.model_dump(mode="json"),
            phase=game.phase.value,
            status="FINISHED" if game.phase == GamePhase.FINISHED else "ACTIVE",
            day=game.day,
        )
        for event in events:
            repo.append_event(
                game.id,
                event.type.value,
                event.payload,
                phase=event.phase.value,
                day=event.day,
                visibility=event.visibility.value,
                actor_player_id=(
                    event.payload.get("actor_id")
                    or event.payload.get("player_id")
                    or event.payload.get("hunter_id")
                ),
            )


def test_classic_lobby_summary_has_authoritative_nine_player_counts(
    tmp_path: Path,
) -> None:
    database = Database.sqlite(tmp_path / "classic-summary.db")
    app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(app) as client:
        created = client.post(
            "/api/games",
            json={"ruleset_version": RULESET, "player_count": 9, "seed": 19},
        )
        assert created.status_code == 201
        game_id = created.json()["id"]

        summaries = client.get("/api/games")
        assert summaries.status_code == 200
        summary = next(item for item in summaries.json() if item["id"] == game_id)
        assert summary["rules_version"] == RULESET
        assert summary["ruleset_version"] == RULESET
        assert summary["player_count"] == 9
        assert summary["alive_player_count"] == 9
        assert len(summary["players"]) == 9
        assert all(player["name"] for player in summary["players"])
        assert summary["name"]


def test_safe_spectator_redacts_hunter_qualification_but_god_view_is_complete(
    tmp_path: Path,
) -> None:
    database = Database.sqlite(tmp_path / "hunter-review.db")
    app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(app) as client:
        created = client.post(
            "/api/games",
            json={
                "ruleset_version": RULESET,
                "player_count": 9,
                "seed": 23,
                "god_mode": True,
                "allow_private_inspection": True,
            },
        ).json()
        game_id = created["id"]
        assert client.post(
            f"/api/games/{game_id}/start",
            json={"command_id": "start-review"},
        ).status_code == 200

        service = app.state.game_service
        game = service.load_game(game_id)
        game.day = 1
        game.phase = GamePhase.HUNTER_REACTION
        hunter_id = next(
            player_id
            for player_id, role in game.role_assignments.items()
            if role == Role.HUNTER
        )
        hunter = game.player(hunter_id)
        assert hunter is not None
        hunter.status = PlayerStatus.DEAD
        hunter.death_cause = DeathCause.WITCH_POISON
        hunter.death_causes = [DeathCause.WITCH_POISON]

        request_event = _append_domain_event(
            game,
            event_type=EventType.HUNTER_ACTION_REQUESTED,
            visibility=Visibility.PUBLIC,
            payload={
                "hunter_id": hunter_id,
                "trigger_causes": [DeathCause.WITCH_POISON.value],
                "legal_target_ids": [],
            },
        )
        submitted_event = _append_domain_event(
            game,
            event_type=EventType.HUNTER_ACTION_SUBMITTED,
            visibility=Visibility.HOST_PRIVATE,
            payload={"actor_id": hunter_id, "action": "pass", "target_id": None},
        )
        passed_event = _append_domain_event(
            game,
            event_type=EventType.HUNTER_PASSED,
            visibility=Visibility.PUBLIC,
            payload={"hunter_id": hunter_id, "reason": "test pass"},
        )
        shot_target_id = next(
            player.id for player in game.players if player.id != hunter_id
        )
        shot_event = _append_domain_event(
            game,
            event_type=EventType.HUNTER_SHOT,
            visibility=Visibility.PUBLIC,
            payload={"hunter_id": hunter_id, "target_id": shot_target_id},
        )
        _persist_test_state(
            database,
            game,
            [request_event, submitted_event, passed_event, shot_event],
        )
        with database.session() as session:
            Repository(session).add_phase_action(
                game_id,
                phase=GamePhase.HUNTER_REACTION.value,
                action_type="hunter_act",
                payload={"actor_id": hunter_id, "action": "pass", "target_id": None},
                day=1,
                player_id=hunter_id,
            )

        safe_game = client.get(f"/api/games/{game_id}").json()
        safe_hunter = next(player for player in safe_game["players"] if player["id"] == hunter_id)
        assert safe_hunter["role"] is None
        assert safe_hunter["death_cause"] is None
        assert safe_hunter["death_causes"] == []
        assert safe_hunter["role_history"] == []

        safe_events = client.get(f"/api/games/{game_id}/events").json()
        request_payload = next(
            event["payload"]
            for event in safe_events
            if event["event_type"] == EventType.HUNTER_ACTION_REQUESTED.value
        )
        assert "trigger_causes" not in request_payload
        safe_review = client.get(f"/api/games/{game_id}/review").json()
        assert safe_review["actions"] == []
        assert all(
            "trigger_causes" not in event["payload"]
            for event in safe_review["events"]
            if event["event_type"] == EventType.HUNTER_ACTION_REQUESTED.value
        )

        god_game = client.get(f"/api/games/{game_id}?god_mode=true").json()
        god_hunter = next(player for player in god_game["players"] if player["id"] == hunter_id)
        assert god_hunter["role"] == Role.HUNTER.value
        assert god_hunter["death_causes"] == [DeathCause.WITCH_POISON.value]
        history_types = {item["event_type"] for item in god_hunter["role_history"]}
        assert history_types == {
            EventType.HUNTER_ACTION_REQUESTED.value,
            EventType.HUNTER_ACTION_SUBMITTED.value,
            EventType.HUNTER_PASSED.value,
            EventType.HUNTER_SHOT.value,
        }
        god_review = client.get(f"/api/games/{game_id}/review?god_mode=true").json()
        assert any(action["action_type"] == "hunter_act" for action in god_review["actions"])
        private_types = {event["event_type"] for event in god_review["events"]}
        assert EventType.HUNTER_ACTION_SUBMITTED.value in private_types

        game.phase = GamePhase.FINISHED
        _persist_test_state(database, game, [])
        postgame_review = client.get(f"/api/games/{game_id}/review").json()
        postgame_types = {event["event_type"] for event in postgame_review["events"]}
        assert EventType.HUNTER_ACTION_SUBMITTED.value in postgame_types
        assert any(
            action["action_type"] == "hunter_act"
            for action in postgame_review["actions"]
        )


def test_postgame_review_preserves_structured_slaughter_side_result(
    tmp_path: Path,
) -> None:
    database = Database.sqlite(tmp_path / "winner-review.db")
    app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(app) as client:
        game_id = client.post(
            "/api/games",
            json={"ruleset_version": RULESET, "player_count": 9, "seed": 29},
        ).json()["id"]
        client.post(f"/api/games/{game_id}/start", json={"command_id": "start-winner"})
        game = app.state.game_service.load_game(game_id)
        game.phase = GamePhase.FINISHED
        game.winner = WinResult(
            winner=Winner.WOLVES,
            reason="all gods eliminated",
            reason_code=WinReason.ALL_GODS_ELIMINATED,
            alive_wolves=2,
            alive_good=3,
            alive_gods=0,
            alive_villagers=3,
        )
        _persist_test_state(database, game, [])

        payload = client.get(f"/api/games/{game_id}").json()
        review = client.get(f"/api/games/{game_id}/review").json()
        expected = {
            "winner": Winner.WOLVES.value,
            "reason": "all gods eliminated",
            "reason_code": WinReason.ALL_GODS_ELIMINATED.value,
            "alive_wolves": 2,
            "alive_good": 3,
            "alive_gods": 0,
            "alive_villagers": 3,
        }
        assert payload["winner"] == expected
        assert review["winner"] == expected
        assert review["game"]["winner"] == expected
