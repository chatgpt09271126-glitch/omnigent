import logging

import pytest
from playwright.async_api import async_playwright

from omnigent.server import code_card_rendering
from omnigent.server.auto_code_cards import CodeCardPage, DetectedCodeBlock
from omnigent.server.code_card_rendering import (
    _render_html,
    is_missing_browser_binary_error,
    render_code_card_png,
    warn_missing_browser_binary_once,
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
async def browser():
    async with async_playwright() as pw:
        launched = await pw.chromium.launch()
        try:
            yield launched
        finally:
            await launched.close()


@pytest.mark.asyncio
async def test_render_code_card_png_produces_valid_png(browser):
    block = DetectedCodeBlock(
        language="python", start_offset=0, lines=["def f():", "    return 1"]
    )
    page = CodeCardPage(block=block, page_index=0, total_pages=1, lines=block.lines, start_line=0)

    png_bytes = await render_code_card_png(page, browser)

    assert png_bytes.startswith(_PNG_MAGIC)
    assert len(png_bytes) > 100  # sanity: not an empty/blank image


@pytest.mark.asyncio
async def test_render_code_card_png_escapes_html_special_characters(browser):
    block = DetectedCodeBlock(
        language="html",
        start_offset=0,
        lines=['<div class="a">', "  if a < b && b > c:", "</div>"],
    )
    page = CodeCardPage(block=block, page_index=0, total_pages=1, lines=block.lines, start_line=0)

    png_bytes = await render_code_card_png(page, browser)

    assert png_bytes.startswith(_PNG_MAGIC)
    assert len(png_bytes) > 100


@pytest.mark.asyncio
async def test_render_code_card_png_multiple_pages_share_one_browser(browser):
    """A single already-launched browser can render more than one page,
    each getting its own page.new_page()/close() rather than a fresh
    browser process."""
    block = DetectedCodeBlock(language="python", start_offset=0, lines=["a = 1", "b = 2"])
    page1 = CodeCardPage(block=block, page_index=0, total_pages=2, lines=["a = 1"], start_line=0)
    page2 = CodeCardPage(block=block, page_index=1, total_pages=2, lines=["b = 2"], start_line=1)

    png1 = await render_code_card_png(page1, browser)
    png2 = await render_code_card_png(page2, browser)

    assert png1.startswith(_PNG_MAGIC)
    assert png2.startswith(_PNG_MAGIC)


def test_is_missing_browser_binary_error_matches_playwright_message():
    exc = RuntimeError(
        "BrowserType.launch: Executable doesn't exist at "
        "/root/.cache/ms-playwright/chromium/headless_shell\n"
        "Looks like Playwright was just installed or updated.\n"
        "Please run the following command to download new browsers:\n"
        "    playwright install\n"
    )
    assert is_missing_browser_binary_error(exc)


def test_is_missing_browser_binary_error_rejects_unrelated_error():
    assert not is_missing_browser_binary_error(RuntimeError("some other failure"))


def test_warn_missing_browser_binary_once_logs_a_single_warning_not_a_traceback(
    monkeypatch, caplog
):
    monkeypatch.setattr(code_card_rendering, "_missing_browser_binary_warned", False)
    exc = RuntimeError("Executable doesn't exist at /fake/path\nplaywright install")

    with caplog.at_level(logging.WARNING):
        warn_missing_browser_binary_once(exc)
        warn_missing_browser_binary_once(exc)
        warn_missing_browser_binary_once(exc)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].exc_info is None  # not a full exception traceback


def test_render_html_applies_syntax_highlighting_for_known_language():
    lines = ["def f(x):", "    # comment", '    return "hi" + str(x)']
    block = DetectedCodeBlock(language="python", start_offset=0, lines=lines)
    page = CodeCardPage(block=block, page_index=0, total_pages=1, lines=lines, start_line=0)

    highlighted_html, _ = _render_html(page)

    # Distinct token classes for a keyword, a comment, and a string literal.
    assert 'class="k"' in highlighted_html  # keyword: def/return
    assert 'class="c1"' in highlighted_html  # comment
    assert 'class="s2"' in highlighted_html  # string literal


def test_render_html_differs_for_known_vs_unknown_language():
    lines = ["def f(x):", "    return x"]
    known_block = DetectedCodeBlock(language="python", start_offset=0, lines=lines)
    unknown_block = DetectedCodeBlock(language=None, start_offset=0, lines=lines)
    known_page = CodeCardPage(
        block=known_block, page_index=0, total_pages=1, lines=lines, start_line=0
    )
    unknown_page = CodeCardPage(
        block=unknown_block, page_index=0, total_pages=1, lines=lines, start_line=0
    )

    known_html, _ = _render_html(known_page)
    unknown_html, _ = _render_html(unknown_page)

    assert known_html != unknown_html
    assert 'class="k"' in known_html  # python keyword highlighted


def test_render_html_gutter_shows_correct_starting_line_number():
    lines = ["a = 1", "b = 2", "c = 3"]
    block = DetectedCodeBlock(language="python", start_offset=0, lines=lines)
    # Simulates a non-first page of a multi-page block: absolute lines 18-20.
    page = CodeCardPage(block=block, page_index=1, total_pages=2, lines=lines, start_line=17)

    highlighted_html, _ = _render_html(page)

    assert '<span class="gutter">18</span>' in highlighted_html
    assert '<span class="gutter">19</span>' in highlighted_html
    assert '<span class="gutter">20</span>' in highlighted_html
    assert '<span class="gutter">1</span>' not in highlighted_html


def test_render_html_unrecognized_language_falls_back_gracefully():
    lines = ["some ~~~ garbage {{{ text", "more text here"]
    block = DetectedCodeBlock(language="not-a-real-language-xyz", start_offset=0, lines=lines)
    page = CodeCardPage(block=block, page_index=0, total_pages=1, lines=lines, start_line=0)

    # Should not raise, and should still produce a rendered card.
    highlighted_html, height = _render_html(page)

    assert "<html>" in highlighted_html
    assert height > 0


@pytest.mark.asyncio
async def test_render_code_card_png_unrecognized_language_produces_valid_png(browser):
    block = DetectedCodeBlock(
        language="totally-bogus-lang", start_offset=0, lines=["print('hi')"]
    )
    page = CodeCardPage(block=block, page_index=0, total_pages=1, lines=block.lines, start_line=0)

    png_bytes = await render_code_card_png(page, browser)

    assert png_bytes.startswith(_PNG_MAGIC)
    assert len(png_bytes) > 100
