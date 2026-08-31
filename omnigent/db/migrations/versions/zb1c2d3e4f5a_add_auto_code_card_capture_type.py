"""Add auto_code_card capture type to code_snapshots.

Revision ID: zb1c2d3e4f5a
Revises: za2b3c4d5e6f
Create Date: 2026-08-30 00:00:00.000000

Widens the ``ck_code_snapshots_capture_type`` check constraint from
``(1, 2, 3, 4)`` to ``(1, 2, 3, 4, 5)`` so automatically generated code
cards (``auto_code_card`` = 5) can be stored alongside manually captured
snapshots. Additive going up; no existing data needs backfill to upgrade.

Downgrading after auto code cards exist is destructive: ``downgrade()``
deletes any ``code_snapshots`` rows with ``capture_type = 5`` before
narrowing the constraint back, since Postgres would otherwise reject the
narrower check and SQLite's batch table rebuild would raise an
``IntegrityError``. Their artifacts are not cleaned up here — a migration
has no business reaching into artifact storage — so a downgrade can leave
orphaned artifact blobs behind.
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
    # Delete auto code card rows before narrowing the constraint — they'd
    # violate it (Postgres) or blow up the batch table rebuild (SQLite).
    op.execute("DELETE FROM code_snapshots WHERE capture_type = 5")
    with op.batch_alter_table("code_snapshots") as batch_op:
        batch_op.drop_constraint("ck_code_snapshots_capture_type", type_="check")
        batch_op.create_check_constraint(
            "ck_code_snapshots_capture_type",
            "capture_type IN (1, 2, 3, 4)",
        )
