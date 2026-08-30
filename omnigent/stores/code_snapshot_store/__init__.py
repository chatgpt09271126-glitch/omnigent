"""Persistence contract for per-code-block snapshot metadata."""

from abc import ABC, abstractmethod

from omnigent.entities import CodeSnapshot, SnapshotCaptureType


class CodeSnapshotStore(ABC):
    """Manage snapshot metadata while the artifact store owns image bytes."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
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
    ) -> CodeSnapshot:
        """Persist and return one snapshot metadata row."""
        ...

    @abstractmethod
    def get(self, snapshot_id: str, conversation_id: str) -> CodeSnapshot | None:
        """Return a snapshot scoped to its conversation."""
        ...

    @abstractmethod
    def list_for_block(
        self,
        conversation_id: str,
        response_id: str,
        item_id: str,
        code_block_start_offset: int,
    ) -> list[CodeSnapshot]:
        """List snapshots for exactly one rendered code block."""
        ...

    @abstractmethod
    def delete(self, snapshot_id: str, conversation_id: str) -> CodeSnapshot | None:
        """Delete and return one snapshot scoped to its conversation."""
        ...

    @abstractmethod
    def remove_conversation(self, conversation_id: str) -> list[CodeSnapshot]:
        """Delete and return all snapshot rows owned by a conversation."""
        ...
