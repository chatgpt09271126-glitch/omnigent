"""Tests for auto_code_cards module: fenced code block detection and pagination."""

import logging

import pytest

from omnigent.server.auto_code_cards import (
    MAX_AUTO_CODE_CARD_PAGES,
    find_code_blocks,
    generate_auto_code_cards,
)


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


def test_find_code_blocks_outer_fence_wraps_inner_example_block():
    # A 4-backtick fence wrapping markdown text that itself shows a 3-backtick
    # example. Only the outer block should be extracted; the inner ```
    # sequence must not be mistaken for the closing fence.
    text = (
        "Here's how to write a code block:\n\n"
        "````markdown\n"
        "```python\n"
        "print('hi')\n"
        "```\n"
        "````\n\n"
        "Done."
    )
    blocks = find_code_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.language == "markdown"
    assert block.lines == ["```python", "print('hi')", "```"]
    assert text[block.start_offset : block.start_offset + 4] == "````"


def test_find_code_blocks_detects_indented_fence_with_correct_offset():
    # Up to 3 leading spaces before the fence is a valid CommonMark fence.
    # The offset must point at the start of the indentation (matching
    # mdast's node.position.start.offset), not at the backticks themselves.
    text = "Note:\n\n   ```python\n   print('hi')\n   ```\n\nDone."
    blocks = find_code_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.language == "python"
    fence_line_start = text.index("```python") - 3
    assert block.start_offset == fence_line_start
    assert text[block.start_offset : block.start_offset + 3] == "   "
    assert text[block.start_offset + 3 : block.start_offset + 6] == "```"


def test_paginate_short_block_returns_single_page():
    from omnigent.server.auto_code_cards import DetectedCodeBlock, paginate_code_block

    block = DetectedCodeBlock(
        language="python", start_offset=0, lines=[f"line {i}" for i in range(10)]
    )
    pages = paginate_code_block(block)
    assert len(pages) == 1
    assert pages[0].page_index == 0
    assert pages[0].total_pages == 1
    assert pages[0].lines == block.lines


def test_paginate_long_block_overlaps_by_three_lines():
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


class _FakeArtifactStore:
    def __init__(self):
        self.puts: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(self, key: str, data: bytes) -> None:
        self.puts[key] = data

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.puts.pop(key, None)


class _FakeSnapshotStore:
    def __init__(self):
        self.added: list[dict] = []

    def add(self, **kwargs):
        self.added.append(kwargs)
        return kwargs


class _FakeBrowser:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _FakeChromium:
    """Spies on how many times a browser is launched, so tests can assert
    one launch handles many pages instead of one launch per page."""

    def __init__(self, *, fail_with: Exception | None = None):
        self.launch_calls = 0
        self._fail_with = fail_with
        self.launched_browsers: list[_FakeBrowser] = []

    async def launch(self):
        self.launch_calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        browser = _FakeBrowser()
        self.launched_browsers.append(browser)
        return browser


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _fake_async_playwright(chromium):
    def factory():
        return _FakePlaywright(chromium)

    return factory


@pytest.mark.asyncio
async def test_generate_auto_code_cards_stores_one_snapshot_per_page(monkeypatch):
    async def fake_render(page, browser):
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr("omnigent.server.code_card_rendering.render_code_card_png", fake_render)
    monkeypatch.setattr(
        "omnigent.server.auto_code_cards.async_playwright", _fake_async_playwright(_FakeChromium())
    )

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
    # Each stored page must carry its own page_index so retrieval can order
    # pages correctly even when their created_at timestamps tie.
    assert [call["page_index"] for call in snapshot_store.added] == [0, 1, 2]


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

    async def flaky_render(page, browser):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("boom: simulated render failure")
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr("omnigent.server.code_card_rendering.render_code_card_png", flaky_render)
    monkeypatch.setattr(
        "omnigent.server.auto_code_cards.async_playwright", _fake_async_playwright(_FakeChromium())
    )

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

    async def fake_render(page, browser):
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr("omnigent.server.code_card_rendering.render_code_card_png", fake_render)
    monkeypatch.setattr(
        "omnigent.server.auto_code_cards.async_playwright", _fake_async_playwright(_FakeChromium())
    )

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
    # The artifact uploaded for the page whose snapshot.add failed must be
    # cleaned up so it doesn't leak in artifact storage with no snapshot row.
    assert len(artifact_store.deleted) == 1
    assert artifact_store.deleted[0] not in artifact_store.puts
    persisted_keys = {call["artifact_key"] for call in snapshot_store.added}
    assert artifact_store.deleted[0] not in persisted_keys


