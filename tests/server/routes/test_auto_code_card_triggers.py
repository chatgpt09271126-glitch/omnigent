"""Regression tests for the auto-code-card generation trigger wiring.

``generate_auto_code_cards`` (Task 4) has its own unit coverage in
``tests/server/test_auto_code_cards.py``. What is NOT covered anywhere
else is that it actually gets *called*, with the right ids, from the
three real persistence hook points that fire it:

- ``helpers._flush_relay_text`` (relay path, scaffold harnesses)
- ``orchestration._persist_external_conversation_item`` (native
  transcript bridge, gated to assistant messages only)
- ``helpers._persist_external_assistant_message``
  (``external_assistant_message`` event type)

These three call sites are the entire trigger surface for the auto
code card feature end-to-end. A refactor of any of them — dropping a
kwarg, flipping a gate condition, moving the fire point above the
persist call — would silently disable card generation with no other
test catching it. These tests call each hook function directly (real
sqlite-backed ``SqlAlchemyConversationStore``, monkeypatched
``generate_auto_code_cards``) to pin the wiring without needing a full
server/relay stack.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from omnigent.server.routes._sessions import common as common_module
from omnigent.server.routes._sessions import helpers as helpers_module
from omnigent.server.routes._sessions import orchestration as orchestration_module
from omnigent.server.schemas import SessionEventInput
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)

_FAKE_SNAPSHOT_STORE = object()
_FAKE_ARTIFACT_STORE = object()


@pytest.fixture
def store(db_uri: str) -> SqlAlchemyConversationStore:
    return SqlAlchemyConversationStore(db_uri)


def _patch_generator(monkeypatch: pytest.MonkeyPatch, module: Any) -> AsyncMock:
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(module, "generate_auto_code_cards", mock)
    return mock


async def _drain_fired_tasks() -> None:
    """Await any auto-code-card task the hook fired-and-forgot.

    The hooks under test deliberately never await
    ``generate_auto_code_cards`` themselves (a slow rasterization must
    not delay the response) — they hold the task in the shared
    ``common._auto_code_card_tasks`` set instead. Tests need the task
    to have actually run before asserting on the mock, so drain that
    set here rather than asserting immediately after the hook returns.
    """
    pending = list(common_module._auto_code_card_tasks)
    if pending:
        await asyncio.gather(*pending)


# ── helpers._flush_relay_text ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flush_relay_text_fires_generation_with_correct_ids(
    store: SqlAlchemyConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _patch_generator(monkeypatch, helpers_module)
    conv = store.create_conversation()
    text_acc = ["Here is some ", "```python\nprint(1)\n```"]

    await helpers_module._flush_relay_text(
        store,
        conv.id,
        text_acc,
        "resp_relay_1",
        "debby",
        code_snapshot_store=_FAKE_SNAPSHOT_STORE,  # type: ignore[arg-type]
        artifact_store=_FAKE_ARTIFACT_STORE,  # type: ignore[arg-type]
    )

    await _drain_fired_tasks()
    mock.assert_awaited_once()
    kwargs = mock.await_args.kwargs
    assert kwargs["conversation_id"] == conv.id
    assert kwargs["response_id"] == "resp_relay_1"
    assert kwargs["snapshot_store"] is _FAKE_SNAPSHOT_STORE
    assert kwargs["artifact_store"] is _FAKE_ARTIFACT_STORE
    # item_id must match the item actually persisted to the store.
    items = store.list_items(conv.id).data
    messages = [item for item in items if item.type == "message"]
    assert len(messages) == 1
    assert kwargs["item_id"] == messages[0].id


@pytest.mark.asyncio
async def test_flush_relay_text_whitespace_only_does_not_fire(
    store: SqlAlchemyConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _patch_generator(monkeypatch, helpers_module)
    conv = store.create_conversation()
    text_acc = ["   ", "\n\t"]

    await helpers_module._flush_relay_text(
        store,
        conv.id,
        text_acc,
        "resp_relay_2",
        "debby",
        code_snapshot_store=_FAKE_SNAPSHOT_STORE,  # type: ignore[arg-type]
        artifact_store=_FAKE_ARTIFACT_STORE,  # type: ignore[arg-type]
    )

    await _drain_fired_tasks()
    mock.assert_not_awaited()
    # Whitespace-only text is dropped entirely, not persisted.
    assert store.list_items(conv.id).data == []


@pytest.mark.asyncio
async def test_flush_relay_text_missing_stores_does_not_fire_or_raise(
    store: SqlAlchemyConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _patch_generator(monkeypatch, helpers_module)
    conv = store.create_conversation()
    text_acc = ["some real text"]

    await helpers_module._flush_relay_text(
        store,
        conv.id,
        text_acc,
        "resp_relay_3",
        "debby",
        code_snapshot_store=None,
        artifact_store=None,
    )

    await _drain_fired_tasks()
    mock.assert_not_awaited()
    # The message itself is still persisted; only card generation is skipped.
    items = store.list_items(conv.id).data
    assert len([item for item in items if item.type == "message"]) == 1


# ── orchestration._persist_external_conversation_item ────────────────────


def _message_body(role: str, text: str) -> SessionEventInput:
    item_data: dict[str, Any] = {
        "role": role,
        "content": [{"type": "output_text", "text": text}],
    }
    if role == "assistant":
        item_data["agent"] = "claude"
    return SessionEventInput(
        type="external_conversation_item",
        data={
            "item_type": "message",
            "item_data": item_data,
            "response_id": "resp_ext_1",
        },
    )


@pytest.mark.asyncio
async def test_persist_external_conversation_item_assistant_fires_generation(
    store: SqlAlchemyConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _patch_generator(monkeypatch, orchestration_module)
    conv = store.create_conversation()
    body = _message_body("assistant", "```js\nconsole.log(1)\n```")

    item_id = await orchestration_module._persist_external_conversation_item(
        conv.id,
        conv,
        body,
        store,
        code_snapshot_store=_FAKE_SNAPSHOT_STORE,  # type: ignore[arg-type]
        artifact_store=_FAKE_ARTIFACT_STORE,  # type: ignore[arg-type]
    )

    await _drain_fired_tasks()
    mock.assert_awaited_once()
    kwargs = mock.await_args.kwargs
    assert kwargs["conversation_id"] == conv.id
    assert kwargs["response_id"] == "resp_ext_1"
    assert kwargs["item_id"] == item_id


@pytest.mark.asyncio
async def test_persist_external_conversation_item_user_message_does_not_fire(
    store: SqlAlchemyConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _patch_generator(monkeypatch, orchestration_module)
    conv = store.create_conversation()
    body = _message_body("user", "```js\nconsole.log(1)\n```")

    await orchestration_module._persist_external_conversation_item(
        conv.id,
        conv,
        body,
        store,
        code_snapshot_store=_FAKE_SNAPSHOT_STORE,  # type: ignore[arg-type]
        artifact_store=_FAKE_ARTIFACT_STORE,  # type: ignore[arg-type]
    )

    await _drain_fired_tasks()
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_external_conversation_item_tool_call_does_not_fire(
    store: SqlAlchemyConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _patch_generator(monkeypatch, orchestration_module)
    conv = store.create_conversation()
    body = SessionEventInput(
        type="external_conversation_item",
        data={
            "item_type": "function_call",
            "item_data": {
                "call_id": "call_1",
                "agent": "claude",
                "name": "Bash",
                "arguments": "{}",
            },
            "response_id": "resp_ext_2",
        },
    )

    await orchestration_module._persist_external_conversation_item(
        conv.id,
        conv,
        body,
        store,
        code_snapshot_store=_FAKE_SNAPSHOT_STORE,  # type: ignore[arg-type]
        artifact_store=_FAKE_ARTIFACT_STORE,  # type: ignore[arg-type]
    )

    await _drain_fired_tasks()
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_external_conversation_item_missing_stores_does_not_fire(
    store: SqlAlchemyConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _patch_generator(monkeypatch, orchestration_module)
    conv = store.create_conversation()
    body = _message_body("assistant", "some assistant text")

    await orchestration_module._persist_external_conversation_item(
        conv.id,
        conv,
        body,
        store,
        code_snapshot_store=None,
        artifact_store=None,
    )

    await _drain_fired_tasks()
    mock.assert_not_awaited()


# ── helpers._persist_external_assistant_message ───────────────────────────


@pytest.mark.asyncio
async def test_persist_external_assistant_message_fires_generation(
    store: SqlAlchemyConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _patch_generator(monkeypatch, helpers_module)
    conv = store.create_conversation()
    body = SessionEventInput(
        type="external_assistant_message",
        data={
            "agent": "claude",
            "text": "```python\nprint('hi')\n```",
            "response_id": "resp_asst_1",
        },
    )

    item_id = await helpers_module._persist_external_assistant_message(
        conv.id,
        body,
        store,
        code_snapshot_store=_FAKE_SNAPSHOT_STORE,  # type: ignore[arg-type]
        artifact_store=_FAKE_ARTIFACT_STORE,  # type: ignore[arg-type]
    )

    await _drain_fired_tasks()
    mock.assert_awaited_once()
    kwargs = mock.await_args.kwargs
    assert kwargs["conversation_id"] == conv.id
    assert kwargs["response_id"] == "resp_asst_1"
    assert kwargs["item_id"] == item_id


@pytest.mark.asyncio
async def test_persist_external_assistant_message_missing_stores_does_not_raise(
    store: SqlAlchemyConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _patch_generator(monkeypatch, helpers_module)
    conv = store.create_conversation()
    body = SessionEventInput(
        type="external_assistant_message",
        data={
            "agent": "claude",
            "text": "plain text, no code",
            "response_id": "resp_asst_2",
        },
    )

    item_id = await helpers_module._persist_external_assistant_message(
        conv.id,
        body,
        store,
        code_snapshot_store=None,
        artifact_store=None,
    )

    await _drain_fired_tasks()
    mock.assert_not_awaited()
    assert item_id
