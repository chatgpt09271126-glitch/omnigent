"""add per-code-block snapshot metadata

Revision ID: c5d8e1f2a3b4
Revises: b4c7d9e1f2a3
Create Date: 2026-08-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "c5d8e1f2a3b4"
down_revision: str | None = "b4c7d9e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the code snapshot metadata table and block lookup index."""
    op.create_table(
        "code_snapshots",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("response_id", sa.String(128), nullable=False),
        sa.Column("item_id", Uuid16(), nullable=False),
        sa.Column("code_block_start_offset", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(128), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("capture_type", sa.SmallInteger(), nullable=False),
        sa.Column("artifact_key", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "capture_type IN (1, 2, 3, 4)",
            name="ck_code_snapshots_capture_type",
        ),
        sa.CheckConstraint(
            "code_block_start_offset >= 0",
            name="ck_code_snapshots_code_block_start_offset",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id", "id"),
        sa.UniqueConstraint("artifact_key"),
    )
    op.create_index(
        "ix_code_snapshots_block",
        "code_snapshots",
        [
            "workspace_id",
            "conversation_id",
            "response_id",
            "item_id",
            "code_block_start_offset",
            "created_at",
            "id",
        ],
    )


def downgrade() -> None:
    """Drop code snapshot metadata."""
    op.drop_index("ix_code_snapshots_block", table_name="code_snapshots")
    op.drop_table("code_snapshots")
