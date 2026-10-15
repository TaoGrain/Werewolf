"""应用启动时执行数据库迁移，并兼容早期未登记 Alembic 版本的 SQLite 库。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from .database import Database
from .models import Base


INITIAL_SQLITE_BASELINE = "d17f83a94c61"
ABSTENTION_SQLITE_BASELINE = "e7a9c4d2f681"
CURRENT_SCHEMA_BASELINE = "f3c1a9b872de"


class DatabaseMigrationError(RuntimeError):
    """数据库无法安全地自动迁移。"""


def upgrade_database(database: Database) -> None:
    """把数据库升级到最新版本。

    早期版本通过 ``Base.metadata.create_all`` 建库，没有写入 Alembic 版本号。
    对这类 SQLite 库先根据关键列识别安全基线，再执行后续正式迁移。
    """

    config = _alembic_config()
    with database.engine.connect() as connection:
        current_revision = MigrationContext.configure(connection).get_current_revision()
        if current_revision is None:
            baseline = _legacy_sqlite_baseline(connection)
            if baseline is not None:
                if connection.in_transaction():
                    connection.rollback()
                config.attributes["connection"] = connection
                command.stamp(config, baseline)

        if connection.in_transaction():
            connection.rollback()
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def _alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    return config


def _legacy_sqlite_baseline(connection: Connection) -> str | None:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    application_tables = set(Base.metadata.tables)
    present_application_tables = tables & application_tables

    if not present_application_tables:
        return None
    if connection.dialect.name != "sqlite":
        raise DatabaseMigrationError(
            "检测到未登记 Alembic 版本的非 SQLite 数据库，无法自动判断安全迁移基线。"
        )

    missing_tables = application_tables - tables
    if missing_tables:
        names = ", ".join(sorted(missing_tables))
        raise DatabaseMigrationError(
            f"检测到未完成的旧数据库结构，缺少表：{names}。请先从备份恢复。"
        )

    event_columns = {column["name"] for column in inspector.get_columns("game_events")}
    action_columns = {column["name"] for column in inspector.get_columns("phase_actions")}
    vote_columns = {
        column["name"]: column for column in inspector.get_columns("votes")
    }
    event_has_rules = "rules_version" in event_columns
    action_has_rules = "rules_version" in action_columns
    has_abstention = "abstained" in vote_columns
    target_is_nullable = bool(vote_columns["target_player_id"]["nullable"])

    if event_has_rules != action_has_rules:
        raise DatabaseMigrationError(
            "game_events 与 phase_actions 的 rules_version 列状态不一致，"
            "无法安全自动迁移。"
        )
    if has_abstention != target_is_nullable:
        raise DatabaseMigrationError(
            "votes 的弃票列与目标可空约束不一致，无法安全自动迁移。"
        )
    if event_has_rules:
        if not has_abstention:
            raise DatabaseMigrationError(
                "数据库包含规则版本列但缺少弃票结构，无法安全自动迁移。"
            )
        return CURRENT_SCHEMA_BASELINE
    if has_abstention:
        return ABSTENTION_SQLITE_BASELINE
    return INITIAL_SQLITE_BASELINE
