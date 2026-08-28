"""Tests for per-thread ``ui_settings`` and response flagging.

Covers the two additions that let an operator flag an assistant response
live (even mid-stream, before any ``conversation_items`` row exists for
the turn) and gate that capability behind a per-conversation UI toggle:

- ``PATCH /v1/sessions/{id}`` merging ``ui_settings`` (never clobbering
  other keys already set).
- ``POST /v1/sessions/{id}/responses/{response_id}/flag`` setting and
  clearing a flag, including on a ``response_id`` with no persisted
  items yet (the mid-stream case) and permission gating in multi-user
  mode.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from omnigent.db.utils import generate_agent_id
from omnigent.errors import OmnigentError
from omnigent.runtime import session_stream
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER, LEVEL_READ, UnifiedAuthProvider
from omnigent.server.routes.response_flags import create_response_flags_router
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.permission_store.sqlalchemy_store import (
    SqlAlchemyPermissionStore,
)

ALICE = "alice@example.com"
BOB = "bob@example.com"


@pytest_asyncio.fixture()
async def session_id(db_uri: str) -> str:
    """Seed a test agent and conversation, return the session ID."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="flag-test-agent", bundle_location="test:///bundle")
    conv = conv_store.create_conversation(agent_id=agent_id)
    return conv.id


# ── PATCH ui_settings: merge semantics ───────────────────────────────


async def test_patch_ui_settings_round_trips(client: httpx.AsyncClient, session_id: str) -> None:
    """A PATCH setting ui_settings is reflected on the next GET."""
    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"ui_settings": {"response_flagging": True}},
    )
    assert resp.status_code == 200
    assert resp.json()["ui_settings"] == {"response_flagging": True}

    get_resp = await client.get(f"/v1/sessions/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["ui_settings"] == {"response_flagging": True}


async def test_patch_ui_settings_merges_not_replaces(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """A second PATCH with a different key doesn't clobber the first."""
    await client.patch(
        f"/v1/sessions/{session_id}",
        json={"ui_settings": {"response_flagging": True}},
    )
    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"ui_settings": {"other_toggle": False}},
    )
    assert resp.status_code == 200
    assert resp.json()["ui_settings"] == {
        "response_flagging": True,
        "other_toggle": False,
    }


async def test_patch_ui_settings_overwrites_same_key(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """Setting an existing key again updates its value in place."""
    await client.patch(
        f"/v1/sessions/{session_id}",
        json={"ui_settings": {"response_flagging": True}},
    )
    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"ui_settings": {"response_flagging": False}},
    )
    assert resp.status_code == 200
    assert resp.json()["ui_settings"] == {"response_flagging": False}


async def test_patch_ui_settings_broadcasts_full_merged_state(
    client: httpx.AsyncClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connected viewers receive the complete merged toggle map."""
    await client.patch(
        f"/v1/sessions/{session_id}",
        json={"ui_settings": {"response_flagging": True}},
    )
    published: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        session_stream,
        "publish",
        lambda conversation_id, event: published.append((conversation_id, event)),
    )

    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"ui_settings": {"other_toggle": False}},
    )

    assert resp.status_code == 200
    assert published == [
        (
            session_id,
            {
                "sequence_number": None,
                "type": "session.ui_settings",
                "conversation_id": session_id,
                "ui_settings": {"response_flagging": True, "other_toggle": False},
            },
        )
    ]


# ── POST .../flag: single-user round-trip ────────────────────────────


async def test_flag_response_sets_and_appears_on_get(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """Flagging a response id succeeds and shows up on the next GET,
    even though no conversation_items row exists for it (the mid-stream
    case: response_id is allocated before any item is persisted)."""
    resp = await client.post(
        f"/v1/sessions/{session_id}/responses/resp_mid_stream/flag",
        json={"flagged": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["response_id"] == "resp_mid_stream"
    assert body["flagged"] is True
    assert "flagged_at" in body

    get_resp = await client.get(f"/v1/sessions/{session_id}")
    assert get_resp.status_code == 200
    flagged = get_resp.json()["flagged_responses"]
    assert "resp_mid_stream" in flagged


async def test_unflag_response_removes_it(client: httpx.AsyncClient, session_id: str) -> None:
    """Clearing a flag removes it from the session snapshot."""
    await client.post(
        f"/v1/sessions/{session_id}/responses/resp_1/flag",
        json={"flagged": True},
    )
    resp = await client.post(
        f"/v1/sessions/{session_id}/responses/resp_1/flag",
        json={"flagged": False},
    )
    assert resp.status_code == 200
    assert resp.json()["flagged"] is False

    get_resp = await client.get(f"/v1/sessions/{session_id}")
    assert "resp_1" not in get_resp.json()["flagged_responses"]


async def test_flag_response_broadcasts_live_event(
    client: httpx.AsyncClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every connected viewer receives the persisted flag transition."""
    published: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        session_stream,
        "publish",
        lambda conversation_id, event: published.append((conversation_id, event)),
    )

    resp = await client.post(
        f"/v1/sessions/{session_id}/responses/resp_streaming/flag",
        json={"flagged": True},
    )

    assert resp.status_code == 200
    assert len(published) == 1
    conversation_id, event = published[0]
    assert conversation_id == session_id
    assert event == {
        "sequence_number": None,
        "type": "response.flagged",
        "conversation_id": session_id,
        "response_id": "resp_streaming",
        "flagged": True,
        "flagged_by": None,
        "flagged_at": resp.json()["flagged_at"],
    }


