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
