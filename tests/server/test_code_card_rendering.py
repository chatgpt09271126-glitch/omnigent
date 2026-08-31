import logging

import pytest
from playwright.async_api import async_playwright

from omnigent.server import code_card_rendering
from omnigent.server.auto_code_cards import CodeCardPage, DetectedCodeBlock
from omnigent.server.code_card_rendering import (
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
    page = CodeCardPage(block=block, page_index=0, total_pages=1, lines=block.lines)

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
    page = CodeCardPage(block=block, page_index=0, total_pages=1, lines=block.lines)

    png_bytes = await render_code_card_png(page, browser)

    assert png_bytes.startswith(_PNG_MAGIC)
    assert len(png_bytes) > 100


@pytest.mark.asyncio
async def test_render_code_card_png_multiple_pages_share_one_browser(browser):
    """A single already-launched browser can render more than one page,
    each getting its own page.new_page()/close() rather than a fresh
    browser process."""
    block = DetectedCodeBlock(language="python", start_offset=0, lines=["a = 1", "b = 2"])
    page1 = CodeCardPage(block=block, page_index=0, total_pages=2, lines=["a = 1"])
    page2 = CodeCardPage(block=block, page_index=1, total_pages=2, lines=["b = 2"])

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
