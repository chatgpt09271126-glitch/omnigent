"""Add page_index to code_snapshots.

Revision ID: zc2d3e4f5a6b
Revises: zb1c2d3e4f5a
Create Date: 2026-08-31 00:00:00.000000

Adds a ``page_index`` column to ``code_snapshots`` recording which page of a
paginated code block a row represents. Auto code card generation splits long
code blocks into multiple overlapping pages (see
``omnigent/server/auto_code_cards.py``) and previously relied on
``created_at`` ordering to reconstruct page order, but ``created_at`` has
1-second resolution and all pages of one block are typically inserted within
the same second, so ordering was effectively random once two pages tied.
``page_index`` is now the authoritative ordering signal.

Additive with ``server_default="0"``: existing manual-capture rows (which are
always a single image, i.e. page 0) backfill cleanly with no explicit data
migration needed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zc2d3e4f5a6b"
down_revision: str | None = "zb1c2d3e4f5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``page_index`` to ``code_snapshots``."""
    op.add_column(
        "code_snapshots",
        sa.Column("page_index", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Remove ``page_index`` from ``code_snapshots``."""
    with op.batch_alter_table("code_snapshots") as batch_op:
        batch_op.drop_column("page_index")
