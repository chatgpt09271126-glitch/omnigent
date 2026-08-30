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


async def test_interview_mode_round_trips_without_clobbering_flags(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """Interview Mode is another opaque per-thread setting, not a DB column."""
    await client.patch(
        f"/v1/sessions/{session_id}",
        json={"ui_settings": {"response_flagging": True}},
    )
    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"ui_settings": {"interview_mode": True}},
    )
    assert resp.status_code == 200
    assert resp.json()["ui_settings"] == {
        "response_flagging": True,
        "interview_mode": True,
    }


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
    assert [event[1]["type"] for event in published] == [
        "response.signal_changed",
        "response.flagged",
    ]
    conversation_id, event = published[1]
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


async def test_generalized_signals_replace_and_clear_exclusive_groups(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """Quality and detail groups settle to one active state each."""

    async def signal(signal_type: str, active: bool = True) -> dict[str, object]:
        response = await client.post(
            f"/v1/sessions/{session_id}/responses/resp_signal/signal",
            json={"signal_type": signal_type, "active": active},
        )
        assert response.status_code == 200
        return response.json()

    assert set((await signal("bad"))["signals"]) == {"bad"}
    assert set((await signal("good"))["signals"]) == {"good"}
    assert set((await signal("attention"))["signals"]) == {"good", "attention"}
    assert set((await signal("shorter"))["signals"]) == {"good", "attention", "shorter"}
    assert set((await signal("more_detail"))["signals"]) == {
        "good",
        "attention",
        "more_detail",
    }
    assert set((await signal("good", False))["signals"]) == {"attention", "more_detail"}
    assert set((await signal("attention", False))["signals"]) == {"more_detail"}
    assert (await signal("more_detail", False))["signals"] == {}

    snapshot = (await client.get(f"/v1/sessions/{session_id}")).json()
    assert snapshot["response_signals"] == {}
    assert snapshot["flagged_responses"] == {}


async def test_legacy_flag_appears_as_generalized_bad(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """Existing flag API data is exposed as Bad without a backfill."""
    await client.post(
        f"/v1/sessions/{session_id}/responses/resp_legacy/flag",
        json={"flagged": True},
    )
    snapshot = (await client.get(f"/v1/sessions/{session_id}")).json()
    assert set(snapshot["response_signals"]["resp_legacy"]) == {"bad"}
    assert "resp_legacy" in snapshot["flagged_responses"]


async def test_signals_are_isolated_by_response(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """Multiple response galleries of signals never bleed into each other."""
    await client.post(
        f"/v1/sessions/{session_id}/responses/resp_a/signal",
        json={"signal_type": "good", "active": True},
    )
    await client.post(
        f"/v1/sessions/{session_id}/responses/resp_b/signal",
        json={"signal_type": "shorter", "active": True},
    )
    signals = (await client.get(f"/v1/sessions/{session_id}")).json()["response_signals"]
    assert set(signals["resp_a"]) == {"good"}
    assert set(signals["resp_b"]) == {"shorter"}


async def test_signals_are_isolated_by_conversation(
    client: httpx.AsyncClient, session_id: str, db_uri: str
) -> None:
    """The same response id in another conversation has independent state."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="signal-isolation-agent", bundle_location="test:///bundle")
    other = conversation_store.create_conversation(agent_id=agent_id)

    await client.post(
        f"/v1/sessions/{session_id}/responses/resp_shared/signal",
        json={"signal_type": "good", "active": True},
    )
    await client.post(
        f"/v1/sessions/{other.id}/responses/resp_shared/signal",
        json={"signal_type": "attention", "active": True},
    )

    first = (await client.get(f"/v1/sessions/{session_id}")).json()["response_signals"]
    second = (await client.get(f"/v1/sessions/{other.id}")).json()["response_signals"]
    assert set(first["resp_shared"]) == {"good"}
    assert set(second["resp_shared"]) == {"attention"}


async def test_flag_nonexistent_session_returns_404(client: httpx.AsyncClient) -> None:
    """Flagging a response on a nonexistent session returns 404."""
    resp = await client.post(
        "/v1/sessions/1d0b12236c77f69f5073a53583de1a3f/responses/resp_1/flag",
        json={"flagged": True},
    )
    assert resp.status_code == 404


async def test_help_request_broadcasts_without_persisting_signal_state(
    client: httpx.AsyncClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Help is a one-shot session event, not a signal restored on reconnect."""
    published: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        session_stream,
        "publish",
        lambda conversation_id, event: published.append((conversation_id, event)),
    )

    response = await client.post(
        f"/v1/sessions/{session_id}/responses/resp_help/help",
        json={"request_id": "help_mobile_1"},
    )

    assert response.status_code == 200
    assert response.json() == published[0][1]
    assert published[0][0] == session_id
    assert published[0][1] == {
        "sequence_number": None,
        "type": "response.help_requested",
        "conversation_id": session_id,
        "response_id": "resp_help",
        "request_id": "help_mobile_1",
        "requested_by": None,
        "requested_at": response.json()["requested_at"],
    }
    snapshot = (await client.get(f"/v1/sessions/{session_id}")).json()
    assert snapshot["response_signals"] == {}


async def test_help_request_rejects_missing_session(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/sessions/1d0b12236c77f69f5073a53583de1a3f/responses/resp_help/help",
        json={"request_id": "help_mobile_2"},
    )
    assert response.status_code == 404


async def test_screenshot_request_broadcasts_without_persisting_signal_state(
    client: httpx.AsyncClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Screenshot pls is a live human request, not reconnect state."""
    published: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        session_stream,
        "publish",
        lambda conversation_id, event: published.append((conversation_id, event)),
    )

    response = await client.post(
        f"/v1/sessions/{session_id}/responses/resp_code/screenshot-request",
        json={"request_id": "screenshot_mobile_1"},
    )

    assert response.status_code == 200
    assert response.json() == published[0][1]
    assert published[0][0] == session_id
    assert published[0][1] == {
        "sequence_number": None,
        "type": "response.screenshot_requested",
        "conversation_id": session_id,
        "response_id": "resp_code",
        "request_id": "screenshot_mobile_1",
        "requested_by": None,
        "requested_at": response.json()["requested_at"],
    }
    snapshot = (await client.get(f"/v1/sessions/{session_id}")).json()
    assert snapshot["response_signals"] == {}


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


def test_read_only_collaborator_cannot_signal(db_uri: str) -> None:
    """The generalized endpoint keeps the existing LEVEL_EDIT boundary."""
    conversation_store, permission_store, conv_id = _seed_multi_user_session(
        db_uri, grant=(BOB, LEVEL_READ)
    )
    app = _multi_user_app(conversation_store, permission_store)
    resp = TestClient(app).post(
        f"/v1/sessions/{conv_id}/responses/resp_1/signal",
        json={"signal_type": "attention", "active": True},
        headers={"X-Forwarded-Email": BOB},
    )
    assert resp.status_code == 403


def test_read_only_collaborator_cannot_request_help(db_uri: str) -> None:
    """Help uses the same existing prompt/signal edit boundary."""
    conversation_store, permission_store, conv_id = _seed_multi_user_session(
        db_uri, grant=(BOB, LEVEL_READ)
    )
    app = _multi_user_app(conversation_store, permission_store)
    resp = TestClient(app).post(
        f"/v1/sessions/{conv_id}/responses/resp_1/help",
        json={"request_id": "help_read_only"},
        headers={"X-Forwarded-Email": BOB},
    )
    assert resp.status_code == 403


def test_read_only_collaborator_cannot_request_screenshot(db_uri: str) -> None:
    """Screenshot requests keep the existing prompt/signal edit boundary."""
    conversation_store, permission_store, conv_id = _seed_multi_user_session(
        db_uri, grant=(BOB, LEVEL_READ)
    )
    app = _multi_user_app(conversation_store, permission_store)
    resp = TestClient(app).post(
        f"/v1/sessions/{conv_id}/responses/resp_1/screenshot-request",
        json={"request_id": "screenshot_read_only"},
        headers={"X-Forwarded-Email": BOB},
    )
    assert resp.status_code == 403


def test_editor_help_preserves_actor(db_uri: str) -> None:
    conversation_store, permission_store, conv_id = _seed_multi_user_session(
        db_uri, grant=(BOB, LEVEL_EDIT)
    )
    app = _multi_user_app(conversation_store, permission_store)
    resp = TestClient(app).post(
        f"/v1/sessions/{conv_id}/responses/resp_1/help",
        json={"request_id": "help_editor"},
        headers={"X-Forwarded-Email": BOB},
    )
    assert resp.status_code == 200
    assert resp.json()["requested_by"] == BOB


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


def test_editor_signal_preserves_actor(db_uri: str) -> None:
    """The active signal keeps the collaborator who most recently set it."""
    conversation_store, permission_store, conv_id = _seed_multi_user_session(
        db_uri, grant=(BOB, LEVEL_EDIT)
    )
    app = _multi_user_app(conversation_store, permission_store)
    resp = TestClient(app).post(
        f"/v1/sessions/{conv_id}/responses/resp_1/signal",
        json={"signal_type": "shorter", "active": True},
        headers={"X-Forwarded-Email": BOB},
    )
    assert resp.status_code == 200
    assert resp.json()["signals"]["shorter"]["signaled_by"] == BOB
