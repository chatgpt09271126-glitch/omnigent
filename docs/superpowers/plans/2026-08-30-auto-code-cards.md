# Auto Code Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically turn code blocks in agent responses into zoomable snapshot images (reusing the existing manual code-snapshot pipeline), generated asynchronously after a message finishes streaming, with a tap-through viewer and a fixed mobile navigation gesture.

**Architecture:** A new backend module parses fenced code blocks out of a finished assistant message using the same character-offset convention the frontend already uses (`node.position.start.offset` into the raw markdown), splits long blocks into overlapping fixed-size line windows, rasterizes each window to PNG via headless Playwright, and persists each as a `CodeSnapshot` row (new `capture_type = "auto_code_card"`) through the existing `ArtifactStore` + `CodeSnapshotStore` — the same two calls the manual upload route already makes. Generation is triggered by a fire-and-forget `asyncio.create_task` at the point the assistant message is persisted (mirroring `background_session_titles.py`), so it never blocks the chat response. On the frontend, tapping a code block queries `list_for_block` for that offset and opens `SnapshotViewer` directly (bypassing the grid); the grid still lists auto cards alongside manual ones. `SnapshotViewer` also gets a real swipe-to-navigate gesture and visible prev/next chevrons (currently `sr-only`).

**Tech Stack:** Python/FastAPI backend, SQLAlchemy + Alembic, Playwright (promoted from test-only to a runtime dependency) for rasterization, React/TypeScript frontend with `@tanstack/react-query`.

## Global Constraints

- Page size: 20 lines per card. Overlap: 3 lines between consecutive cards. Both are named constants, not configurable per-request.
- Manual screenshot capture (`region_capture`, `mobile_quick_capture`, `uploaded_image`, `clipboard_image`) is not modified by any task in this plan.
- Auto-card generation must never delay the assistant message becoming visible in the chat — it only starts after the message is already persisted.
- Card offset (`code_block_start_offset`) must exactly match the frontend's convention: the character offset of the fence-opening backticks within the raw persisted message text (see Task 2).
- Rasterized images are real PNGs uploaded through the existing `ArtifactStore`, not client-rendered DOM/SVG.

---

## File Structure

New files:
- `omnigent/server/auto_code_cards.py` — fence parsing, pagination, and the async orchestration entry point (`generate_auto_code_cards`).
- `omnigent/server/code_card_rendering.py` — HTML template + Playwright-based rasterization (`render_code_card_png`).
- `tests/server/test_auto_code_cards.py` — unit tests for fence parsing and pagination.
- `tests/server/test_code_card_rendering.py` — unit test for rasterization producing valid PNG bytes.
- `omnigent/db/migrations/versions/<new>_add_auto_code_card_capture_type.py` — widens the `capture_type` check constraint.

Modified files:
- `omnigent/entities/code_snapshot.py` — add `"auto_code_card"` to `SnapshotCaptureType`.
- `omnigent/db/enum_codecs.py` — add `"auto_code_card": 5` to `CODE_SNAPSHOT_CAPTURE_TYPE`.
- `omnigent/db/db_models.py` — widen `ck_code_snapshots_capture_type` to `(1, 2, 3, 4, 5)`.
- `pyproject.toml` — move `playwright` from test-only extras to a core runtime dependency.
- `omnigent/server/routes/_sessions/orchestration.py` — fire `generate_auto_code_cards` after `_flush_relay_text` (relay path, ~line 6171) and after `_persist_external_conversation_item` (native path, ~line 2104).
- `web/src/lib/codeSnapshotsApi.ts` — add `listCodeSnapshotsForBlock` if not already exposed as a typed client call (confirm during Task 6; the store method `list_for_block` exists server-side but the HTTP route/client wrapper may not yet exist).
- `web/src/components/ai-elements/message.tsx` — code block tap opens the viewer directly instead of (or in addition to) the toolbar controls.
- `web/src/components/code-snapshots/CodeSnapshots.tsx` — swipe gesture + visible chevrons on `SnapshotViewer`; new `AutoCardViewer` entry point scoped to one block's cards.

---

## Task 1: Add the `auto_code_card` capture type