@pytest.mark.asyncio
async def test_generate_auto_code_cards_launches_one_browser_for_all_pages(monkeypatch):
    """Rendering multiple pages from one message must launch Chromium once,
    not once per page."""
    render_calls: list[object] = []

    async def fake_render(page, browser):
        render_calls.append(browser)
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr("omnigent.server.code_card_rendering.render_code_card_png", fake_render)
    chromium = _FakeChromium()
    monkeypatch.setattr(
        "omnigent.server.auto_code_cards.async_playwright", _fake_async_playwright(chromium)
    )

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

    # 3 pages rendered, but only one Chromium launch for all of them.
    assert len(render_calls) == 3
    assert chromium.launch_calls == 1
    assert len({id(b) for b in render_calls}) == 1
    assert chromium.launched_browsers[0].closed is True


@pytest.mark.asyncio
async def test_generate_auto_code_cards_caps_total_pages(monkeypatch):
    """A message with far more pages than the cap only renders up to the cap."""
    render_calls: list[object] = []

    async def fake_render(page, browser):
        render_calls.append(page)
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr("omnigent.server.code_card_rendering.render_code_card_png", fake_render)
    chromium = _FakeChromium()
    monkeypatch.setattr(
        "omnigent.server.auto_code_cards.async_playwright", _fake_async_playwright(chromium)
    )

    artifact_store = _FakeArtifactStore()
    snapshot_store = _FakeSnapshotStore()
    # 10 blocks x 3 pages each (45 lines / page size 20 w/ overlap) = 30 pages,
    # well above MAX_AUTO_CODE_CARD_PAGES.
    text = "\n\n".join(
        "```python\n" + "\n".join(f"line {i}" for i in range(45)) + "\n```" for _ in range(10)
    )

    await generate_auto_code_cards(
        text=text,
        conversation_id="conv-1",
        response_id="resp-1",
        item_id="item-1",
        snapshot_store=snapshot_store,
        artifact_store=artifact_store,
    )

    assert len(render_calls) == MAX_AUTO_CODE_CARD_PAGES
    assert len(snapshot_store.added) == MAX_AUTO_CODE_CARD_PAGES
    # Still only one browser launch even though pages were capped.
    assert chromium.launch_calls == 1


@pytest.mark.asyncio
async def test_generate_auto_code_cards_logs_missing_browser_binary_once_as_warning(
    monkeypatch, caplog
):
    """A missing Chromium binary must be logged once as a plain warning (no
    per-page traceback spam), and generation must degrade silently."""
    import omnigent.server.code_card_rendering as code_card_rendering

    monkeypatch.setattr(code_card_rendering, "_missing_browser_binary_warned", False)

    launch_error = RuntimeError(
        "BrowserType.launch: Executable doesn't exist at /fake/chromium\n"
        "Please run the following command to download new browsers:\n"
        "    playwright install\n"
    )
    monkeypatch.setattr(
        "omnigent.server.auto_code_cards.async_playwright",
        _fake_async_playwright(_FakeChromium(fail_with=launch_error)),
    )

    artifact_store = _FakeArtifactStore()
    snapshot_store = _FakeSnapshotStore()
    text = "```python\nprint(1)\n```"

    with caplog.at_level(logging.WARNING):
        await generate_auto_code_cards(
            text=text,
            conversation_id="conv-1",
            response_id="resp-1",
            item_id="item-1",
            snapshot_store=snapshot_store,
            artifact_store=artifact_store,
        )
        await generate_auto_code_cards(
            text=text,
            conversation_id="conv-1",
            response_id="resp-2",
            item_id="item-1",
            snapshot_store=snapshot_store,
            artifact_store=artifact_store,
        )

    assert snapshot_store.added == []
    assert artifact_store.puts == {}
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1
    assert warning_records[0].exc_info is None
