"""SQLAlchemy-backed code snapshot metadata store."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from omnigent.db.db_models import SqlCodeSnapshot, current_workspace_id
from omnigent.db.enum_codecs import (
    decode_code_snapshot_capture_type,
    encode_code_snapshot_capture_type,
)
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker, now_epoch
from omnigent.entities import CodeSnapshot, SnapshotCaptureType
from omnigent.stores.code_snapshot_store import CodeSnapshotStore


def _to_entity(row: SqlCodeSnapshot) -> CodeSnapshot:
    return CodeSnapshot(
        id=row.id,
        conversation_id=row.conversation_id,
        response_id=row.response_id,
        item_id=row.item_id,
        code_block_start_offset=row.code_block_start_offset,
        language=row.language,
        created_by=row.created_by,
        created_at=row.created_at,
        page_index=row.page_index,
        capture_type=decode_code_snapshot_capture_type(row.capture_type),  # type: ignore[arg-type]
        artifact_key=row.artifact_key,
        content_type=row.content_type,
        bytes=row.bytes,
    )


class SqlAlchemyCodeSnapshotStore(CodeSnapshotStore):
    """Relational metadata store with conversation-scoped lookups."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.code_snapshot_store",
        )

    def add(
        self,
        *,
        conversation_id: str,
        response_id: str,
        item_id: str,
        code_block_start_offset: int,
        language: str | None,
        created_by: str | None,
        capture_type: SnapshotCaptureType,
        artifact_key: str,
        content_type: str,
        bytes: int,
        page_index: int = 0,
    ) -> CodeSnapshot:
        row = SqlCodeSnapshot(
            id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            response_id=response_id,
            item_id=item_id,
            code_block_start_offset=code_block_start_offset,
            language=language,
            created_by=created_by,
            created_at=now_epoch(),
            page_index=page_index,
            capture_type=encode_code_snapshot_capture_type(capture_type),
            artifact_key=artifact_key,
            content_type=content_type,
            bytes=bytes,
        )
        with self._session("insert_snapshot") as session:
            session.add(row)
            return _to_entity(row)

    def get(self, snapshot_id: str, conversation_id: str) -> CodeSnapshot | None:
        with self._session("select_snapshot_by_id") as session:
            row = session.get(
                SqlCodeSnapshot,
                (current_workspace_id(), conversation_id, snapshot_id),
            )
            return _to_entity(row) if row is not None else None

    def list_for_block(
        self,
        conversation_id: str,
        response_id: str,
        item_id: str,
        code_block_start_offset: int,
    ) -> list[CodeSnapshot]:
        stmt = (
            select(SqlCodeSnapshot)
            .where(
                SqlCodeSnapshot.workspace_id == current_workspace_id(),
                SqlCodeSnapshot.conversation_id == conversation_id,
                SqlCodeSnapshot.response_id == response_id,
                SqlCodeSnapshot.item_id == item_id,
                SqlCodeSnapshot.code_block_start_offset == code_block_start_offset,
            )
            .order_by(SqlCodeSnapshot.page_index, SqlCodeSnapshot.created_at, SqlCodeSnapshot.id)
        )
        with self._session("list_block_snapshots") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def delete(self, snapshot_id: str, conversation_id: str) -> CodeSnapshot | None:
        with self._session("delete_snapshot") as session:
            row = session.get(
                SqlCodeSnapshot,
                (current_workspace_id(), conversation_id, snapshot_id),
            )
            if row is None:
                return None
            entity = _to_entity(row)
            session.delete(row)
            return entity

    def remove_conversation(self, conversation_id: str) -> list[CodeSnapshot]:
        stmt = select(SqlCodeSnapshot).where(
            SqlCodeSnapshot.workspace_id == current_workspace_id(),
            SqlCodeSnapshot.conversation_id == conversation_id,
        )
        with self._session("delete_conversation_snapshots") as session:
            rows = list(session.execute(stmt).scalars().all())
            entities = [_to_entity(row) for row in rows]
            session.execute(
                delete(SqlCodeSnapshot).where(
                    SqlCodeSnapshot.workspace_id == current_workspace_id(),
                    SqlCodeSnapshot.conversation_id == conversation_id,
                )
            )
            return entities
