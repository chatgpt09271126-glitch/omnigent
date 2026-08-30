"""Add auto_code_card capture type to code_snapshots.

Revision ID: zb1c2d3e4f5a
Revises: za2b3c4d5e6f
Create Date: 2026-08-30 00:00:00.000000

Widens the ``ck_code_snapshots_capture_type`` check constraint from
``(1, 2, 3, 4)`` to ``(1, 2, 3, 4, 5)`` so automatically generated code
cards (``auto_code_card`` = 5) can be stored alongside manually captured
snapshots. Additive. No existing data needs backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "zb1c2d3e4f5a"
down_revision: str | None = "d6e9f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Widen ``ck_code_snapshots_capture_type`` to allow value 5."""
    with op.batch_alter_table("code_snapshots") as batch_op:
        batch_op.drop_constraint("ck_code_snapshots_capture_type", type_="check")
        batch_op.create_check_constraint(
            "ck_code_snapshots_capture_type",
            "capture_type IN (1, 2, 3, 4, 5)",
        )


def downgrade() -> None:
    """Narrow ``ck_code_snapshots_capture_type`` back to ``(1, 2, 3, 4)``."""
    with op.batch_alter_table("code_snapshots") as batch_op:
        batch_op.drop_constraint("ck_code_snapshots_capture_type", type_="check")
        batch_op.create_check_constraint(
            "ck_code_snapshots_capture_type",
            "capture_type IN (1, 2, 3, 4)",
        )
