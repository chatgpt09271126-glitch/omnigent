"""Detect fenced code blocks in a finished assistant message and split long
blocks into overlapping, swipeable pages for auto-generated code snapshots.
"""

from __future__ import annotations

import dataclasses
import re

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
    if starts[-1] + CODE_CARD_PAGE_SIZE < total:
        starts.append(total - CODE_CARD_PAGE_SIZE)

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
