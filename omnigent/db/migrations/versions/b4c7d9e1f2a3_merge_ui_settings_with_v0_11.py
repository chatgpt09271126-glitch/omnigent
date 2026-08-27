"""merge custom UI settings with the v0.11 migration chain

Revision ID: b4c7d9e1f2a3
Revises: e5d9bc8ac650, f1a2b3c4d5f7
Create Date: 2026-08-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "b4c7d9e1f2a3"
down_revision: tuple[str, str] = ("e5d9bc8ac650", "f1a2b3c4d5f7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the custom and upstream migration branches."""


def downgrade() -> None:
    """Split the migration branches at their existing heads."""
