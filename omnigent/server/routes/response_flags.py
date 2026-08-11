"""Routes for operator response (turn) flagging.

An operator can flag an assistant response — even while it is still
streaming, since ``response_id`` is allocated before any item for the
turn is persisted — so every connected viewer (including a read-only
mobile reader) sees a live highlight on that response.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

from omnigent.db.utils import now_epoch
from omnigent.runtime import session_stream
from omnigent.server.auth import LEVEL_EDIT, AuthProvider
from omnigent.server.routes._auth_helpers import (
    attribution_user,
    get_user_id,
    require_access,
)
from omnigent.server.routes._errors import session_not_found
from omnigent.server.schemas import ResponseFlaggedEvent
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore


class SetResponseFlagRequest(BaseModel):
    """Request body for ``POST .../responses/{response_id}/flag``.

    :param flagged: ``True`` to flag the response, ``False`` to clear
        an existing flag.
    """

    flagged: bool


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
        user_id = get_user_id(request, auth_provider)
        if permission_store is not None:
            await require_access(
                user_id, session_id, LEVEL_EDIT, permission_store, conversation_store
            )
        conversation = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conversation is None:
            raise session_not_found()

        flagged_by = attribution_user(user_id)
        flagged_at = now_epoch()
        await asyncio.to_thread(
            conversation_store.set_response_flag,
            session_id,
            response_id,
            body.flagged,
            flagged_by,
            flagged_at,
        )
        event = ResponseFlaggedEvent(
            type="response.flagged",
            conversation_id=session_id,
            response_id=response_id,
            flagged=body.flagged,
            flagged_by=flagged_by,
            flagged_at=flagged_at,
        )
        session_stream.publish(session_id, event.model_dump())
        return {
            "response_id": response_id,
            "flagged": body.flagged,
            "flagged_by": flagged_by,
            "flagged_at": flagged_at,
        }

    return router
