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


@pytest.mark.asyncio
async def test_render_code_card_png_escapes_html_special_characters():
    block = DetectedCodeBlock(
        language="html",
        start_offset=0,
        lines=["<div class=\"a\">", "  if a < b && b > c:", "</div>"],
    )
    page = CodeCardPage(block=block, page_index=0, total_pages=1, lines=block.lines)

    png_bytes = await render_code_card_png(page)

    assert png_bytes.startswith(_PNG_MAGIC)
    assert len(png_bytes) > 100
