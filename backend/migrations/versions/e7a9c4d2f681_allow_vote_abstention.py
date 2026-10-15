"""allow vote abstention

Revision ID: e7a9c4d2f681
Revises: d17f83a94c61
Create Date: 2026-07-22 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7a9c4d2f681"
down_revision: Union[str, Sequence[str], None] = "d17f83a94c61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("votes", schema=None) as batch_op:
        batch_op.alter_column(
            "target_player_id",
            existing_type=sa.String(length=36),
            existing_nullable=False,
            nullable=True,
            comment="被投票玩家ID，弃票时为空",
        )
        batch_op.add_column(
            sa.Column(
                "abstained",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
                comment="是否弃票",
            )
        )
    with op.batch_alter_table("votes", schema=None) as batch_op:
        batch_op.alter_column("abstained", server_default=None)
        batch_op.create_check_constraint(
            "ck_votes_abstention_target",
            "(abstained = true AND target_player_id IS NULL) OR "
            "(abstained = false AND target_player_id IS NOT NULL)",
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM votes WHERE target_player_id IS NULL"))
    with op.batch_alter_table("votes", schema=None) as batch_op:
        batch_op.drop_constraint("ck_votes_abstention_target", type_="check")
        batch_op.drop_column("abstained")
        batch_op.alter_column(
            "target_player_id",
            existing_type=sa.String(length=36),
            existing_nullable=True,
            nullable=False,
            comment="被投票玩家ID",
        )
