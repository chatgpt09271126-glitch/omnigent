"""Rasterize a code card page to a real PNG image via headless Playwright,
matching the dark, generously-spaced editor look of a manually captured
desktop code screenshot.
"""

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

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

_CARD_WIDTH_PX = 1000
_LINE_HEIGHT_PX = 28
_VERTICAL_PADDING_PX = 48

# Matches the in-app chat's own code-block theme (Shiki's "github-dark",
# see web/src/components/ai-elements/lazyCodePlugin.ts) so a code card looks
# like the same surface the candidate already reads code in, not a
# different-looking screenshot.
_PYGMENTS_STYLE = "github-dark"

_formatter = HtmlFormatter(style=_PYGMENTS_STYLE, noclasses=False, cssclass="highlight")

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
  }}
  .lang {{ color: #7d8590; font-size: 14px; margin-bottom: 12px; }}
  .code-row {{ display: flex; white-space: pre; }}
  .gutter {{
    flex: 0 0 auto;
    width: {gutter_width}ch;
    text-align: right;
    padding-right: 16px;
    color: #7d8590;
    user-select: none;
  }}
  {pygments_css}
  .highlight {{ background: #0d1117; }}
  .highlight pre {{ margin: 0; white-space: pre; }}
</style>
</head>
<body>
  <div class="card">
    <div class="lang">{language}</div>
    <div class="code-body">
      {rows}
    </div>
  </div>
</body>
</html>
"""


def _get_lexer(language: str | None, code: str):
    """Resolve a Pygments lexer for ``language``, falling back gracefully
    when it's missing or unrecognized instead of letting rendering crash.
    """
    if language:
        try:
            return get_lexer_by_name(language, stripnl=False)
        except ClassNotFound:
            pass
    try:
        return guess_lexer(code)
    except ClassNotFound:
        return TextLexer(stripnl=False)


def _highlight_lines(page: CodeCardPage) -> list[str]:
    """Return one highlighted HTML fragment per source line, in order."""
    code = "\n".join(page.lines)
    lexer = _get_lexer(page.block.language, code)
    highlighted = highlight(code, lexer, _formatter)
    # HtmlFormatter wraps the whole block in <div class="highlight"><pre>...</pre></div>;
    # split back into per-line fragments so each can sit beside its gutter number.
    start = highlighted.index("<pre>") + len("<pre>")
    end = highlighted.rindex("</pre>")
    body = highlighted[start:end]
    if body.endswith("\n"):
        body = body[:-1]
    return body.split("\n")


def _render_html(page: CodeCardPage) -> tuple[str, int]:
    language = html.escape(page.block.language or "")
    line_numbers = [page.start_line + 1 + i for i in range(len(page.lines))]
    gutter_width = max(len(str(n)) for n in line_numbers) if line_numbers else 1

    code_lines = _highlight_lines(page)
    rows = "\n".join(
        f'<div class="code-row"><span class="gutter">{num}</span>'
        f'<span class="highlight">{line}</span></div>'
        for num, line in zip(line_numbers, code_lines)
    )

    height = _VERTICAL_PADDING_PX * 2 + len(page.lines) * _LINE_HEIGHT_PX + 40
    return (
        _TEMPLATE.format(
            width=_CARD_WIDTH_PX,
            padding=_VERTICAL_PADDING_PX,
            line_height=_LINE_HEIGHT_PX,
            gutter_width=gutter_width,
            pygments_css=_formatter.get_style_defs(".highlight"),
            language=language,
            rows=rows,
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
