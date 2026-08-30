"""Detect fenced code blocks in a finished assistant message and split long
blocks into overlapping, swipeable pages for auto-generated code snapshots.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnigent.stores.artifact_store import ArtifactStore
    from omnigent.stores.code_snapshot_store import CodeSnapshotStore

_logger = logging.getLogger(__name__)

CODE_CARD_PAGE_SIZE = 20
CODE_CARD_PAGE_OVERLAP = 3

# Matches a fenced code block: opening ``` + optional language tag, body,
# closing ```. Non-greedy body match so back-to-back blocks split correctly.
# The offset of group(0)'s start is the frontend's node.position.start.offset
# equivalent (character index of the opening backticks in the raw text).
_FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)\n(.*?)\n```", re.DOTALL)


@dataclasses.dataclass(frozen=True)
class DetectedCodeBlock:
    """A single fenced code block found in raw assistant message text."""

    language: str | None
    start_offset: int
    lines: list[str]


@dataclasses.dataclass(frozen=True)
class CodeCardPage:
    """One page of a (possibly split) code block, ready to rasterize."""

    block: DetectedCodeBlock
    page_index: int
    total_pages: int
    lines: list[str]


def find_code_blocks(text: str) -> list[DetectedCodeBlock]:
    """Return every complete fenced code block in ``text``, in source order."""
    blocks: list[DetectedCodeBlock] = []
    for match in _FENCE_RE.finditer(text):
        language = match.group(1) or None
        body = match.group(2)
        blocks.append(
            DetectedCodeBlock(
                language=language,
                start_offset=match.start(),
                lines=body.split("\n"),
            )
        )
    return blocks


def paginate_code_block(block: DetectedCodeBlock) -> list[CodeCardPage]:
    """Split ``block`` into fixed-size pages that overlap by
    ``CODE_CARD_PAGE_OVERLAP`` lines, so swiping forward keeps the last
    few lines of context visible instead of jumping to an unfamiliar chunk.
    """
    total = len(block.lines)
    if total <= CODE_CARD_PAGE_SIZE:
        return [CodeCardPage(block=block, page_index=0, total_pages=1, lines=block.lines)]

    stride = CODE_CARD_PAGE_SIZE - CODE_CARD_PAGE_OVERLAP
    starts = list(range(0, total - CODE_CARD_PAGE_OVERLAP, stride))
    # Ensure the final page reaches the last line exactly once, even if the
    # last stride step would otherwise leave a short trailing remainder.
    if starts[-1] + CODE_CARD_PAGE_SIZE > total:
        starts[-1] = total - CODE_CARD_PAGE_SIZE

    pages = [
        CodeCardPage(
            block=block,
            page_index=i,
            total_pages=len(starts),
            lines=block.lines[start : start + CODE_CARD_PAGE_SIZE],
        )
        for i, start in enumerate(starts)
    ]
    return pages


async def generate_auto_code_cards(
    *,
    text: str,
    conversation_id: str,
    response_id: str,
    item_id: str,
    snapshot_store: "CodeSnapshotStore",
    artifact_store: "ArtifactStore",
) -> None:
    """Detect code blocks in a finished assistant message, paginate, rasterize,
    and persist each page as an ``auto_code_card`` snapshot. Best-effort: a
    failure on one page is logged and skipped rather than aborting the rest,
    since these cards are additive on top of an already-delivered answer.
    """
    # Imported lazily to avoid a circular import: code_card_rendering imports
    # CodeCardPage from this module.
    from omnigent.server.code_card_rendering import render_code_card_png

    for block in find_code_blocks(text):
        for page in paginate_code_block(block):
            try:
                png_bytes = await render_code_card_png(page)
                artifact_key = f"code_snapshots/{conversation_id}/{uuid.uuid4().hex}"
                await asyncio.to_thread(artifact_store.put, artifact_key, png_bytes)
                await asyncio.to_thread(
                    snapshot_store.add,
                    conversation_id=conversation_id,
                    response_id=response_id,
                    item_id=item_id,
                    code_block_start_offset=block.start_offset,
                    language=block.language,
                    created_by=None,
                    capture_type="auto_code_card",
                    artifact_key=artifact_key,
                    content_type="image/png",
                    bytes=len(png_bytes),
                )
            except Exception:
                _logger.exception(
                    "Failed to generate auto code card page %s/%s for response %s",
                    page.page_index + 1,
                    page.total_pages,
                    response_id,
                )
