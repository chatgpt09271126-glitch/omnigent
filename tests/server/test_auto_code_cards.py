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
    # Every source line appears in at least one page, in order, none skipped.
    # Calculate actual overlap for each consecutive pair of pages by matching line content.
    stitched = pages[0].lines[:]
    for i in range(1, len(pages)):
        prev_page = pages[i - 1]
        curr_page = pages[i]
        # Find the overlap: how many lines from the start of curr_page match the end of prev_page
        overlap = 0
        for j in range(1, min(len(prev_page.lines), len(curr_page.lines)) + 1):
            if prev_page.lines[-j:] == curr_page.lines[:j]:
                overlap = j
        stitched.extend(curr_page.lines[overlap:])
    assert stitched == block.lines


def test_paginate_every_page_has_exact_size_when_total_exceeds_size():
    """Regression test: all pages (including the last) must have exactly CODE_CARD_PAGE_SIZE lines
    when the total block is long enough. Previously, the last page could end up shorter than
    CODE_CARD_PAGE_SIZE due to dead code in the fallback condition."""
    from omnigent.server.auto_code_cards import (
        CODE_CARD_PAGE_SIZE,
        DetectedCodeBlock,
        paginate_code_block,
    )

    total_lines = 45
    block = DetectedCodeBlock(
        language=None, start_offset=0, lines=[f"line {i}" for i in range(total_lines)]
    )
    pages = paginate_code_block(block)

    # Regression: every page must have exactly CODE_CARD_PAGE_SIZE lines
    for i, page in enumerate(pages):
        assert len(page.lines) == CODE_CARD_PAGE_SIZE, (
            f"Page {i} has {len(page.lines)} lines, expected {CODE_CARD_PAGE_SIZE}"
        )


import pytest

from omnigent.server.auto_code_cards import generate_auto_code_cards


class _FakeArtifactStore:
    def __init__(self):
        self.puts: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self.puts[key] = data


class _FakeSnapshotStore:
    def __init__(self):
        self.added: list[dict] = []

    def add(self, **kwargs):
        self.added.append(kwargs)
        return kwargs


@pytest.mark.asyncio
async def test_generate_auto_code_cards_stores_one_snapshot_per_page(monkeypatch):
    async def fake_render(page):
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr("omnigent.server.code_card_rendering.render_code_card_png", fake_render)

    artifact_store = _FakeArtifactStore()
    snapshot_store = _FakeSnapshotStore()
    text = "answer:\n\n```python\n" + "\n".join(f"line {i}" for i in range(45)) + "\n```\n"

    await generate_auto_code_cards(
        text=text,
        conversation_id="conv-1",
        response_id="resp-1",
        item_id="item-1",
        snapshot_store=snapshot_store,
        artifact_store=artifact_store,
    )

    # 45 lines, page size 20, overlap 3 -> 3 pages (per Task 2's pagination test).
    assert len(snapshot_store.added) == 3
    for call in snapshot_store.added:
        assert call["capture_type"] == "auto_code_card"
        assert call["conversation_id"] == "conv-1"
        assert call["response_id"] == "resp-1"
        assert call["item_id"] == "item-1"
        assert call["content_type"] == "image/png"
        assert call["artifact_key"] in artifact_store.puts
        assert call["bytes"] == len(artifact_store.puts[call["artifact_key"]])


@pytest.mark.asyncio
async def test_generate_auto_code_cards_no_op_when_no_code_blocks(monkeypatch):
    artifact_store = _FakeArtifactStore()
    snapshot_store = _FakeSnapshotStore()

    await generate_auto_code_cards(
        text="just prose, no code",
        conversation_id="conv-1",
        response_id="resp-1",
        item_id="item-1",
        snapshot_store=snapshot_store,
        artifact_store=artifact_store,
    )

    assert snapshot_store.added == []
    assert artifact_store.puts == {}


@pytest.mark.asyncio
async def test_generate_auto_code_cards_isolates_one_page_failure(monkeypatch):
    """A render failure on one page must not abort the other pages."""
    call_count = {"n": 0}

    async def flaky_render(page):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("boom: simulated render failure")
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr("omnigent.server.code_card_rendering.render_code_card_png", flaky_render)

    artifact_store = _FakeArtifactStore()
    snapshot_store = _FakeSnapshotStore()
    text = "answer:\n\n```python\n" + "\n".join(f"line {i}" for i in range(45)) + "\n```\n"

    await generate_auto_code_cards(
        text=text,
        conversation_id="conv-1",
        response_id="resp-1",
        item_id="item-1",
        snapshot_store=snapshot_store,
        artifact_store=artifact_store,
    )

    # 3 pages total, page 2 fails to render -> only 2 snapshots persisted.
    assert call_count["n"] == 3
    assert len(snapshot_store.added) == 2


@pytest.mark.asyncio
async def test_generate_auto_code_cards_isolates_one_page_store_failure(monkeypatch):
    """A snapshot_store.add failure on one page must not abort the other pages."""

    async def fake_render(page):
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr("omnigent.server.code_card_rendering.render_code_card_png", fake_render)

    artifact_store = _FakeArtifactStore()

    class _FlakySnapshotStore(_FakeSnapshotStore):
        def __init__(self):
            super().__init__()
            self.call_count = 0

        def add(self, **kwargs):
            self.call_count += 1
            if self.call_count == 2:
                raise RuntimeError("boom: simulated store failure")
            return super().add(**kwargs)

    snapshot_store = _FlakySnapshotStore()
    text = "answer:\n\n```python\n" + "\n".join(f"line {i}" for i in range(45)) + "\n```\n"

    await generate_auto_code_cards(
        text=text,
        conversation_id="conv-1",
        response_id="resp-1",
        item_id="item-1",
        snapshot_store=snapshot_store,
        artifact_store=artifact_store,
    )

    # 3 pages total, page 2's store.add raises -> 2 snapshots persisted (1st and 3rd).
    assert len(snapshot_store.added) == 2
