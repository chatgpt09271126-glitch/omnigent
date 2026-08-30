"""E2E: a rendered code block captures and persists its own snapshot."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.conftest import seed_committed_turn

_REPLY = (
    "Here is the result:\n\n```ts\nconst answer = 42;\n```\n\n"
    "And a separate block:\n\n```ts\nconst second = 7;\n```\n"
)


def assert_snapshot_has_visible_content(image) -> None:
    """Verify the decoded artifact contains contrasting rendered pixels."""
    metrics = image.evaluate(
        """async element => {
          await element.decode();
          const canvas = document.createElement('canvas');
          canvas.width = element.naturalWidth;
          canvas.height = element.naturalHeight;
          const context = canvas.getContext('2d', {willReadFrequently: true});
          context.drawImage(element, 0, 0);
          const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
          let minimum = 255;
          let maximum = 0;
          let visible = 0;
          for (let index = 0; index < pixels.length; index += 16) {
            if (pixels[index + 3] < 16) continue;
            visible += 1;
            const luminance =
              pixels[index] * 0.2126 + pixels[index + 1] * 0.7152 + pixels[index + 2] * 0.0722;
            minimum = Math.min(minimum, luminance);
            maximum = Math.max(maximum, luminance);
          }
          return {
            width: canvas.width,
            height: canvas.height,
            visible,
            contrast: maximum - minimum,
          };
        }"""
    )
    assert metrics["width"] > 100
    assert metrics["height"] > 40
    assert metrics["visible"] > 100
    assert metrics["contrast"] > 20


@pytest.fixture
def code_snapshot_session(seeded_session: tuple[str, str]) -> Iterator[tuple[str, str]]:
    """Seed a finalized assistant code block with stable response/item ids."""
    base_url, session_id = seeded_session
    seed_committed_turn(
        session_id,
        prompt="Show a small TypeScript result.",
        reply=_REPLY,
        response_id="resp_code_snapshot_e2e",
    )
    yield base_url, session_id


def test_region_capture_opens_block_gallery_and_deletes(
    page: Page,
    code_snapshot_session: tuple[str, str],
) -> None:
    """Capture a browser region, view its stored image, then delete it."""
    base_url, session_id = code_snapshot_session
    page.goto(f"{base_url}/c/{session_id}")

    code_block = page.get_by_test_id("code-snapshot-drop-zone").first
    code_body = code_block.locator('[data-streamdown="code-block-body"]')
    expect(code_body).to_be_visible(timeout=30_000)
    box = code_body.evaluate(
        """element => {
          const rect = element.getBoundingClientRect();
          return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
        }"""
    )
    assert isinstance(box, dict)

    code_block.get_by_role("button", name="Capture snapshot region").click()
    overlay = page.get_by_test_id("code-snapshot-capture-overlay")
    expect(overlay).to_be_visible()
    page.mouse.move(box["x"] + 8, box["y"] + 8)
    page.mouse.down()
    page.mouse.move(
        box["x"] + min(box["width"] - 8, 360),
        box["y"] + min(box["height"] - 8, 160),
        steps=8,
    )
    page.mouse.up()

    gallery_button = page.get_by_role("button", name="Open 1 code snapshots")
    expect(gallery_button).to_be_visible(timeout=30_000)
    gallery_button.click()
    gallery = page.get_by_role("dialog", name="Code snapshot gallery")
    expect(gallery).to_be_visible()
    expect(gallery.get_by_role("img", name="Code snapshot")).to_be_visible(timeout=15_000)

    gallery.get_by_role("button", name="Delete snapshot 1").click()
    expect(gallery.get_by_text("0 snapshots")).to_be_visible()
    gallery.get_by_role("button", name="Close snapshot gallery").click()
    expect(page.get_by_role("button", name="Open 1 code snapshots")).to_have_count(0)


def test_mobile_code_focus_quick_snapshot_has_visible_pixels(
    page: Page,
    code_snapshot_session: tuple[str, str],
) -> None:
    """Code Focus Quick Snapshot captures its framed code, not a black image."""
    base_url, session_id = code_snapshot_session
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/c/{session_id}")

    code_block = page.get_by_test_id("code-snapshot-drop-zone").first
    expect(code_block).to_be_visible(timeout=30_000)
    code_block.get_by_role("button", name="Open code focus mode").click()
    focus = page.get_by_role("dialog", name="Code focus mode")
    expect(focus).to_be_visible()

    focus.get_by_role("button", name="Take quick code snapshot").click()
    status = page.get_by_test_id("quick-snapshot-status")
    expect(status).to_be_visible()
    expect(status).to_contain_text(
        re.compile(r"Capturing visible code|Saving snapshot|Snapshot saved", re.IGNORECASE)
    )
    gallery_button = focus.get_by_role("button", name="Open 1 code snapshots")
    expect(gallery_button).to_be_visible(timeout=30_000)
    gallery_button.click()
    gallery = page.get_by_role("dialog", name="Code snapshot gallery")
    image = gallery.get_by_role("img", name="Code snapshot")
    expect(image).to_be_visible(timeout=15_000)
    assert_snapshot_has_visible_content(image)


def test_mobile_quick_snapshot_captures_visible_code(
    page: Page,
    code_snapshot_session: tuple[str, str],
) -> None:
    """At phone width Camera saves the visible code region in one tap."""
    base_url, session_id = code_snapshot_session
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/c/{session_id}")

    code_block = page.get_by_test_id("code-snapshot-drop-zone").first
    expect(code_block.locator('[data-streamdown="code-block-body"]')).to_be_visible(timeout=30_000)
    code_block.get_by_role("button", name="Take quick code snapshot").click()
    gallery_button = page.get_by_role("button", name="Open 1 code snapshots")
    expect(gallery_button).to_be_visible(timeout=30_000)
    gallery_button.click()
    gallery = page.get_by_role("dialog", name="Code snapshot gallery")
    expect(gallery.get_by_role("img", name="Code snapshot")).to_be_visible(timeout=15_000)
    assert_snapshot_has_visible_content(gallery.get_by_role("img", name="Code snapshot"))


def test_mobile_snapshot_viewer_anchors_zoom_and_clamps_pan(
    page: Page,
    code_snapshot_session: tuple[str, str],
) -> None:
    """Fullscreen snapshot gestures preserve the focal point without exposing empty edges."""
    base_url, session_id = code_snapshot_session
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/c/{session_id}")

    code_block = page.get_by_test_id("code-snapshot-drop-zone").first
    expect(code_block.locator('[data-streamdown="code-block-body"]')).to_be_visible(timeout=30_000)
    code_block.get_by_role("button", name="Take quick code snapshot").click()
    gallery_button = page.get_by_role("button", name="Open 1 code snapshots")
    expect(gallery_button).to_be_visible(timeout=30_000)
    gallery_button.click()
    gallery = page.get_by_role("dialog", name="Code snapshot gallery")
    gallery.get_by_role("button", name="Open snapshot 1 of 1").click()

    viewport = page.get_by_test_id("snapshot-viewer-viewport")
    image = page.get_by_test_id("snapshot-viewer-image")
    expect(image).to_be_visible(timeout=15_000)
    image_box = image.bounding_box()
    assert image_box is not None
    pinch_y = image_box["y"] + image_box["height"] / 2
    pinch_left = image_box["x"] + image_box["width"] * 0.1
    pinch_start_right = image_box["x"] + image_box["width"] * 0.3
    pinch_end_right = image_box["x"] + image_box["width"] * 0.7

    viewport.evaluate(
        """(element, points) => {
          const dispatch = (type, pointerId, point, isPrimary) => {
            const event = new PointerEvent(type, {
              bubbles: true,
              cancelable: true,
              clientX: point.x,
              clientY: point.y,
              pointerId,
              pointerType: 'touch',
              isPrimary,
            });
            element.dispatchEvent(event);
          };
          dispatch('pointerdown', 31, points.left, true);
          dispatch('pointerdown', 32, points.startRight, false);
          dispatch('pointermove', 32, points.endRight, false);
          dispatch('pointerup', 32, points.endRight, false);
          dispatch('pointerup', 31, points.left, true);
        }""",
        {
            "left": {"x": pinch_left, "y": pinch_y},
            "startRight": {"x": pinch_start_right, "y": pinch_y},
            "endRight": {"x": pinch_end_right, "y": pinch_y},
        },
    )

    zoom = float(viewport.get_attribute("data-zoom") or "1")
    assert zoom > 2
    offset_x = float(viewport.get_attribute("data-offset-x") or "0")
    assert abs(offset_x) > 1

    viewport.evaluate(
        """element => {
          const dispatch = (type, x) => element.dispatchEvent(new PointerEvent(type, {
            bubbles: true,
            cancelable: true,
            clientX: x,
            clientY: innerHeight / 2,
            pointerId: 40,
            pointerType: 'touch',
            isPrimary: true,
          }));
          dispatch('pointerdown', innerWidth / 2);
          dispatch('pointermove', innerWidth * 8);
          dispatch('pointerup', innerWidth * 8);
        }"""
    )

    transformed = image.evaluate(
        """element => ({
          image: element.getBoundingClientRect(),
          viewport: element.parentElement.getBoundingClientRect(),
        })"""
    )
    assert transformed["image"]["left"] <= transformed["viewport"]["left"] + 1
    assert transformed["image"]["right"] >= transformed["viewport"]["right"] - 1


def test_code_focus_region_capture_receives_pointer_events(
    page: Page,
    code_snapshot_session: tuple[str, str],
) -> None:
    """The modal's outside-pointer lock must not disable the capture overlay."""
    base_url, session_id = code_snapshot_session
    page.goto(f"{base_url}/c/{session_id}")

    code_block = page.get_by_test_id("code-snapshot-drop-zone").first
    expect(code_block).to_be_visible(timeout=30_000)
    code_block.get_by_role("button", name="Open code focus mode").click()
    focus = page.get_by_role("dialog", name="Code focus mode")
    expect(focus).to_be_visible()

    focus.get_by_role("button", name="Capture snapshot region").click()
    overlay = page.get_by_test_id("code-snapshot-capture-overlay")
    expect(overlay).to_be_visible()
    assert overlay.evaluate("element => getComputedStyle(element).pointerEvents") == "auto"

    page.mouse.move(120, 160)
    page.mouse.down()
    page.mouse.move(520, 380, steps=8)
    expect(page.get_by_test_id("code-snapshot-selection")).to_be_visible()
    page.mouse.up()

    gallery_button = focus.get_by_role("button", name="Open 1 code snapshots")
    expect(gallery_button).to_be_visible(timeout=30_000)
    gallery_button.click()
    gallery = page.get_by_role("dialog", name="Code snapshot gallery")
    expect(gallery.get_by_role("img", name="Code snapshot")).to_be_visible(timeout=15_000)
    gallery.get_by_role("button", name="Close snapshot gallery").click()
    expect(focus).to_be_visible()

    code_blocks = page.get_by_test_id("code-snapshot-drop-zone")
    expect(code_blocks).to_have_count(2)
    expect(code_blocks.nth(1).get_by_role("button", name="Open 1 code snapshots")).to_have_count(0)
