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

from playwright.async_api import async_playwright

if TYPE_CHECKING:
    from omnigent.stores.artifact_store import ArtifactStore
    from omnigent.stores.code_snapshot_store import CodeSnapshotStore

_logger = logging.getLogger(__name__)

CODE_CARD_PAGE_SIZE = 20
CODE_CARD_PAGE_OVERLAP = 3

# Caps the total number of rendered pages across all code blocks in a single
# message, so one enormous or heavily-fenced response can't spawn unbounded
# screenshot work.
MAX_AUTO_CODE_CARD_PAGES = 20

# Bounds how many Chromium processes can be launching at once across the
# whole server process, so a burst of messages finishing together doesn't
# spawn unbounded browser processes.
_BROWSER_LAUNCH_CONCURRENCY = 4
_browser_launch_semaphore = asyncio.Semaphore(_BROWSER_LAUNCH_CONCURRENCY)

# Matches a candidate opening fence line per CommonMark: up to 3 leading
# spaces of indentation, then 3-or-more backticks, then an optional language
# tag (nothing else on the line). The closing fence must have at least as
# many backticks as the opening one, so a 4+-backtick fence wrapping an inner
# 3-backtick example is treated as one block, not split on the inner fence.
_OPEN_FENCE_RE = re.compile(r"^( {0,3})(`{3,})([A-Za-z0-9_+-]*)[ \t]*$")
_CLOSE_FENCE_RE = re.compile(r"^ {0,3}(`{3,})[ \t]*$")


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
    """Return every complete fenced code block in ``text``, in source order.

    Follows CommonMark fence semantics (matching mdast, which the frontend
    uses to compute ``node.position.start.offset``): an opening fence is
    3-or-more backticks, optionally indented up to 3 spaces, and a closing
    fence needs at least as many backticks as the opening one. This keeps a
    4+-backtick fence wrapping an inner 3-backtick example from being split
    on the inner fence, and keeps computed offsets aligned with the
    frontend's, which include the opening fence's leading indentation.
    """
    lines = text.split("\n")
    # Cumulative character offset of the start of each line in ``text``.
    line_offsets = [0] * len(lines)
    offset = 0
    for i, line in enumerate(lines):
        line_offsets[i] = offset
        offset += len(line) + 1  # account for the '\n' joining this line

    blocks: list[DetectedCodeBlock] = []
    i = 0
    while i < len(lines):
        open_match = _OPEN_FENCE_RE.match(lines[i])
        if open_match is None:
            i += 1
            continue

        indent, fence, language_raw = open_match.groups()
        fence_len = len(fence)
        language = language_raw or None
        start_offset = line_offsets[i]

        close_index = None
        for j in range(i + 1, len(lines)):
            close_match = _CLOSE_FENCE_RE.match(lines[j])
            if close_match and len(close_match.group(1)) >= fence_len:
                close_index = j
                break

        if close_index is None:
            # Unterminated fence: not a complete block, nothing further to
            # scan matches CommonMark treating the rest as part of it.
            break

        blocks.append(
            DetectedCodeBlock(
                language=language,
                start_offset=start_offset,
                lines=lines[i + 1 : close_index],
            )
        )
        i = close_index + 1

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
    snapshot_store: CodeSnapshotStore,
    artifact_store: ArtifactStore,
) -> None:
    """Detect code blocks in a finished assistant message, paginate, rasterize,
    and persist each page as an ``auto_code_card`` snapshot. Best-effort: a
    failure on one page is logged and skipped rather than aborting the rest,
    since these cards are additive on top of an already-delivered answer.

    All pages for the message share a single Chromium browser launch (capped
    at ``MAX_AUTO_CODE_CARD_PAGES`` total pages), rather than launching one
    browser per page.
    """
    # Imported lazily to avoid a circular import: code_card_rendering imports
    # CodeCardPage from this module.
    from omnigent.server.code_card_rendering import (
        is_missing_browser_binary_error,
        render_code_card_png,
        warn_missing_browser_binary_once,
    )

    pages = [page for block in find_code_blocks(text) for page in paginate_code_block(block)]
    if not pages:
        return

    if len(pages) > MAX_AUTO_CODE_CARD_PAGES:
        _logger.info(
            "Capping auto code card generation for response %s at %s pages (found %s)",
            response_id,
            MAX_AUTO_CODE_CARD_PAGES,
            len(pages),
        )
        pages = pages[:MAX_AUTO_CODE_CARD_PAGES]

    async with _browser_launch_semaphore, async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception as exc:
            if is_missing_browser_binary_error(exc):
                warn_missing_browser_binary_once(exc)
                return
            raise
        try:
            for page in pages:
                try:
                    png_bytes = await render_code_card_png(page, browser)
                    artifact_key = f"code_snapshots/{conversation_id}/{uuid.uuid4().hex}"
                    await asyncio.to_thread(artifact_store.put, artifact_key, png_bytes)
                    try:
                        await asyncio.to_thread(
                            snapshot_store.add,
                            conversation_id=conversation_id,
                            response_id=response_id,
                            item_id=item_id,
                            code_block_start_offset=page.block.start_offset,
                            language=page.block.language,
                            created_by=None,
                            capture_type="auto_code_card",
                            artifact_key=artifact_key,
                            content_type="image/png",
                            bytes=len(png_bytes),
                            page_index=page.page_index,
                        )
                    except Exception:
                        await asyncio.to_thread(artifact_store.delete, artifact_key)
                        raise
                except Exception:
                    _logger.exception(
                        "Failed to generate auto code card page %s/%s for response %s",
                        page.page_index + 1,
                        page.total_pages,
                        response_id,
                    )
        finally:
            await browser.close()
