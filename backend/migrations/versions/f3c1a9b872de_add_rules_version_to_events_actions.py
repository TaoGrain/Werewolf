"""add rules version to events and actions

Revision ID: f3c1a9b872de
Revises: e7a9c4d2f681
Create Date: 2026-07-23 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3c1a9b872de"
down_revision: Union[str, Sequence[str], None] = "e7a9c4d2f681"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    games = sa.table(
        "games",
        sa.column("id", sa.String(length=36)),
        sa.column("rules_version", sa.String(length=64)),
    )
    for table_name in ("game_events", "phase_actions"):
        op.add_column(
            table_name,
            sa.Column(
                "rules_version",
                sa.String(length=64),
                nullable=False,
                server_default="mvp-v1",
                comment="record ruleset version",
            ),
        )
        target = sa.table(
            table_name,
            sa.column("game_id", sa.String(length=36)),
            sa.column("rules_version", sa.String(length=64)),
        )
        actual_version = (
            sa.select(games.c.rules_version)
            .where(games.c.id == target.c.game_id)
            .scalar_subquery()
        )
        op.execute(target.update().values(rules_version=actual_version))

    llm_calls = sa.table(
        "llm_calls",
        sa.column("game_id", sa.String(length=36)),
        sa.column("rules_version", sa.String(length=64)),
    )
    actual_llm_version = (
        sa.select(games.c.rules_version)
        .where(games.c.id == llm_calls.c.game_id)
        .scalar_subquery()
    )
    op.execute(
        llm_calls.update()
        .where(llm_calls.c.rules_version.is_(None))
        .values(rules_version=actual_llm_version)
    )


def downgrade() -> None:
    for table_name in ("phase_actions", "game_events"):
        op.drop_column(table_name, "rules_version")
