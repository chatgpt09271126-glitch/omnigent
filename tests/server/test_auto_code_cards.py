"""Tests for auto_code_cards module: fenced code block detection and pagination."""

from omnigent.server.auto_code_cards import find_code_blocks


def test_find_code_blocks_returns_language_and_offset():
    text = "Here is the answer:\n\n```python\nprint('hi')\n```\n\nDone."
    blocks = find_code_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.language == "python"
    assert block.lines == ["print('hi')"]
    # offset must point at the opening ``` , matching the frontend's
    # node.position.start.offset convention (mdast fence-node start).
    assert text[block.start_offset : block.start_offset + 3] == "```"


def test_find_code_blocks_handles_multiple_blocks_and_no_language():
    text = "```\nraw block\n```\n\ntext between\n\n```js\nconsole.log(1)\n```"
    blocks = find_code_blocks(text)
    assert len(blocks) == 2
    assert blocks[0].language is None
    assert blocks[0].lines == ["raw block"]
    assert blocks[1].language == "js"
    assert blocks[1].lines == ["console.log(1)"]


def test_find_code_blocks_ignores_unterminated_fence():
    text = "```python\nprint('no closing fence')"
    assert find_code_blocks(text) == []


def test_paginate_short_block_returns_single_page():
    from omnigent.server.auto_code_cards import DetectedCodeBlock, paginate_code_block

    block = DetectedCodeBlock(language="python", start_offset=0, lines=[f"line {i}" for i in range(10)])
    pages = paginate_code_block(block)
    assert len(pages) == 1
    assert pages[0].page_index == 0
    assert pages[0].total_pages == 1
    assert pages[0].lines == block.lines


def test_paginate_long_block_overlaps_by_three_lines():
    from omnigent.server.auto_code_cards import (
        CODE_CARD_PAGE_OVERLAP,
        CODE_CARD_PAGE_SIZE,
        DetectedCodeBlock,
        paginate_code_block,
    )

    total_lines = 45
    block = DetectedCodeBlock(
        language=None, start_offset=0, lines=[f"line {i}" for i in range(total_lines)]
    )
    pages = paginate_code_block(block)

    assert all(p.total_pages == len(pages) for p in pages)
    assert len(pages[0].lines) == CODE_CARD_PAGE_SIZE
    # Page 2 starts CODE_CARD_PAGE_SIZE - CODE_CARD_PAGE_OVERLAP lines into the block,
    # so its first CODE_CARD_PAGE_OVERLAP lines equal page 1's last CODE_CARD_PAGE_OVERLAP lines.
    assert pages[1].lines[:CODE_CARD_PAGE_OVERLAP] == pages[0].lines[-CODE_CARD_PAGE_OVERLAP:]
    # Every source line appears in at least one page, in order, none skipped.
    stitched = pages[0].lines[:]
    for page in pages[1:]:
        stitched.extend(page.lines[CODE_CARD_PAGE_OVERLAP:])
    assert stitched == block.lines