**Files:**
- Modify: `omnigent/entities/code_snapshot.py:8-9`
- Modify: `omnigent/db/enum_codecs.py:67-72`
- Modify: `omnigent/db/db_models.py:1197-1201`
- Create: `omnigent/db/migrations/versions/zb1c2d3e4f5a_add_auto_code_card_capture_type.py`
- Test: `tests/db/test_enum_codecs.py` (add a case if this file exists; otherwise `tests/db/test_code_snapshot_enum_codecs.py`)

**Interfaces:**
- Produces: `SnapshotCaptureType` now includes `"auto_code_card"`; `encode_code_snapshot_capture_type("auto_code_card") == 5`; `decode_code_snapshot_capture_type(5) == "auto_code_card"`.

- [ ] **Step 1: Write the failing test for the new enum value**

```python
# tests/db/test_code_snapshot_enum_codecs.py
from omnigent.db.enum_codecs import decode_code_snapshot_capture_type, encode_code_snapshot_capture_type


def test_auto_code_card_capture_type_round_trips():
    assert encode_code_snapshot_capture_type("auto_code_card") == 5
    assert decode_code_snapshot_capture_type(5) == "auto_code_card"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_code_snapshot_enum_codecs.py -v`
Expected: FAIL — `KeyError`/`ValueError` from `encode_code_snapshot_capture_type`, since `"auto_code_card"` isn't in `CODE_SNAPSHOT_CAPTURE_TYPE` yet.

- [ ] **Step 3: Add the enum value and widen the Literal**

In `omnigent/entities/code_snapshot.py`:
```python
SnapshotCaptureType = Literal[
    "region_capture", "mobile_quick_capture", "uploaded_image", "clipboard_image", "auto_code_card"
]
```

In `omnigent/db/enum_codecs.py`:
```python
CODE_SNAPSHOT_CAPTURE_TYPE: dict[str, int] = {
    "region_capture": 1,
    "mobile_quick_capture": 2,
    "uploaded_image": 3,
    "clipboard_image": 4,
    "auto_code_card": 5,
}
```

In `omnigent/db/db_models.py`, update the check constraint:
```python
CheckConstraint(
    "capture_type IN (1, 2, 3, 4, 5)",
    name="ck_code_snapshots_capture_type",
),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/db/test_code_snapshot_enum_codecs.py -v`
Expected: PASS

- [ ] **Step 5: Write the Alembic migration to widen the DB constraint**

```python
# omnigent/db/migrations/versions/zb1c2d3e4f5a_add_auto_code_card_capture_type.py
"""Add auto_code_card capture type to code_snapshots.

Revision ID: zb1c2d3e4f5a
Revises: za2b3c4d5e6f
Create Date: 2026-08-30 00:00:00.000000

Widens the ``ck_code_snapshots_capture_type`` check constraint from
``(1, 2, 3, 4)`` to ``(1, 2, 3, 4, 5)`` so automatically generated code
cards (``auto_code_card`` = 5) can be stored alongside manually captured
snapshots. Additive. No existing data needs backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "zb1c2d3e4f5a"
down_revision: str | None = "za2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Widen ``ck_code_snapshots_capture_type`` to allow value 5."""
    with op.batch_alter_table("code_snapshots") as batch_op:
        batch_op.drop_constraint("ck_code_snapshots_capture_type", type_="check")
        batch_op.create_check_constraint(
            "ck_code_snapshots_capture_type",
            "capture_type IN (1, 2, 3, 4, 5)",
        )


def downgrade() -> None:
    """Narrow ``ck_code_snapshots_capture_type`` back to ``(1, 2, 3, 4)``."""
    with op.batch_alter_table("code_snapshots") as batch_op:
        batch_op.drop_constraint("ck_code_snapshots_capture_type", type_="check")
        batch_op.create_check_constraint(
            "ck_code_snapshots_capture_type",
            "capture_type IN (1, 2, 3, 4)",
        )
```

Confirm `za2b3c4d5e6f` is still the current migration head before setting `down_revision` — run `alembic heads` from the repo root and use whatever the actual head revision id is if it has moved since this plan was written.

- [ ] **Step 6: Run the migration against a scratch DB and verify**

