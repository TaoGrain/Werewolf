from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.persistence import Database, upgrade_database


EXPECTED_TABLES = {
    "games",
    "players",
    "role_assignments",
    "game_events",
    "phase_commands",
    "phase_actions",
    "votes",
    "agent_sessions",
    "agent_memory_snapshots",
    "llm_calls",
    "llm_messages",
}


def test_initial_migration_upgrades_and_downgrades_empty_database(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migration.db"
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        table_names = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES <= table_names
        assert "alembic_version" in table_names
    finally:
        engine.dispose()

    command.downgrade(config, "base")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()


def test_startup_upgrades_unversioned_legacy_sqlite_database(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "legacy-unversioned.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "d17f83a94c61")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
    engine.dispose()

    database = Database(database_url)
    try:
        upgrade_database(database)
        columns = {
            table_name: {
                column["name"]: column
                for column in inspect(database.engine).get_columns(table_name)
            }
            for table_name in ("game_events", "phase_actions", "votes")
        }
        assert columns["game_events"]["rules_version"]["nullable"] is False
        assert columns["phase_actions"]["rules_version"]["nullable"] is False
        assert columns["votes"]["abstained"]["nullable"] is False
        assert columns["votes"]["target_player_id"]["nullable"] is True
        with database.engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "f3c1a9b872de"
            )
    finally:
        database.dispose()
