"""add ui_settings to conversations and response_flags table

Revision ID: f1a2b3c4d5f7
Revises: e6f7a8b9c0d1
Create Date: 2026-08-10 00:00:00.000000

Backs the new per-thread client-UI feature-toggle mechanism (surfaced from the
chat header) and its first feature, operator response flagging.

Adds a nullable ``ui_settings`` column to ``conversations``: a compact JSON
object of boolean toggles (e.g. ``{"response_flagging": true}``), mirroring
``session_overrides`` — opaque to the server, written with read-modify-write
merge semantics so flipping one toggle never clobbers another, and owned by
the client (the set of keys can grow without a schema change).

Adds the ``response_flags`` table: one sparse row per operator-flagged
response (turn). Keyed by ``response_id`` rather than any ``conversation_items``
row so an operator can flag a response while it is still streaming, before any
item for that turn has been persisted; deleting the row unflags it. Created at
the current schema state, so it carries the tenant-partition ``workspace_id``
column as the leading primary-key member (matching every other table after
``r1a2b3c4d5e6``) and stores ``conversation_id`` as ``Uuid16`` (matching
``conversations.id``). No foreign-key constraints (schema Rule R032 — see
``p1a2b3c4d5e6``): the ``conversation_id`` relationship is enforced by the
application (``delete_conversation``), not the database.

Both changes are additive: an older server binary reading the migrated DB
simply ignores the new column/table. Rollback is a clean ``downgrade()``
(drops the column and table) since no existing data is rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "f1a2b3c4d5f7"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``conversations.ui_settings`` and create ``response_flags``."""
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("ui_settings", sa.String(512), nullable=True))

    op.create_table(
        "response_flags",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("response_id", sa.String(128), nullable=False),
        sa.Column("flagged_by", sa.String(320), nullable=True),
        sa.Column("flagged_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id", "response_id"),
    )


def downgrade() -> None:
    """Drop ``response_flags`` and the ``ui_settings`` column."""
    op.drop_table("response_flags")
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("ui_settings")