Run: `alembic upgrade head` (against a disposable/dev DB per repo's existing migration test setup), then `alembic downgrade -1` to confirm the downgrade path also works.
Expected: both succeed with no errors.

- [ ] **Step 7: Commit**

```bash
git add omnigent/entities/code_snapshot.py omnigent/db/enum_codecs.py omnigent/db/db_models.py \
  omnigent/db/migrations/versions/zb1c2d3e4f5a_add_auto_code_card_capture_type.py \
  tests/db/test_code_snapshot_enum_codecs.py
git commit -m "feat(db): add auto_code_card capture type for code snapshots"
```

---

## Task 2: Fenced code-block parser with sliding-window pagination

**Files:**
- Create: `omnigent/server/auto_code_cards.py`
- Test: `tests/server/test_auto_code_cards.py`

**Interfaces:**
- Produces:
  ```python
  @dataclasses.dataclass(frozen=True)
  class DetectedCodeBlock:
      language: str | None
      start_offset: int          # char offset of the opening ``` in the raw text
      lines: list[str]           # code content, fence lines excluded

  @dataclasses.dataclass(frozen=True)
  class CodeCardPage:
      block: DetectedCodeBlock
      page_index: int            # 0-based
      total_pages: int
      lines: list[str]           # this page's slice of block.lines

  CODE_CARD_PAGE_SIZE: int = 20
  CODE_CARD_PAGE_OVERLAP: int = 3

  def find_code_blocks(text: str) -> list[DetectedCodeBlock]: ...
  def paginate_code_block(block: DetectedCodeBlock) -> list[CodeCardPage]: ...
  ```
- Consumes: nothing from other tasks (pure text-processing, no I/O).

- [ ] **Step 1: Write the failing test for fence detection and offset**

```python
# tests/server/test_auto_code_cards.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/server/test_auto_code_cards.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'omnigent.server.auto_code_cards'`

- [ ] **Step 3: Implement fence detection**

```python
# omnigent/server/auto_code_cards.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/server/test_auto_code_cards.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing test for sliding-window pagination**

```python
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
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/server/test_auto_code_cards.py -v`
Expected: FAIL — `ImportError: cannot import name 'paginate_code_block'`

- [ ] **Step 7: Implement sliding-window pagination**

Append to `omnigent/server/auto_code_cards.py`:
```python
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
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/server/test_auto_code_cards.py -v`
Expected: PASS (5 tests)

- [ ] **Step 9: Commit**

```bash
git add omnigent/server/auto_code_cards.py tests/server/test_auto_code_cards.py
git commit -m "feat: parse fenced code blocks and paginate with sliding-window overlap"
```

---

## Task 3: Rasterize a code page to PNG via headless Playwright

**Files:**
- Create: `omnigent/server/code_card_rendering.py`
- Test: `tests/server/test_code_card_rendering.py`
- Modify: `pyproject.toml` (move `playwright` out of test-only extras into core dependencies)

**Interfaces:**
- Consumes: `CodeCardPage` from Task 2 (`omnigent.server.auto_code_cards`).
- Produces:
  ```python
  async def render_code_card_png(page: CodeCardPage) -> bytes: ...
  ```

- [ ] **Step 1: Move `playwright` to a runtime dependency**

Read `pyproject.toml`'s dependency list and test-only extras (around the `playwright` lines identified earlier). Move the `playwright` entry out of the `[project.optional-dependencies]` test group and into `[project.dependencies]` (or the project's core dependency list — match however `boto3`/other runtime deps are declared in this file). Leave `pytest-playwright-visual-snapshot` in the test extras untouched — that one stays test-only.

- [ ] **Step 2: Write the failing test for PNG output**

```python
# tests/server/test_code_card_rendering.py
import pytest

from omnigent.server.auto_code_cards import CodeCardPage, DetectedCodeBlock
from omnigent.server.code_card_rendering import render_code_card_png

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_render_code_card_png_produces_valid_png():
    block = DetectedCodeBlock(language="python", start_offset=0, lines=["def f():", "    return 1"])
    page = CodeCardPage(block=block, page_index=0, total_pages=1, lines=block.lines)

    png_bytes = await render_code_card_png(page)

    assert png_bytes.startswith(_PNG_MAGIC)
    assert len(png_bytes) > 100  # sanity: not an empty/blank image
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/server/test_code_card_rendering.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'omnigent.server.code_card_rendering'`

- [ ] **Step 4: Implement rasterization**

```python
# omnigent/server/code_card_rendering.py
"""Rasterize a code card page to a real PNG image via headless Playwright,
matching the dark, generously-spaced editor look of a manually captured
desktop code screenshot.
"""

from __future__ import annotations

import html

from playwright.async_api import async_playwright

from omnigent.server.auto_code_cards import CodeCardPage

_CARD_WIDTH_PX = 900
_LINE_HEIGHT_PX = 28
_VERTICAL_PADDING_PX = 48

_TEMPLATE = """
<html>
<head>
<style>
  body {{ margin: 0; background: #0d1117; }}
  .card {{
    width: {width}px;
    padding: {padding}px 32px;
    background: #0d1117;
    font-family: 'SF Mono', Menlo, Consolas, monospace;
    font-size: 18px;
    line-height: {line_height}px;
    color: #e6edf3;
    white-space: pre;
  }}
  .lang {{ color: #7d8590; font-size: 14px; margin-bottom: 12px; }}
</style>
</head>
<body>
  <div class="card">
    <div class="lang">{language}</div>
    <div class="code">{code}</div>
  </div>
</body>
</html>
"""


def _render_html(page: CodeCardPage) -> str:
    code = html.escape("\n".join(page.lines))
    language = html.escape(page.block.language or "")
    height = _VERTICAL_PADDING_PX * 2 + len(page.lines) * _LINE_HEIGHT_PX + 40
    return _TEMPLATE.format(
        width=_CARD_WIDTH_PX,
        padding=_VERTICAL_PADDING_PX,
        line_height=_LINE_HEIGHT_PX,
        language=language,
        code=code,
    ), height


async def render_code_card_png(page: CodeCardPage) -> bytes:
    """Render one code card page to PNG bytes using a headless browser."""
    html_content, height = _render_html(page)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            browser_page = await browser.new_page(viewport={"width": _CARD_WIDTH_PX, "height": height})
            await browser_page.set_content(html_content)
            return await browser_page.screenshot(type="png", full_page=True)
        finally:
            await browser.close()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/server/test_code_card_rendering.py -v`
Expected: PASS. If Playwright's Chromium binary isn't installed in the dev environment, run `playwright install chromium` first — check whether the repo's `just ensure`/CI setup already provisions this (grep `playwright install` across `.github/` and `justfile`) and add it there if not, so CI doesn't fail on a missing browser binary.

- [ ] **Step 6: Commit**

```bash
git add omnigent/server/code_card_rendering.py tests/server/test_code_card_rendering.py pyproject.toml
git commit -m "feat: rasterize code card pages to PNG via headless Playwright"
```

---

## Task 4: Orchestrate detection → pagination → rasterization → storage

**Files:**
- Modify: `omnigent/server/auto_code_cards.py` (add the orchestration entry point)
- Test: `tests/server/test_auto_code_cards.py` (add orchestration test with fakes)

**Interfaces:**
- Consumes:
  - `find_code_blocks`, `paginate_code_block` (Task 2, same module)
  - `render_code_card_png` (Task 3, `omnigent.server.code_card_rendering`)
  - `CodeSnapshotStore.add(*, conversation_id, response_id, item_id, code_block_start_offset, language, created_by, capture_type, artifact_key, content_type, bytes) -> CodeSnapshot` (existing, `omnigent.stores.code_snapshot_store`)
  - `ArtifactStore.put(key: str, data: bytes) -> None` (existing, `omnigent.stores.artifact_store`)
- Produces:
  ```python
  async def generate_auto_code_cards(
      *,
      text: str,
      conversation_id: str,
      response_id: str,
      item_id: str,
      snapshot_store: CodeSnapshotStore,
      artifact_store: ArtifactStore,
  ) -> None: ...
  ```
  This is the function later tasks fire via `asyncio.create_task`.

- [ ] **Step 1: Write the failing test using fake stores**

```python
# append to tests/server/test_auto_code_cards.py
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

    monkeypatch.setattr("omnigent.server.auto_code_cards.render_code_card_png", fake_render)

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/server/test_auto_code_cards.py -v`
Expected: FAIL — `ImportError: cannot import name 'generate_auto_code_cards'`

- [ ] **Step 3: Implement orchestration**

Append to `omnigent/server/auto_code_cards.py`:
```python
import asyncio
import logging
import uuid

from omnigent.server.code_card_rendering import render_code_card_png
from omnigent.stores import ArtifactStore, CodeSnapshotStore

_logger = logging.getLogger(__name__)


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
    """
    for block in find_code_blocks(text):
        for page in paginate_code_block(block):
            try:
                png_bytes = await render_code_card_png(page)
                artifact_key = f"code_snapshots/{conversation_id}/{uuid.uuid4().hex}"
                await asyncio.to_thread(artifact_store.put, artifact_key, png_bytes)
                snapshot_store.add(
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
```

Note: `snapshot_store.add(...)` signature above must match exactly what `CodeSnapshotStore.add` in `omnigent/stores/code_snapshot_store/__init__.py` declares — re-read that file during implementation and adjust argument names if they differ from what's assumed here (the investigation found `created_by` as a parameter but didn't confirm every keyword name character-for-character).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/server/test_auto_code_cards.py -v`
Expected: PASS (7 tests total)

- [ ] **Step 5: Commit**

```bash
git add omnigent/server/auto_code_cards.py tests/server/test_auto_code_cards.py
git commit -m "feat: orchestrate auto code card detection, rendering, and storage"
```

---

## Task 5: Fire generation when an assistant message finishes streaming

**Files:**
- Modify: `omnigent/server/routes/_sessions/orchestration.py` (relay path ~line 6171, native path ~line 2104)
- Test: manual (see verification below) — this task wires an existing tested function into a live SSE/persistence code path; the unit-testable logic already has coverage from Task 4.

**Interfaces:**
- Consumes: `generate_auto_code_cards` (Task 4, `omnigent.server.auto_code_cards`).

- [ ] **Step 1: Read the exact flush/persist call sites**

Open `omnigent/server/routes/_sessions/orchestration.py` and re-read:
- Around line 6171-6187, the terminal-event branch that calls `_flush_relay_text(conversation_store, session_id, text_acc, current_response_id, _final_model)`.
- Around line 2104, `_persist_external_conversation_item(...)` for the native harness path.

Confirm what each returns — specifically whether `_flush_relay_text` returns the persisted item's `item_id`, or whether that has to be read back from `conversation_store` after the call. The auto-card generator needs a real `item_id` to attach snapshots to (matching what the manual upload route validates against — see `create_code_snapshot`'s `origin_item` check in `omnigent/server/routes/sessions/routes_code_snapshots.py`).

- [ ] **Step 2: Fire the background task after the relay flush**

Immediately after the existing `_flush_relay_text(...)` call in the terminal-event branch, add:
```python
if text_acc.strip():
    asyncio.create_task(
        generate_auto_code_cards(
            text=text_acc,
            conversation_id=session_id,
            response_id=current_response_id,
            item_id=persisted_item_id,  # from Step 1's investigation
            snapshot_store=conversation_store.code_snapshot_store,  # confirm exact attribute name on conversation_store during implementation
            artifact_store=artifact_store,  # confirm how this route already accesses the shared ArtifactStore instance (same one routes_code_snapshots.py uses)
        )
    )
```
Import `generate_auto_code_cards` at the top of the file alongside the other `omnigent.server.*` imports.

- [ ] **Step 3: Fire the background task after the native persist path**

Apply the equivalent call after `_persist_external_conversation_item(...)` at line 2104, using whatever variables that function's surrounding scope already has for the message text, session/conversation id, response id, and persisted item id.

- [ ] **Step 4: Manual verification**

Start a local dev server (per the repo's existing dev workflow), open a session with Interview Mode or any chat, send a prompt that returns a code block, and confirm:
- The chat response appears immediately, with no added delay.
- A few seconds later, querying the snapshot list for that conversation (e.g. via the existing manual-gallery UI, before Task 6 wires up the tap-through) shows new `auto_code_card` entries.
- Check server logs for any `Failed to generate auto code card page` exceptions and resolve them if the manual test doesn't produce snapshots.

- [ ] **Step 5: Commit**

```bash
git add omnigent/server/routes/_sessions/orchestration.py
git commit -m "feat: trigger auto code card generation on assistant message completion"
```

---

## Task 6: Frontend — tap a code block to open the viewer directly

**Files:**
- Modify: `web/src/lib/codeSnapshotsApi.ts` — confirm/add a typed client call for listing snapshots scoped to one block.
- Modify: `web/src/components/code-snapshots/CodeSnapshots.tsx` — add an entry point that opens `SnapshotViewer` pre-scoped to one block's cards, bypassing `SnapshotGallery`.
- Modify: `web/src/components/ai-elements/message.tsx` — wire the code block tap handler.
- Test: `web/src/components/code-snapshots/CodeSnapshots.test.tsx` — add a test for the scoped-open path.

**Interfaces:**
- Consumes: backend `list_for_block` behavior (already exists server-side via `CodeSnapshotStore.list_for_block`); confirm whether an HTTP route already exposes it, or whether one needs adding to `omnigent/server/routes/sessions/routes_code_snapshots.py` alongside `create_code_snapshot` — check this first, since the investigation only confirmed the store method exists, not that it's reachable over HTTP.
- Produces: a new exported component, e.g. `AutoCardViewer({ origin, onOpenChange })`, that fetches snapshots for exactly `origin.codeBlockStartOffset` and renders `SnapshotViewer` starting at page 0.

- [ ] **Step 1: Confirm or add the list-for-block HTTP route**

Grep `omnigent/server/routes/sessions/routes_code_snapshots.py` for a GET route filtering by `code_block_start_offset`. If none exists, add one following the same pattern as `create_code_snapshot` (auth/origin validation, then `store.list_for_block(conversation_id, response_id, item_id, code_block_start_offset)`), returning the list as JSON. Add a corresponding client function in `web/src/lib/codeSnapshotsApi.ts`:
```ts
export async function listCodeSnapshotsForBlock(
  origin: CodeSnapshotOrigin,
): Promise<CodeSnapshot[]> {
  const response = await hostFetch(
    `/v1/sessions/${origin.conversationId}/code-snapshots?response_id=${origin.responseId}&item_id=${origin.itemId}&code_block_start_offset=${origin.codeBlockStartOffset}`,
  );
  if (!response.ok) throw new Error(`Failed to list code snapshots: ${response.status}`);
  return response.json();
}
```
(Match the exact query-param/route shape to whatever the backend route actually accepts once Step 1's grep result is known — this is a starting shape, not a guess to ship blind.)

- [ ] **Step 2: Write the failing test for tap-to-open behavior**

```tsx
// append to web/src/components/code-snapshots/CodeSnapshots.test.tsx
it("opens the viewer directly for a code block's own snapshots, without the grid", async () => {
  // Arrange: mock listCodeSnapshotsForBlock to return 2 auto_code_card snapshots
  // for a given origin. Render the chat message containing that code block.
  // Act: click/tap the code block.
  // Assert: SnapshotViewer (data-testid="snapshot-viewer") is visible,
  // SnapshotGallery (data-testid="snapshot-gallery") is NOT rendered,
  // and the header shows "1 of 2".
});
```

Follow this file's existing test setup conventions (its current tests already mock `createCodeSnapshot` and render `CodeSnapshots`/`SnapshotViewer` — mirror that pattern exactly rather than introducing a new test harness).

- [ ] **Step 3: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/code-snapshots/CodeSnapshots.test.tsx`
Expected: FAIL — the tap handler doesn't exist yet, so the assertion that `snapshot-viewer` appears fails.

- [ ] **Step 4: Implement the scoped viewer entry point**

In `CodeSnapshots.tsx`, add:
```tsx
export function AutoCardViewer({
  origin,
  onOpenChange,
}: {
  origin: CodeSnapshotOrigin;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: snapshots = [] } = useQuery({
    queryKey: ["code-snapshots-for-block", origin.conversationId, origin.responseId, origin.itemId, origin.codeBlockStartOffset],
    queryFn: () => listCodeSnapshotsForBlock(origin),
  });

  if (snapshots.length === 0) return null;

  return (
    <SnapshotViewer
      snapshots={snapshots}
      index={0}
      onIndexChange={() => {}}
      onBack={() => onOpenChange(false)}
      onClose={() => onOpenChange(false)}
    />
  );
}
```
Match `SnapshotViewer`'s actual prop names exactly — re-read its function signature in this file before wiring this up, since the investigation summarized its behavior but the plan is written from that summary, not the literal signature.

In `message.tsx`, wire the code block's click handler (around the `ChatCodeBlockPre` component, ~line 498-534) to open `AutoCardViewer` with `origin = { conversationId, responseId, itemId, codeBlockStartOffset, language, canEdit: false }` when `snapshotsEnabled` is true and the user taps the code block itself (not the existing toolbar buttons, which should keep their current behavior).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd web && npx vitest run src/components/code-snapshots/CodeSnapshots.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/codeSnapshotsApi.ts web/src/components/code-snapshots/CodeSnapshots.tsx \
  web/src/components/ai-elements/message.tsx web/src/components/code-snapshots/CodeSnapshots.test.tsx \
  omnigent/server/routes/sessions/routes_code_snapshots.py
git commit -m "feat(web): tap a code block to open its auto card sequence directly"
```

---

## Task 7: Show a pending indicator until cards are ready

**Files:**
- Modify: `web/src/components/ai-elements/message.tsx`

**Interfaces:**
- Consumes: `listCodeSnapshotsForBlock` (Task 6).

- [ ] **Step 1: Add a short-poll pending state**

Since generation is async (Task 5, ~1-3s after the message completes) and there's no push notification for "cards ready" in scope for this plan, poll `listCodeSnapshotsForBlock` a few times after the message finishes: e.g. on mount of a just-completed assistant message's code block, poll every 1.5s up to 5 attempts, stop polling once a non-empty result arrives or the attempts are exhausted. While polling and empty, show a small pending affordance (a subtle spinner or dot) on the code block instead of making it tappable; once snapshots exist, make it tappable per Task 6.

```tsx
function useAutoCardReadiness(origin: CodeSnapshotOrigin | null): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!origin) return;
    let cancelled = false;
    let attempts = 0;
    const poll = async () => {
      if (cancelled) return;
      const snapshots = await listCodeSnapshotsForBlock(origin).catch(() => []);
      if (cancelled) return;
      if (snapshots.length > 0) {
        setReady(true);
        return;
      }
      attempts += 1;
      if (attempts < 5) setTimeout(poll, 1500);
    };
    void poll();
    return () => {
      cancelled = true;
    };
  }, [origin]);
  return ready;
}
```

Wire this into the code block's rendered state so the tap target (Task 6) only activates once `ready` is true, and shows the pending affordance otherwise. If the 5-attempt/7.5s window elapses with no cards (e.g. the block had no fenced code, or generation failed), the block simply stays non-tappable with no visible error — consistent with Task 4's best-effort/non-blocking design.

- [ ] **Step 2: Manual verification**

Send a prompt producing a long code block in a live session; confirm the code block shows a pending affordance immediately, then becomes tappable within a few seconds without any page reload, and opens directly into the multi-page swipeable viewer.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/ai-elements/message.tsx
git commit -m "feat(web): show pending state on code blocks until auto cards are ready"
```

---

## Task 8: Fix `SnapshotViewer`'s broken mobile navigation

**Files:**
- Modify: `web/src/components/code-snapshots/CodeSnapshots.tsx` (prev/next buttons ~line 828-841; pointer handlers ~line 622-690)
- Test: `web/src/components/code-snapshots/CodeSnapshots.test.tsx`

**Interfaces:**
- Consumes: existing `changeIndex`, `index`, `snapshots` state already in `SnapshotViewer` (no new props).

- [ ] **Step 1: Write the failing test for visible, clickable chevrons**

```tsx
it("renders visible, clickable prev/next chevrons instead of screen-reader-only buttons", () => {
  // Render SnapshotViewer with 3 snapshots, index=1.
  // Assert the "Previous snapshot" and "Next snapshot" buttons do NOT have
  // the sr-only class, and clicking "Next snapshot" calls onIndexChange(2).
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/code-snapshots/CodeSnapshots.test.tsx`
Expected: FAIL — buttons currently have `className="sr-only"`.

- [ ] **Step 3: Make the chevrons visible and tappable**

Replace the two `sr-only` buttons (lines 828-841) with visible chevron buttons inside the existing `chromeVisible`-controlled header/overlay, matching the styling already used for the Back/Close buttons in the same header:
```tsx
<button
  type="button"
  aria-label="Previous snapshot"
  disabled={index === 0}
  onClick={() => changeIndex(index - 1)}
  className={cn(
    "absolute top-1/2 left-2 -translate-y-1/2 rounded-full bg-black/50 p-2 text-white transition-opacity disabled:opacity-30",
    chromeVisible ? "opacity-100" : "pointer-events-none opacity-0",
  )}
>
  <ChevronLeftIcon className="size-5" />
</button>
<button
  type="button"
  aria-label="Next snapshot"
  disabled={index === snapshots.length - 1}
  onClick={() => changeIndex(index + 1)}
  className={cn(
    "absolute top-1/2 right-2 -translate-y-1/2 rounded-full bg-black/50 p-2 text-white transition-opacity disabled:opacity-30",
    chromeVisible ? "opacity-100" : "pointer-events-none opacity-0",
  )}
>
  <ChevronRightIcon className="size-5" />
</button>
```
`ChevronLeftIcon`/`ChevronRightIcon` are already imported in this file (used elsewhere, e.g. the Back button). Add `ChevronRightIcon` to the import if it isn't already there.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/components/code-snapshots/CodeSnapshots.test.tsx`
Expected: PASS

- [ ] **Step 5: Add a real swipe-to-navigate gesture, distinct from pinch/pan-zoom**

In the `onPointerDown`/pointer-tracking logic (~line 622-690), track horizontal drag distance for single-pointer, non-zoomed gestures. On pointer up, if the total horizontal displacement exceeds a threshold (e.g. 60px) and the current zoom is at or near `SNAPSHOT_MIN_ZOOM` (i.e. the user isn't mid-pan of a zoomed image), call `changeIndex(index + (deltaX < 0 ? 1 : -1))` and animate the transition (reuse the existing `setAnimateTransform(true)` / `updateView` mechanism already used for the double-tap zoom reset, so the swipe gets the same snap animation rather than an instant jump). Guard this so it never fires when `state.pointers.size === 2` (an active pinch) or when the image is zoomed in past 1x (where horizontal drag should keep panning the image, not switch snapshots) — read the existing zoom/pan state fields (`view.zoom`, `SNAPSHOT_MIN_ZOOM`) already defined in this component to implement the threshold check precisely.

- [ ] **Step 6: Manual verification on a real mobile device or simulator**

Open the gallery with 3+ snapshots, verify: swiping left/right at 1x zoom smoothly navigates between snapshots with a snap animation; pinch-zooming and panning a zoomed image still works and does not trigger navigation; the visible chevrons work as a fallback tap target.

- [ ] **Step 7: Commit**

```bash
git add web/src/components/code-snapshots/CodeSnapshots.tsx web/src/components/code-snapshots/CodeSnapshots.test.tsx
git commit -m "fix(web): add real swipe-to-navigate gesture and visible chevrons to snapshot viewer"
```

---

## Task 9: End-to-end verification

- [ ] **Step 1: Full flow, real device**

On the native iOS shell (per the earlier CLAUDE.md guidance, `just run-ios`), start a practice interview session with Interview Mode on, send a prompt that returns a >20-line code block, and confirm: the text answer appears with no added delay; within a few seconds the code block becomes tappable; tapping it opens directly into a multi-page swipeable viewer (not the grid); swiping between pages is smooth with visible overlap context; the grid gallery (opened separately) shows both this session's manual screenshots (if any) and the new auto cards together.

- [ ] **Step 2: Confirm manual screenshot capture is unaffected**

Take a manual screenshot via the existing capture flow in the same session and confirm it still uploads and appears in the grid exactly as before.

- [ ] **Step 3: Push**

```bash
git push origin main
```
(Or open a PR per `CLAUDE.md`'s PR template if the repo's contribution flow for this branch requires review rather than a direct push — confirm which applies before pushing, since prior work in this session pushed directly to `main`.)
