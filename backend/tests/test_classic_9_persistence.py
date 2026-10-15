from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.agents import AgentGateway
from app.agents.providers import FakeProvider
from app.api import router
from app.domain import GamePhase, PlayerStatus, Role
from app.engine import GameEngine
from app.persistence import Base, Database, Repository
from app.services import DatabaseCallRecorder, GameApplicationService


RULESET = "classic-9-hunter-v1"


def _service(database: Database, provider: FakeProvider | None = None) -> GameApplicationService:
    provider = provider or FakeProvider()
    return GameApplicationService(
        database,
        GameEngine(),
        AgentGateway(provider, "fake-model", DatabaseCallRecorder(database)),
    )


def _prepare_persisted_hunter_reaction(
    service: GameApplicationService,
) -> tuple[str, str]:
    game = service.create_game(
        ruleset_version=RULESET,
        random_seed=808,
        god_view=True,
        allow_private_inspection=True,
    )
    service.start_game(game.id, "start")
    game = service.load_game(game.id)
    hunter_id = next(
        player.id
        for player in game.players
        if game.role_assignments[player.id] == Role.HUNTER
    )
    game.day = 1
    game.phase = GamePhase.NIGHT_RESOLUTION
    game.wolf_target_id = hunter_id
    resolved = service.engine.complete_phase(game, "prepare:night-resolution")
    announced = service.engine.complete_phase(
        resolved.game, "prepare:day-announcement"
    )
    assert announced.game.phase == GamePhase.HUNTER_REACTION
    assert announced.game.pending_hunter_action is not None
    service._persist_engine_result(
        game.id,
        announced.game,
        [*resolved.events, *announced.events],
        "prepare-hunter-reaction",
        "test_setup",
        [],
    )
    return game.id, hunter_id


@pytest.mark.asyncio
@pytest.mark.parametrize("hunter_action", ["shoot", "pass"])
async def test_hunter_reaction_survives_restart_and_replay_is_idempotent(
    tmp_path: Path,
    hunter_action: str,
) -> None:
    database_path = tmp_path / f"hunter-{hunter_action}.db"
    first_database = Database.sqlite(database_path)
    Base.metadata.create_all(first_database.engine)
    game_id, hunter_id = _prepare_persisted_hunter_reaction(
        _service(first_database)
    )
    with first_database.session() as session:
        before_sequences = [
            row.sequence for row in Repository(session).list_events(game_id)
        ]
    first_database.dispose()

    response = {
        "action": "pass",
        "target_id": None,
        "public_reason": "restart pass",
    }
    provider = FakeProvider([response] if hunter_action == "pass" else None)
    restarted_database = Database.sqlite(database_path)
    restarted = _service(restarted_database, provider)

    recovered = restarted.load_game(game_id)
    assert recovered.phase == GamePhase.HUNTER_REACTION
    assert recovered.pending_hunter_action is not None
    assert recovered.pending_hunter_action.hunter_id == hunter_id

    first = await restarted.advance_now(game_id, "restart-hunter-action")
    assert first.status == "SUCCEEDED"
    settled = restarted.load_game(game_id)
    assert settled.pending_hunter_action is None
    assert settled.role_states[hunter_id].hunter_shot_available is False

    shot_target_id = None
    if hunter_action == "shoot":
        shot_target_id = provider.requests[0].metadata["legal_targets"][0]
        assert settled.player(shot_target_id).status == PlayerStatus.DEAD
        assert [cause.value for cause in settled.player(shot_target_id).death_causes] == [
            "HUNTER_SHOT"
        ]

    replay = await restarted.advance_now(game_id, "restart-hunter-action")
    assert replay.status == "SUCCEEDED"
    assert replay.replayed is True
    replayed_game = restarted.load_game(game_id)

    with restarted_database.session() as session:
        repo = Repository(session)
        events = repo.list_events(game_id)
        actions = repo.list_phase_actions(game_id)
        calls = repo.list_llm_calls(game_id)
        db_game = repo.require_game(game_id)
        assert db_game.rules_version == RULESET
        assert events
        assert actions
        assert calls
        assert all(row.rules_version == RULESET for row in events)
        assert all(row.rules_version == RULESET for row in actions)
        assert all(row.rules_version == RULESET for row in calls)

    sequences = [row.sequence for row in events]
    assert sequences == list(range(1, len(sequences) + 1))
    assert sequences[: len(before_sequences)] == before_sequences
    assert len(provider.requests) == 1
    shot_events = [row for row in events if row.event_type == "HUNTER_SHOT"]
    assert len(shot_events) == (1 if hunter_action == "shoot" else 0)
    assert [cause.value for cause in replayed_game.player(hunter_id).death_causes] == [
        "WOLF_ATTACK"
    ]
    death_event_ids = [
        row.payload.get("player_id")
        for row in events
        if row.event_type == "PLAYER_DIED"
    ]
    assert len(death_event_ids) == len(set(death_event_ids))
    if shot_target_id is not None:
        assert replayed_game.player(shot_target_id).death_causes == settled.player(
            shot_target_id
        ).death_causes

    app = FastAPI()
    app.state.game_service = restarted
    app.include_router(router)
    with TestClient(app) as client:
        api_events = client.get(f"/api/games/{game_id}/events?god_mode=true")
        assert api_events.status_code == 200
        assert all(item["ruleset_version"] == RULESET for item in api_events.json())
        review = client.get(f"/api/games/{game_id}/review?god_mode=true")
        assert review.status_code == 200
        payload = review.json()
        assert payload["rules_version"] == RULESET
        assert payload["ruleset_version"] == RULESET
        assert all(item["rules_version"] == RULESET for item in payload["timeline"])
        assert all(item["rules_version"] == RULESET for item in payload["calls"])
        assert all(item["rules_version"] == RULESET for item in payload["actions"])

    restarted_database.dispose()


