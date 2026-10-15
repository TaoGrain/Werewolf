from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.persistence import Database


CLASSIC_RULESET = "classic-9-hunter-v1"
CLASSIC_LINEUP = {
    "WOLF": 3,
    "SEER": 1,
    "WITCH": 1,
    "HUNTER": 1,
    "VILLAGER": 3,
}


def _create_client(tmp_path: Path, database_name: str) -> TestClient:
    database = Database.sqlite(tmp_path / database_name)
    return TestClient(create_app(Settings(llm_provider="fake"), database))


def test_classic_9_creation_uses_nine_default_players_and_personalities(
    tmp_path: Path,
) -> None:
    with _create_client(tmp_path, "classic-defaults.db") as client:
        response = client.post(
            "/api/games",
            json={"ruleset_version": CLASSIC_RULESET, "player_count": 9},
        )

        assert response.status_code == 201, response.text
        game = response.json()
        assert game["rules_version"] == CLASSIC_RULESET
        assert game["ruleset_version"] == CLASSIC_RULESET
        assert game["player_count"] == 9
        assert game["alive_player_count"] == 9
        assert game["lineup"] == CLASSIC_LINEUP
        assert game["role_counts"] == CLASSIC_LINEUP
        assert [player["seat"] for player in game["players"]] == list(range(1, 10))
        assert [player["name"] for player in game["players"]] == [
            "沈砚",
            "林雾",
            "周烬",
            "许昼",
            "白露",
            "陆川",
            "苏禾",
            "程野",
            "江澜",
        ]
        personalities = [player["personality"] for player in game["players"]]
        assert all(personality for personality in personalities)
        assert len({personality["style"] for personality in personalities}) == 9

        loaded = client.get(f"/api/games/{game['id']}")
        assert loaded.status_code == 200
        assert loaded.json()["ruleset_version"] == CLASSIC_RULESET
        assert loaded.json()["player_count"] == 9


def test_legacy_creation_still_defaults_to_mvp_v1(tmp_path: Path) -> None:
    with _create_client(tmp_path, "legacy-default.db") as client:
        response = client.post("/api/games", json={"seed": 7})

        assert response.status_code == 201, response.text
        game = response.json()
        assert game["rules_version"] == "mvp-v1"
        assert game["ruleset_version"] == "mvp-v1"
        assert game["player_count"] == 6
        assert game["alive_player_count"] == 6
        assert len(game["players"]) == 6


def test_fake_provider_overrides_explicit_model_routes_in_offline_mode(
    tmp_path: Path,
) -> None:
    database = Database.sqlite(tmp_path / "fake-model-routing.db")
    app = create_app(Settings(llm_provider="fake"), database)

    with TestClient(app) as client:
        response = client.post(
            "/api/games",
            json={"ruleset_version": CLASSIC_RULESET, "model": "minimaxM3"},
        )

    assert response.status_code == 201, response.text
    stored_game = app.state.game_service.load_game(response.json()["id"])
    assert stored_game.config.model_key == "minimaxM3"
    expected_routes = {
        "minimaxM3": ("fake", "MiniMax-M3"),
        "deepseekv4pro": ("fake", "deepseek-v4-pro"),
        "deepseekv4flash": ("fake", "deepseek-v4-flash"),
    }
    actual_routes = {
        key: (gateway.provider.name, gateway.model)
        for key, gateway in app.state.game_service.gateways.items()
    }
    assert actual_routes == expected_routes
    provider_ids = {id(route.provider) for route in app.state.game_service.gateways.values()}
    assert len(provider_ids) == 1

def test_classic_9_creation_accepts_exact_custom_name_list(tmp_path: Path) -> None:
    names = [f"{seat} 号玩家" for seat in range(1, 10)]
    with _create_client(tmp_path, "classic-custom-names.db") as client:
        response = client.post(
            "/api/games",
            json={
                "ruleset_version": CLASSIC_RULESET,
                "player_count": 9,
                "player_names": names,
            },
        )

        assert response.status_code == 201, response.text
        assert [player["name"] for player in response.json()["players"]] == names


@pytest.mark.parametrize(
    "payload",
    [
        {"ruleset_version": "unknown-v1", "player_count": 9},
        {"ruleset_version": CLASSIC_RULESET, "player_count": 6},
        {"ruleset_version": CLASSIC_RULESET, "player_count": 9, "player_names": []},
        {"ruleset_version": CLASSIC_RULESET, "player_count": 9, "player_names": ["玩家"] * 8},
        {"ruleset_version": "mvp-v1", "player_count": 9},
        {"ruleset_version": "mvp-v1", "player_count": 6, "player_names": ["玩家"] * 9},
    ],
)
def test_creation_rejects_inconsistent_version_count_or_names(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    with _create_client(tmp_path, "invalid-creation.db") as client:
        response = client.post("/api/games", json=payload)

    assert response.status_code == 422
