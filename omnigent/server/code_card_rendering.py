"""Rasterize a code card page to a real PNG image via headless Playwright,
matching the dark, generously-spaced editor look of a manually captured
desktop code screenshot.
"""

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

from omnigent.server.auto_code_cards import CodeCardPage

if TYPE_CHECKING:
    from playwright.async_api import Browser

_logger = logging.getLogger(__name__)

# Playwright raises a plain playwright.async_api.Error (no dedicated subclass)
# when the Chromium binary hasn't been installed via `playwright install`.
# We match on message text and log it once per process instead of once per
# page, to keep auto code card rendering's silent best-effort degradation
# promise without spamming logs.
_MISSING_BROWSER_BINARY_MARKERS = ("Executable doesn't exist", "playwright install")

_missing_browser_binary_warned = False

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


def _render_html(page: CodeCardPage) -> tuple[str, int]:
    code = html.escape("\n".join(page.lines))
    language = html.escape(page.block.language or "")
    height = _VERTICAL_PADDING_PX * 2 + len(page.lines) * _LINE_HEIGHT_PX + 40
    return (
        _TEMPLATE.format(
            width=_CARD_WIDTH_PX,
            padding=_VERTICAL_PADDING_PX,
            line_height=_LINE_HEIGHT_PX,
            language=language,
            code=code,
        ),
        height,
    )


def is_missing_browser_binary_error(exc: BaseException) -> bool:
    """True if ``exc`` is Playwright's error for an uninstalled browser binary."""
    message = str(exc)
    return any(marker in message for marker in _MISSING_BROWSER_BINARY_MARKERS)


def warn_missing_browser_binary_once(exc: BaseException) -> None:
    """Log the missing-browser-binary condition once per process, as a warning
    (not a full traceback), so repeated messages don't spam the logs while a
    deploy is missing its Playwright browser install.
    """
    global _missing_browser_binary_warned
    if _missing_browser_binary_warned:
        return
    _missing_browser_binary_warned = True
    _logger.warning(
        "Playwright's Chromium binary is not installed; skipping auto code "
        "card rendering until `playwright install chromium` is run. %s",
        exc,
    )


async def render_code_card_png(page: CodeCardPage, browser: Browser) -> bytes:
    """Render one code card page to PNG bytes using an already-launched
    browser. The caller owns the browser's launch/close lifecycle so many
    pages from the same message can share a single Chromium process.
    """
    html_content, height = _render_html(page)
    browser_page = await browser.new_page(viewport={"width": _CARD_WIDTH_PX, "height": height})
    try:
        await browser_page.set_content(html_content)
        return await browser_page.screenshot(type="png", full_page=True)
    finally:
        await browser_page.close()