def test_old_mvp_snapshot_without_new_fields_is_loadable_after_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-state.db"
    database = Database.sqlite(database_path)
    Base.metadata.create_all(database.engine)
    service = _service(database)
    game = service.create_game(random_seed=6)
    service.start_game(game.id, "start")

    with database.session() as session:
        repo = Repository(session)
        row = repo.require_game(game.id)
        legacy = dict(row.state_snapshot)
        legacy.pop("pending_hunter_action", None)
        legacy.pop("runoff_speeches", None)
        for player in legacy["players"]:
            player.pop("death_causes", None)
        for role_state in legacy["role_states"].values():
            role_state.pop("hunter_shot_available", None)
        legacy_rules = legacy["config"]["ruleset"]
        legacy_rules.pop("runoff_has_discussion", None)
        legacy_rules.pop("win_condition", None)
        row.state_snapshot = legacy
    database.dispose()

    restarted_database = Database.sqlite(database_path)
    restored = _service(restarted_database).load_game(game.id)
    assert restored.config.ruleset.version == "mvp-v1"
    assert restored.pending_hunter_action is None
    assert restored.runoff_speeches == {}
    assert all(player.death_causes == [] for player in restored.players)
    restarted_database.dispose()


def _alembic_config(database_url: str, output_buffer: StringIO | None = None) -> Config:
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"), output_buffer=output_buffer)
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///offline-rules-version.db",
        "mysql+pymysql://user:password@localhost/werewolf",
    ],
)
def test_rules_version_migration_compiles_offline_for_sqlite_and_mysql(
    database_url: str,
) -> None:
    output = StringIO()
    command.upgrade(
        _alembic_config(database_url, output),
        "e7a9c4d2f681:f3c1a9b872de",
        sql=True,
    )
    migration_sql = output.getvalue().lower()
    assert "game_events" in migration_sql
    assert "phase_actions" in migration_sql
    assert migration_sql.count("rules_version") >= 4
    assert "mvp-v1" in migration_sql


def test_migration_backfills_existing_rows_with_actual_game_rules_version(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-schema.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _alembic_config(database_url)
    command.upgrade(config, "e7a9c4d2f681")

    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO games "
                "(id,status,phase,day,rules_version,random_seed,config,state_snapshot,"
                "version,next_event_sequence,created_at,updated_at) "
                "VALUES ('legacy-classic','ACTIVE','HUNTER_REACTION',1,:rules,NULL,'{}','{}',1,2,:now,:now)"
            ),
            {"rules": RULESET, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO game_events "
                "(id,game_id,sequence,event_type,day,phase,visibility,actor_player_id,"
                "llm_call_id,payload,created_at) "
                "VALUES ('event-1','legacy-classic',1,'HUNTER_ACTION_REQUESTED',1,"
                "'HUNTER_REACTION','PUBLIC',NULL,NULL,'{}',:now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO phase_actions "
                "(id,game_id,phase_command_id,player_id,llm_call_id,day,phase,action_type,"
                "payload,is_valid,validation_errors,created_at) "
                "VALUES ('action-1','legacy-classic',NULL,NULL,NULL,1,'HUNTER_REACTION',"
                "'hunter_act','{}',1,'[]',:now)"
            ),
            {"now": now},
        )
    engine.dispose()

    command.upgrade(config, "head")
    migrated = create_engine(database_url)
    try:
        columns = {
            table_name: {
                column["name"]: column
                for column in inspect(migrated).get_columns(table_name)
            }
            for table_name in ("game_events", "phase_actions")
        }
        assert columns["game_events"]["rules_version"]["nullable"] is False
        assert columns["phase_actions"]["rules_version"]["nullable"] is False
        with migrated.connect() as connection:
            event_version = connection.scalar(
                text("SELECT rules_version FROM game_events WHERE id='event-1'")
            )
            action_version = connection.scalar(
                text("SELECT rules_version FROM phase_actions WHERE id='action-1'")
            )
        assert event_version == RULESET
        assert action_version == RULESET
    finally:
        migrated.dispose()
