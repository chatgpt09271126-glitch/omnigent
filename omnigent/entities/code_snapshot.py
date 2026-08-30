"""Code snapshot metadata associated with one rendered code block."""

from __future__ import annotations

import dataclasses
from typing import Literal

SnapshotCaptureType = Literal[
    "region_capture",
    "mobile_quick_capture",
    "uploaded_image",
    "clipboard_image",
    "auto_code_card",
]


@dataclasses.dataclass(frozen=True)
class CodeSnapshot:
    """Persisted metadata for an image attached to one rendered code block."""

    id: str
    conversation_id: str
    response_id: str
    item_id: str
    code_block_start_offset: int
    language: str | None
    created_by: str | None
    created_at: int
    capture_type: SnapshotCaptureType
    artifact_key: str
    content_type: str
    bytes: int
