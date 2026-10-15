"""preserve MySQL datetime microseconds

Revision ID: b6e9c2107f42
Revises: a0235d03be84
Create Date: 2026-07-21 14:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "b6e9c2107f42"
down_revision: Union[str, Sequence[str], None] = "a0235d03be84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DATETIME_COLUMNS = (
    ("games", "created_at", False),
    ("games", "updated_at", False),
    ("phase_commands", "started_at", True),
    ("phase_commands", "finished_at", True),
    ("phase_commands", "created_at", False),
    ("phase_commands", "updated_at", False),
    ("players", "created_at", False),
    ("players", "updated_at", False),
    ("agent_sessions", "last_message_at", True),
    ("agent_sessions", "created_at", False),
    ("agent_sessions", "updated_at", False),
    ("role_assignments", "created_at", False),
    ("role_assignments", "updated_at", False),
    ("llm_calls", "started_at", False),
    ("llm_calls", "finished_at", True),
    ("agent_memory_snapshots", "created_at", False),
    ("game_events", "created_at", False),
    ("llm_messages", "created_at", False),
    ("phase_actions", "created_at", False),
    ("votes", "created_at", False),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    for table_name, column_name, nullable in DATETIME_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.DateTime(),
            type_=mysql.DATETIME(fsp=6),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    for table_name, column_name, nullable in DATETIME_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=mysql.DATETIME(fsp=6),
            type_=sa.DateTime(),
            existing_nullable=nullable,
        )
