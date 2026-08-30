"""add generalized response signals

Revision ID: d6e9f2a3b4c5
Revises: c5d8e1f2a3b4
Create Date: 2026-08-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "d6e9f2a3b4c5"
down_revision: str | None = "c5d8e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create additive non-Bad response-signal storage."""
    op.create_table(
        "response_signals",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("response_id", sa.String(128), nullable=False),
        sa.Column("signal_group", sa.SmallInteger(), nullable=False),
        sa.Column("signal_type", sa.SmallInteger(), nullable=False),
        sa.Column("signaled_by", sa.String(320), nullable=True),
        sa.Column("signaled_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("signal_group IN (1, 2, 3)", name="ck_response_signals_group"),
        sa.CheckConstraint("signal_type IN (2, 3, 4, 5)", name="ck_response_signals_type"),
        sa.CheckConstraint(
            "(signal_group = 1 AND signal_type = 2) OR "
            "(signal_group = 2 AND signal_type = 3) OR "
            "(signal_group = 3 AND signal_type IN (4, 5))",
            name="ck_response_signals_group_type",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id", "response_id", "signal_group"),
    )


def downgrade() -> None:
    """Drop generalized response-signal storage."""
    op.drop_table("response_signals")
