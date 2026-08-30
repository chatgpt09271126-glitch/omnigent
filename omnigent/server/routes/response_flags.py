"""Routes for operator response (turn) flagging.

An operator can flag an assistant response — even while it is still
streaming, since ``response_id`` is allocated before any item for the
turn is persisted — so every connected viewer (including a read-only
mobile reader) sees a live highlight on that response.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from omnigent.db.utils import now_epoch
from omnigent.runtime import session_stream
from omnigent.server.auth import LEVEL_EDIT, AuthProvider
from omnigent.server.routes._auth_helpers import (
    attribution_user,
    get_user_id,
    require_access,
)
from omnigent.server.routes._errors import session_not_found
from omnigent.server.schemas import (
    ResponseFlaggedEvent,
    ResponseHelpRequestedEvent,
    ResponseScreenshotRequestedEvent,
    ResponseSignalChangedEvent,
    ResponseSignalInfo,
    ResponseSignalMutationResponse,
    ResponseSignalType,
)
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore


class SetResponseFlagRequest(BaseModel):
    """Request body for ``POST .../responses/{response_id}/flag``.

    :param flagged: ``True`` to flag the response, ``False`` to clear
        an existing flag.
    """

    flagged: bool


class SetResponseSignalRequest(BaseModel):
    """Request body for the generalized response-signal endpoint."""

    signal_type: ResponseSignalType
    active: bool


class RequestResponseEffectRequest(BaseModel):
    """Client nonce for one transient human-to-human effect."""

    request_id: str = Field(min_length=1, max_length=128)


def create_response_flags_router(
    conversation_store: ConversationStore,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build the response-flags router.

    Routes are scoped to
    ``/sessions/{session_id}/responses/{response_id}/flag``.

    :param conversation_store: The shared :class:`ConversationStore` instance.
    :param auth_provider: Auth provider used to identify the requesting
        user. ``None`` in single-user mode (no attribution stored).
    :param permission_store: Permission store used to check session-level
        access grants. ``None`` disables permission enforcement.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()

    async def mutate_signal(
        request: Request,
        session_id: str,
        response_id: str,
        signal_type: ResponseSignalType,
        active: bool,
    ) -> ResponseSignalMutationResponse:
        user_id = get_user_id(request, auth_provider)
        if permission_store is not None:
            await require_access(
                user_id, session_id, LEVEL_EDIT, permission_store, conversation_store
            )
        conversation = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conversation is None:
            raise session_not_found()

        signaled_by = attribution_user(user_id)
        signaled_at = now_epoch()
        settled = await asyncio.to_thread(
            conversation_store.set_response_signal,
            session_id,
            response_id,
            signal_type,
            active,
            signaled_by,
            signaled_at,
        )
        signal_payload = {
            name: ResponseSignalInfo(
                signal_type=signal.signal_type,
                signaled_by=signal.signaled_by,
                signaled_at=signal.signaled_at,
            )
            for name, signal in settled.items()
        }
        event = ResponseSignalChangedEvent(
            type="response.signal_changed",
            conversation_id=session_id,
            response_id=response_id,
            changed_signal=signal_type,
            active=active,
            signaled_by=signaled_by,
            signaled_at=signaled_at,
            signals=signal_payload,
        )
        session_stream.publish(session_id, event.model_dump())

        # Good activation can clear a legacy Bad, so old connected clients
        # also receive the resulting legacy flag state.
        if signal_type == "bad" or (signal_type == "good" and active):
            bad = settled.get("bad")
            legacy = ResponseFlaggedEvent(
                type="response.flagged",
                conversation_id=session_id,
                response_id=response_id,
                flagged=bad is not None,
                flagged_by=bad.signaled_by if bad is not None else signaled_by,
                flagged_at=bad.signaled_at if bad is not None else signaled_at,
            )
            session_stream.publish(session_id, legacy.model_dump())

        return ResponseSignalMutationResponse(
            conversation_id=session_id,
            response_id=response_id,
            changed_signal=signal_type,
            active=active,
            signaled_by=signaled_by,
            signaled_at=signaled_at,
            signals=signal_payload,
        )

    @router.post(
        "/sessions/{session_id}/responses/{response_id}/signal",
        response_model=ResponseSignalMutationResponse,
    )
    async def set_response_signal(
        request: Request,
        session_id: str,
        response_id: str,
        body: SetResponseSignalRequest,
    ) -> ResponseSignalMutationResponse:
        """Set or clear a human signal on a stable assistant response."""
        return await mutate_signal(request, session_id, response_id, body.signal_type, body.active)

    @router.post(
        "/sessions/{session_id}/responses/{response_id}/help",
        response_model=ResponseHelpRequestedEvent,
    )
    async def request_response_help(
        request: Request,
        session_id: str,
        response_id: str,
        body: RequestResponseEffectRequest,
    ) -> ResponseHelpRequestedEvent:
        """Broadcast a one-shot human Help effect without prompting the agent."""
        user_id = get_user_id(request, auth_provider)
        if permission_store is not None:
            await require_access(
                user_id, session_id, LEVEL_EDIT, permission_store, conversation_store
            )
        conversation = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conversation is None:
            raise session_not_found()

        event = ResponseHelpRequestedEvent(
            type="response.help_requested",
            conversation_id=session_id,
            response_id=response_id,
            request_id=body.request_id,
            requested_by=attribution_user(user_id),
            requested_at=now_epoch(),
        )
        session_stream.publish(session_id, event.model_dump())
        return event

    @router.post(
        "/sessions/{session_id}/responses/{response_id}/screenshot-request",
        response_model=ResponseScreenshotRequestedEvent,
    )
    async def request_response_screenshot(
        request: Request,
        session_id: str,
        response_id: str,
        body: RequestResponseEffectRequest,
    ) -> ResponseScreenshotRequestedEvent:
        """Broadcast a one-shot request for a human-provided screenshot."""
        user_id = get_user_id(request, auth_provider)
        if permission_store is not None:
            await require_access(
                user_id, session_id, LEVEL_EDIT, permission_store, conversation_store
            )
        conversation = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conversation is None:
            raise session_not_found()

        event = ResponseScreenshotRequestedEvent(
            type="response.screenshot_requested",
            conversation_id=session_id,
            response_id=response_id,
            request_id=body.request_id,
            requested_by=attribution_user(user_id),
            requested_at=now_epoch(),
        )
        session_stream.publish(session_id, event.model_dump())
        return event

    @router.post("/sessions/{session_id}/responses/{response_id}/flag")
    async def set_response_flag(
        request: Request,
        session_id: str,
        response_id: str,
        body: SetResponseFlagRequest,
    ) -> dict[str, object]:
        """Flag or unflag a response (turn), broadcasting the change live.

        Requires ``LEVEL_EDIT`` on the session in multi-user mode. Works
        mid-stream: ``response_id`` is allocated at turn start, before any
        ``conversation_items`` row for the turn is persisted, so the flag
        can be set the moment streaming begins.

        :param request: The incoming request, used to extract the user identity.
        :param session_id: The owning session, e.g. ``"conv_abc123"``.
        :param response_id: The turn's response id, e.g. ``"resp_xyz789"``.
        :param body: ``{"flagged": true}`` to flag, ``{"flagged": false}``
            to clear.
        :returns: ``{"response_id": str, "flagged": bool, "flagged_by":
            str | None, "flagged_at": int}``.
        :raises OmnigentError: 401/403/404 if the user lacks edit permission,
            or 404 if the session does not exist.
        """
        result = await mutate_signal(request, session_id, response_id, "bad", body.flagged)
        return {
            "response_id": response_id,
            "flagged": "bad" in result.signals,
            "flagged_by": result.signaled_by,
            "flagged_at": result.signaled_at,
        }

    return router