async def test_flag_nonexistent_session_returns_404(client: httpx.AsyncClient) -> None:
    """Flagging a response on a nonexistent session returns 404."""
    resp = await client.post(
        "/v1/sessions/1d0b12236c77f69f5073a53583de1a3f/responses/resp_1/flag",
        json={"flagged": True},
    )
    assert resp.status_code == 404


# ── POST .../flag: multi-user permission gating ──────────────────────


def _install_error_handler(app: FastAPI) -> None:
    """Mirror ``create_app()``'s OmnigentError → HTTP translation."""

    @app.exception_handler(OmnigentError)
    async def _handle_omnigent_error(request: Request, exc: OmnigentError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )


def _multi_user_app(
    conversation_store: SqlAlchemyConversationStore,
    permission_store: SqlAlchemyPermissionStore,
) -> FastAPI:
    """Build a standalone app mounting only the response-flags router."""
    app = FastAPI()
    _install_error_handler(app)
    app.include_router(
        create_response_flags_router(
            conversation_store,
            auth_provider=UnifiedAuthProvider(source="header"),
            permission_store=permission_store,
        ),
        prefix="/v1",
    )
    return app


def _seed_multi_user_session(
    db_uri: str,
    *,
    owner: str = ALICE,
    grant: tuple[str, int] | None = None,
) -> tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore, str]:
    """Seed an agent + conversation with an owner and optional extra grant."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    permission_store = SqlAlchemyPermissionStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="flag-perm-agent", bundle_location="test:///bundle")
    conv = conversation_store.create_conversation(agent_id=agent_id)
    permission_store.ensure_user(owner)
    permission_store.grant(owner, conv.id, LEVEL_OWNER)
    if grant is not None:
        user, level = grant
        permission_store.ensure_user(user)
        permission_store.grant(user, conv.id, level)
    return conversation_store, permission_store, conv.id


def test_read_only_collaborator_cannot_flag(db_uri: str) -> None:
    """A LEVEL_READ collaborator (Bob) is rejected with 403."""
    conversation_store, permission_store, conv_id = _seed_multi_user_session(
        db_uri, grant=(BOB, LEVEL_READ)
    )
    app = _multi_user_app(conversation_store, permission_store)

    resp = TestClient(app).post(
        f"/v1/sessions/{conv_id}/responses/resp_1/flag",
        json={"flagged": True},
        headers={"X-Forwarded-Email": BOB},
    )
    assert resp.status_code == 403


def test_editor_can_flag(db_uri: str) -> None:
    """A LEVEL_EDIT collaborator (Bob) can flag a response."""
    conversation_store, permission_store, conv_id = _seed_multi_user_session(
        db_uri, grant=(BOB, LEVEL_EDIT)
    )
    app = _multi_user_app(conversation_store, permission_store)

    resp = TestClient(app).post(
        f"/v1/sessions/{conv_id}/responses/resp_1/flag",
        json={"flagged": True},
        headers={"X-Forwarded-Email": BOB},
    )
    assert resp.status_code == 200
    assert resp.json()["flagged_by"] == BOB
