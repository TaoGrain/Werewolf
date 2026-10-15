"""add comments to every non-id column

Revision ID: d17f83a94c61
Revises: b6e9c2107f42
Create Date: 2026-07-21 14:30:00
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

from app.persistence.column_comments import get_column_comment


revision: str = "d17f83a94c61"
down_revision: Union[str, Sequence[str], None] = "b6e9c2107f42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _required_columns():  # type: ignore[no-untyped-def]
    bind = op.get_bind()
    inspector = inspect(bind)
    for table_name in inspector.get_table_names():
        for column in inspector.get_columns(table_name):
            if column["name"] == "id":
                continue
            yield table_name, column


def upgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    for table_name, column in _required_columns():
        op.alter_column(
            table_name,
            column["name"],
            existing_type=column["type"],
            existing_nullable=column["nullable"],
            existing_server_default=column.get("default"),
            existing_comment=column.get("comment"),
            comment=get_column_comment(table_name, column["name"]),
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    for table_name, column in _required_columns():
        op.alter_column(
            table_name,
            column["name"],
            existing_type=column["type"],
            existing_nullable=column["nullable"],
            existing_server_default=column.get("default"),
            existing_comment=column.get("comment"),
            comment=None,
        )
