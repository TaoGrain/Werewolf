from pathlib import Path
import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.persistence import Database
from app.persistence import Repository


def test_safe_spectator_api_and_commands(tmp_path: Path):
    database = Database.sqlite(tmp_path / "api.db")
    app = create_app(Settings(llm_provider="fake", database_url=f"sqlite:///{tmp_path / 'ignored.db'}"), database)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        cors = client.options(
            "/api/games",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert cors.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
        created = client.post("/api/games", json={"random_seed": 7, "god_mode": False})
        assert created.status_code == 201
        assert created.json()["thinking_enabled"] is False
        game_id = created.json()["id"]
        first_player_id = created.json()["players"][0]["id"]
        assert all(player["role"] is None for player in created.json()["players"])
        started = client.post(f"/api/games/{game_id}/start", json={"command_id": "start"})
        assert started.status_code == 200
        assert client.get(f"/api/games/{game_id}").json()["phase"] == "ROLE_ASSIGNED"
        assert client.get(f"/api/games/{game_id}?god_mode=true").json()["players"][0]["role"] is None
        assert client.get(f"/api/games/{game_id}/players/{first_player_id}/calls").status_code == 403
        public_events = client.get(f"/api/games/{game_id}/events").json()
        assert all(event["visibility"] == "PUBLIC" for event in public_events)
        command = client.post(f"/api/games/{game_id}/advance", json={"command_id": "advance-1"})
        assert command.status_code == 202
        duplicate = client.post(f"/api/games/{game_id}/advance", json={"command_id": "advance-1"})
        assert duplicate.json()["replayed"] is True


def test_create_game_accepts_and_persists_model_selection(tmp_path: Path):
    database = Database.sqlite(tmp_path / "model-selection.db")
    app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(app) as client:
        created = client.post(
            "/api/games",
            json={
                "seed": 17,
                "model": "deepseekv4pro",
                "thinking_enabled": True,
            },
        )
        assert created.status_code == 201
        assert created.json()["model"] == "deepseekv4pro"
        assert created.json()["thinking_enabled"] is True

        loaded = client.get(f"/api/games/{created.json()['id']}")
        assert loaded.status_code == 200
        assert loaded.json()["model"] == "deepseekv4pro"
        assert loaded.json()["thinking_enabled"] is True

        invalid = client.post(
            "/api/games",
            json={"seed": 18, "model": "unknown-model"},
        )
        assert invalid.status_code == 422

def test_create_game_temperature_config_defaults_validates_and_persists(tmp_path: Path):
    database = Database.sqlite(tmp_path / "temperature-config.db")
    app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(app) as client:
        default_game = client.post("/api/games", json={"seed": 21})
        assert default_game.status_code == 201
        assert {
            key: default_game.json()[key]
            for key in ("speech_temperature", "vote_temperature", "action_temperature")
        } == {
            "speech_temperature": 0.55,
            "vote_temperature": 0.35,
            "action_temperature": 0.5,
        }

        custom_game = client.post(
            "/api/games",
            json={
                "seed": 22,
                "speech_temperature": 0.7,
                "vote_temperature": 0.4,
                "action_temperature": 0.8,
            },
        )
        assert custom_game.status_code == 201
        game_id = custom_game.json()["id"]
        loaded = client.get(f"/api/games/{game_id}").json()
        assert loaded["speech_temperature"] == 0.7
        assert loaded["vote_temperature"] == 0.4
        assert loaded["action_temperature"] == 0.8
        with database.session() as session:
            stored = Repository(session).load_game_state(game_id)
        assert stored["config"]["action_temperature"] == 0.8

        for field, value in (("speech_temperature", -0.1), ("vote_temperature", 2.1), ("action_temperature", 3)):
            invalid = client.post("/api/games", json={field: value})
            assert invalid.status_code == 422



def test_rest_api_drives_complete_fake_game(tmp_path: Path):
    database = Database.sqlite(tmp_path / "full-api.db")
    app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(app) as client:
        created = client.post(
            "/api/games",
            json={
                "seed": 42,
                "god_mode": True,
                "allow_private_inspection": True,
                "model": "deepseekv4pro",
            },
        ).json()
        game_id = created["id"]
        first_player_id = created["players"][0]["id"]
        started = client.post(
            f"/api/games/{game_id}/start",
            headers={"Idempotency-Key": "start-http"},
        )
        assert started.status_code == 200

        for index in range(100):
            state = client.get(f"/api/games/{game_id}?god_mode=true").json()
            if state["phase"] == "FINISHED":
                break
            if state["phase"] == "DAY_ANNOUNCEMENT" and state["day"] == 1:
                safe_state = client.get(f"/api/games/{game_id}").json()
                assert any(not player["alive"] for player in state["players"])
                assert all(player["alive"] for player in safe_state["players"])
                public_events = client.get(f"/api/games/{game_id}/events").json()
                assert not any(event["event_type"] == "NIGHT_ANNOUNCED" for event in public_events)
            command_id = f"http-advance-{index}"
            queued = client.post(
                f"/api/games/{game_id}/advance",
                headers={"Idempotency-Key": command_id},
            )
            assert queued.status_code == 202
            for _ in range(300):
                command = client.get(f"/api/games/{game_id}/commands/{command_id}").json()
                if command["status"] in {"SUCCEEDED", "FAILED"}:
                    break
                time.sleep(0.01)
            assert command["status"] == "SUCCEEDED", command

        final_state = client.get(f"/api/games/{game_id}").json()
        assert final_state["phase"] == "FINISHED"
        assert all(player["role"] for player in final_state["players"])
        seer = next(player for player in final_state["players"] if player["role"] == "SEER")
        witch = next(player for player in final_state["players"] if player["role"] == "WITCH")
        assert seer["role_history"]
        assert "alignment" in seer["role_history"][0]
        assert witch["role_history"]
        review = client.get(f"/api/games/{game_id}/review").json()
        assert review["fact_based"] is True
        assert review["ai_review"] is None
        assert review["metrics"]["llm_call_count"] > 0
        gameplay_call_count = review["metrics"]["llm_call_count"]

        generated_review = client.post(
            f"/api/games/{game_id}/review/ai",
            headers={"Idempotency-Key": "ai-review-default-model"},
            json={},
        )
        assert generated_review.status_code == 200, generated_review.text
        generated_payload = generated_review.json()
        assert generated_payload["model_key"] == "deepseekv4pro"
        assert {item["player_id"] for item in generated_payload["evaluations"]} == {
            player["id"] for player in final_state["players"]
        }
        replayed_review = client.post(
            f"/api/games/{game_id}/review/ai",
            headers={"Idempotency-Key": "ai-review-default-model"},
            json={},
        )
        assert replayed_review.status_code == 200
        assert replayed_review.json() == generated_payload
        with database.session() as session:
            assert len(Repository(session).list_llm_calls(game_id, call_type="ai_review")) == 1

        persisted_review = client.get(f"/api/games/{game_id}/review").json()
        assert persisted_review["ai_review"] == generated_payload
        assert persisted_review["metrics"]["llm_call_count"] == gameplay_call_count
        assert len(persisted_review["calls"]) == gameplay_call_count
        calls = client.get(f"/api/games/{game_id}/players/{first_player_id}/calls").json()
        assert calls
        assert "reasoning_content" not in str(calls)
        assert all("reasoning_text" in call for call in calls)


def test_host_events_include_wolf_target_but_player_events_do_not(tmp_path: Path):
    database = Database.sqlite(tmp_path / "host-events.db")
    app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(app) as client:
        game_id = client.post("/api/games", json={"seed": 5, "god_mode": True}).json()["id"]
        client.post(f"/api/games/{game_id}/start", json={"command_id": "start"})

        # 身份分配 -> 预言家查验 -> 狼人交流 -> 狼人出刀；各阶段分别推进。
        for command_id in (
            "to-seer",
            "seer-action",
            "wolf-discussion",
            "wolf-action",
        ):
            queued = client.post(
                f"/api/games/{game_id}/advance",
                json={"command_id": command_id},
            )
            assert queued.status_code == 202
            for _ in range(200):
                command = client.get(f"/api/games/{game_id}/commands/{command_id}").json()
                if command["status"] in {"SUCCEEDED", "FAILED"}:
                    break
                time.sleep(0.01)
            assert command["status"] == "SUCCEEDED", command

        player_events = client.get(f"/api/games/{game_id}/events").json()
        host_events = client.get(f"/api/games/{game_id}/events?god_mode=true").json()
        assert not any(event["event_type"].startswith("WOLF_") for event in player_events)
        discussion = [event for event in host_events if event["event_type"] == "WOLF_DISCUSSION_MESSAGE"]
        assert len(discussion) == 2
        assert all(event["payload"]["audience_player_ids"] for event in discussion)
        wolf_target = next(event for event in host_events if event["event_type"] == "WOLF_TARGET_SELECTED")
        assert wolf_target["payload"]["target_id"]

        revealed_game = client.get(f"/api/games/{game_id}?god_mode=true").json()
        wolf_id = next(player["id"] for player in revealed_game["players"] if player["role"] == "WOLF")
        good_id = next(player["id"] for player in revealed_game["players"] if player["role"] != "WOLF")
        wolf_review = client.get(f"/api/games/{game_id}/review?player_id={wolf_id}").json()
        good_review = client.get(f"/api/games/{game_id}/review?player_id={good_id}").json()
        assert any(event["event_type"] == "WOLF_DISCUSSION_MESSAGE" for event in wolf_review["events"])
        assert not any(event["event_type"] == "WOLF_DISCUSSION_MESSAGE" for event in good_review["events"])


def test_http_retry_recovers_orphaned_command_after_restart(tmp_path: Path):
    database = Database.sqlite(tmp_path / "orphan-http.db")
    first_app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(first_app) as client:
        created = client.post("/api/games", json={"seed": 9}).json()
        game_id = created["id"]
        client.post(f"/api/games/{game_id}/start", json={"command_id": "start"})
    with database.session() as session:
        repo = Repository(session)
        command = repo.create_phase_command(
            game_id,
            "orphan-http",
            command_type="advance",
            phase="ROLE_ASSIGNED",
        )
        repo.update_phase_command(command, status="RUNNING")

    restarted_app = create_app(Settings(llm_provider="fake"), database)
    with TestClient(restarted_app) as client:
        retried = client.post(
            f"/api/games/{game_id}/retry",
            headers={"Idempotency-Key": "replacement-http"},
        )
        assert retried.status_code == 202
        assert retried.json()["command_id"] == "replacement-http"
        for _ in range(200):
            status_response = client.get(
                f"/api/games/{game_id}/commands/replacement-http"
            ).json()
            if status_response["status"] in {"SUCCEEDED", "FAILED"}:
                break
            time.sleep(0.01)
        assert status_response["status"] == "SUCCEEDED"
        orphan = client.get(f"/api/games/{game_id}/commands/orphan-http").json()
        assert orphan["status"] == "FAILED"
