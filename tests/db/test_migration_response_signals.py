"""Migration coverage for additive Interview response signals."""

from __future__ import annotations

import tempfile
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import omnigent.db


def test_existing_response_flags_survive_signal_migration() -> None:
    """An already-deployed Bad row remains unchanged after the additive DDL."""
    with tempfile.TemporaryDirectory() as tmp:
        uri = f"sqlite:///{Path(tmp) / 'signals.db'}"
        config = Config()
        config.set_main_option(
            "script_location", str(Path(omnigent.db.__file__).parent / "migrations")
        )
        config.set_main_option("sqlalchemy.url", uri)
        command.upgrade(config, "c5d8e1f2a3b4")

        engine = sa.create_engine(uri)
        conversation_id = bytes.fromhex("1234567890abcdef1234567890abcdef")
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO response_flags "
                    "(workspace_id, conversation_id, response_id, flagged_by, flagged_at) "
                    "VALUES (0, :conversation_id, 'resp_existing', 'alice@example.com', 123)"
                ),
                {"conversation_id": conversation_id},
            )

        command.upgrade(config, "d6e9f2a3b4c5")
        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT response_id, flagged_by, flagged_at FROM response_flags "
                    "WHERE response_id = 'resp_existing'"
                )
            ).one()
        assert tuple(row) == ("resp_existing", "alice@example.com", 123)
        assert "response_signals" in sa.inspect(engine).get_table_names()
        engine.dispose()
