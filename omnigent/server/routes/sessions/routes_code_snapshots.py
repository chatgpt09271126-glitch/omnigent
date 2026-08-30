"""Session-scoped CRUD routes for per-code-block snapshots."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from omnigent.db.db_models import InvalidUuidError, uuid_to_bytes
from omnigent.entities import CodeSnapshot, MessageData, SnapshotCaptureType
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runtime.content_resolver import MAX_IMAGE_UPLOAD_BYTES
from omnigent.server.auth import LEVEL_EDIT, LEVEL_READ, AuthProvider
from omnigent.server.routes._auth_helpers import (
    attribution_user,
    get_user_id,
    require_access_and_level,
)
from omnigent.server.routes._origin import require_trusted_origin
from omnigent.server.routes._sessions.helpers import _read_upload_capped
from omnigent.stores.artifact_store import ArtifactStore
from omnigent.stores.code_snapshot_store import CodeSnapshotStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.permission_store import PermissionStore

_CAPTURE_TYPES: frozenset[str] = frozenset(
    {
        "region_capture",
        "mobile_quick_capture",
        "uploaded_image",
        "clipboard_image",
    }
)


def _snapshot_dict(snapshot: CodeSnapshot) -> dict[str, object]:
    """Return public metadata without exposing the artifact-store key."""
    return {
        "id": snapshot.id,
        "object": "code_snapshot",
        "conversation_id": snapshot.conversation_id,
        "response_id": snapshot.response_id,
        "item_id": snapshot.item_id,
        "code_block_start_offset": snapshot.code_block_start_offset,
        "language": snapshot.language,
        "created_by": snapshot.created_by,
        "created_at": snapshot.created_at,
        "capture_type": snapshot.capture_type,
        "content_type": snapshot.content_type,
        "bytes": snapshot.bytes,
        "content_url": (
            f"/v1/sessions/{snapshot.conversation_id}/code-snapshots/"
            f"{snapshot.id}/content"
        ),
    }


def _detected_raster_type(content: bytes) -> str | None:
    """Recognize the safe raster formats served inline by this feature."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def register_code_snapshot_routes(
    router: APIRouter,
    *,
    conversation_store: ConversationStore,
    snapshot_store: CodeSnapshotStore | None,
    artifact_store: ArtifactStore | None,
    auth_provider: AuthProvider | None,
    permission_store: PermissionStore | None,
) -> None:
    """Register snapshot routes on the sessions router."""

    async def _authorize(request: Request, session_id: str, level: int) -> None:
        user_id = get_user_id(request, auth_provider)
        access = await require_access_and_level(
            user_id,
            session_id,
            level,
            permission_store,
            conversation_store,
        )
        if access.conversation is None:
            conversation = await asyncio.to_thread(
                conversation_store.get_conversation,
                session_id,
            )
            if conversation is None:
                raise OmnigentError("Conversation not found", code=ErrorCode.NOT_FOUND)

    def _require_stores() -> tuple[CodeSnapshotStore, ArtifactStore]:
        if snapshot_store is None or artifact_store is None:
            raise HTTPException(status_code=501, detail="code snapshot store not configured")
        return snapshot_store, artifact_store

    @router.get("/sessions/{session_id}/code-snapshots", response_model=None)
    async def list_code_snapshots(
        request: Request,
        session_id: str,
        response_id: str,
        item_id: str,
        code_block_start_offset: int,
    ) -> dict[str, object]:
        await _authorize(request, session_id, LEVEL_READ)
        store, _ = _require_stores()
        if code_block_start_offset < 0:
            raise HTTPException(
                status_code=422,
                detail="code_block_start_offset must be non-negative",
            )
        snapshots = await asyncio.to_thread(
            store.list_for_block,
            session_id,
            response_id,
            item_id,
            code_block_start_offset,
        )
        return {"object": "list", "data": [_snapshot_dict(item) for item in snapshots]}

    @router.post(
        "/sessions/{session_id}/code-snapshots",
        status_code=201,
        response_model=None,
        dependencies=[Depends(require_trusted_origin)],
    )
    async def create_code_snapshot(
        request: Request,
        session_id: str,
        file: Annotated[UploadFile, File(...)],
        response_id: Annotated[str, Form(...)],
        item_id: Annotated[str, Form(...)],
        code_block_start_offset: Annotated[int, Form(...)],
        capture_type: Annotated[str, Form(...)],
        language: Annotated[str | None, Form()] = None,
    ) -> dict[str, object]:
        await _authorize(request, session_id, LEVEL_EDIT)
        store, blobs = _require_stores()
        if not response_id or len(response_id) > 128:
            raise HTTPException(status_code=422, detail="invalid response_id")
        try:
            uuid_to_bytes(item_id)
        except InvalidUuidError as exc:
            raise HTTPException(status_code=422, detail="invalid item_id") from exc
        origin_item = await asyncio.to_thread(
            conversation_store.get_item,
            session_id,
            item_id,
        )
        if (
            origin_item is None
            or origin_item.response_id != response_id
            or not isinstance(origin_item.data, MessageData)
            or origin_item.data.role != "assistant"
        ):
            raise HTTPException(status_code=422, detail="invalid originating assistant item")
        if code_block_start_offset < 0:
            raise HTTPException(
                status_code=422,
                detail="code_block_start_offset must be non-negative",
            )
        if capture_type not in _CAPTURE_TYPES:
            raise HTTPException(status_code=422, detail="invalid capture_type")
        normalized_language = language.strip()[:128] if language and language.strip() else None
        content = await _read_upload_capped(file, MAX_IMAGE_UPLOAD_BYTES)
        content_type = _detected_raster_type(content)
        if content_type is None:
            raise HTTPException(
                status_code=415,
                detail="Only PNG, JPEG, GIF, and WebP snapshot images are supported.",
            )

        artifact_key = f"code_snapshots/{session_id}/{uuid.uuid4().hex}"
        await asyncio.to_thread(blobs.put, artifact_key, content)
        try:
            snapshot = await asyncio.to_thread(
                store.add,
                conversation_id=session_id,
                response_id=response_id,
                item_id=item_id,
                code_block_start_offset=code_block_start_offset,
                language=normalized_language,
                created_by=attribution_user(get_user_id(request, auth_provider)),
                capture_type=cast(SnapshotCaptureType, capture_type),
                artifact_key=artifact_key,
                content_type=content_type,
                bytes=len(content),
            )
        except Exception:
            await asyncio.to_thread(blobs.delete, artifact_key)
            raise
        return _snapshot_dict(snapshot)

    @router.get(
        "/sessions/{session_id}/code-snapshots/{snapshot_id}/content",
        response_model=None,
    )
    async def get_code_snapshot_content(
        request: Request,
        session_id: str,
        snapshot_id: str,
    ) -> Response:
        await _authorize(request, session_id, LEVEL_READ)
        store, blobs = _require_stores()
        snapshot = await asyncio.to_thread(store.get, snapshot_id, session_id)
        if snapshot is None:
            raise OmnigentError("Snapshot not found", code=ErrorCode.NOT_FOUND)
        content = await asyncio.to_thread(blobs.get, snapshot.artifact_key)
        extension = {
            "image/gif": "gif",
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }.get(snapshot.content_type, "png")
        return Response(
            content=content,
            media_type=snapshot.content_type,
            headers={
                "Content-Disposition": f'inline; filename="snapshot-{snapshot.id}.{extension}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, max-age=31536000, immutable",
                "ETag": f'"code-snapshot-{snapshot.id}"',
            },
        )

    @router.delete(
        "/sessions/{session_id}/code-snapshots/{snapshot_id}",
        response_model=None,
    )
    async def delete_code_snapshot(
        request: Request,
        session_id: str,
        snapshot_id: str,
    ) -> dict[str, object]:
        await _authorize(request, session_id, LEVEL_EDIT)
        store, blobs = _require_stores()
        snapshot = await asyncio.to_thread(store.delete, snapshot_id, session_id)
        if snapshot is None:
            raise OmnigentError("Snapshot not found", code=ErrorCode.NOT_FOUND)
        await asyncio.to_thread(blobs.delete, snapshot.artifact_key)
        return {"id": snapshot_id, "object": "code_snapshot.deleted", "deleted": True}
