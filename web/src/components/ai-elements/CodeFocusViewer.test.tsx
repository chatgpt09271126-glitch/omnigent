import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/host", () => ({
  getEmbedRoot: () => null,
}));

import { CodeFocusViewer } from "./CodeFocusViewer";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const renderedCode = (
  <div data-streamdown="code-block">
    <div data-streamdown="code-block-header">typescript</div>
    <div data-streamdown="code-block-actions">Download</div>
    <div data-streamdown="code-block-body">
      <pre>
        <code>
          <span>const answer = 42;</span>
        </code>
      </pre>
    </div>
  </div>
);

function renderViewer(initialWrap = true) {
  return render(<CodeFocusViewer initialWrap={initialWrap} renderedCode={renderedCode} />);
}

describe("CodeFocusViewer", () => {
  it("opens an accessible full-viewport reader with the requested compact controls", () => {
    renderViewer();
    const trigger = screen.getByRole("button", { name: "Open code focus mode" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "Code focus mode" });
    expect(dialog).toHaveClass("code-focus-viewer", "fixed", "inset-0");
    expect(screen.getByRole("button", { name: "Disable word wrap" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeEnabled();
    expect(screen.getByLabelText("Code zoom 100%")).toHaveTextContent("100%");
    expect(screen.getByRole("button", { name: "Zoom in" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reset zoom" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Close code focus mode" })).toBeInTheDocument();
    expect(screen.getByLabelText("Code content")).toHaveClass("code-focus-viewport");
    expect(dialog).toHaveTextContent("const answer = 42;");
  });

  it("zooms the code font in 10% steps and resets without transforming the content", () => {
    renderViewer();
    fireEvent.click(screen.getByRole("button", { name: "Open code focus mode" }));

    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    const content = document.querySelector<HTMLElement>(".code-focus-content")!;
    expect(screen.getByLabelText("Code zoom 110%")).toHaveTextContent("110%");
    expect(content.style.getPropertyValue("--code-focus-zoom")).toBe("1.1");
    expect(content.style.transform).toBe("");
    expect(screen.getByRole("button", { name: "Reset zoom" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Reset zoom" }));
    expect(screen.getByLabelText("Code zoom 100%")).toBeInTheDocument();
    expect(content.style.getPropertyValue("--code-focus-zoom")).toBe("1");
    expect(screen.getByRole("button", { name: "Reset zoom" })).toBeDisabled();
  });

  it("compensates scrolling for the code position under the zoom midpoint", () => {
    renderViewer();
    fireEvent.click(screen.getByRole("button", { name: "Open code focus mode" }));
    const viewport = screen.getByLabelText("Code content");
    const textNode = viewport.querySelector("code span")?.firstChild;
    expect(textNode).toBeInstanceOf(Text);
    viewport.scrollLeft = 40;
    viewport.scrollTop = 80;

    const caretDescriptor = Object.getOwnPropertyDescriptor(document, "caretRangeFromPoint");
    Object.defineProperty(document, "caretRangeFromPoint", {
      configurable: true,
      value: () => ({ startContainer: textNode, startOffset: 3 }),
    });
    let rectRead = 0;
    const rangeSpy = vi.spyOn(document, "createRange").mockImplementation(
      () =>
        ({
          collapse: () => {},
          getBoundingClientRect: () => ({ height: 20, left: 0, top: 0, width: 0 }),
          getClientRects: () => {
            const beforeZoom = rectRead++ === 0;
            return [
              {
                height: 20,
                left: beforeZoom ? 100 : 124,
                top: beforeZoom ? 200 : 232,
                width: 0,
              },
            ];
          },
          setStart: () => {},
        }) as unknown as Range,
    );

    try {
      fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
      expect(viewport.scrollLeft).toBe(64);
      expect(viewport.scrollTop).toBe(112);
    } finally {
      rangeSpy.mockRestore();
      if (caretDescriptor) {
        Object.defineProperty(document, "caretRangeFromPoint", caretDescriptor);
      } else {
        Reflect.deleteProperty(document, "caretRangeFromPoint");
      }
    }
  });

  it("handles two-touch pinch zoom while leaving one-touch moves native", () => {
    renderViewer();
    fireEvent.click(screen.getByRole("button", { name: "Open code focus mode" }));
    const viewport = screen.getByLabelText("Code content");

    const oneTouchMove = new Event("touchmove", { bubbles: true, cancelable: true });
    Object.defineProperty(oneTouchMove, "touches", {
      value: [{ clientX: 20, clientY: 20 }],
    });
    Object.defineProperty(oneTouchMove, "changedTouches", {
      value: [{ clientX: 20, clientY: 20 }],
    });
    fireEvent(viewport, oneTouchMove);
    expect(screen.getByLabelText("Code zoom 100%")).toBeInTheDocument();

    fireEvent.touchStart(viewport, {
      changedTouches: [{ clientX: 100, clientY: 50 }],
      touches: [
        { clientX: 0, clientY: 50 },
        { clientX: 100, clientY: 50 },
      ],
    });
    fireEvent.touchMove(viewport, {
      changedTouches: [{ clientX: 150, clientY: 50 }],
      touches: [
        { clientX: 0, clientY: 50 },
        { clientX: 150, clientY: 50 },
      ],
    });

    expect(screen.getByLabelText("Code zoom 150%")).toBeInTheDocument();
  });

  it("intercepts desktop browser-zoom shortcuts only while open", () => {
    renderViewer();
    fireEvent.click(screen.getByRole("button", { name: "Open code focus mode" }));

    const zoomIn = new KeyboardEvent("keydown", {
      bubbles: true,
      cancelable: true,
      ctrlKey: true,
      key: "+",
    });
    fireEvent(window, zoomIn);
    expect(zoomIn.defaultPrevented).toBe(true);
    expect(screen.getByLabelText("Code zoom 110%")).toBeInTheDocument();

    const reset = new KeyboardEvent("keydown", {
      bubbles: true,
      cancelable: true,
      ctrlKey: true,
      key: "0",
    });
    fireEvent(window, reset);
    expect(reset.defaultPrevented).toBe(true);
    expect(screen.getByLabelText("Code zoom 100%")).toBeInTheDocument();
  });

  it("pins and restores the conversation scroller, then returns focus on close", async () => {
    const { getByTestId } = render(
      <div data-testid="conversation-scroll" style={{ overflowY: "auto" }}>
        <CodeFocusViewer initialWrap renderedCode={renderedCode} />
      </div>,
    );
    const scroller = getByTestId("conversation-scroll");
    Object.defineProperties(scroller, {
      clientHeight: { configurable: true, value: 200 },
      scrollHeight: { configurable: true, value: 1000 },
    });
    scroller.scrollTop = 137;
    const trigger = screen.getByRole("button", { name: "Open code focus mode" });

    fireEvent.click(trigger);
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);
    expect(scroller.scrollTop).toBe(137);

    fireEvent.click(screen.getByRole("button", { name: "Close code focus mode" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(scroller.scrollTop).toBe(137);
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("closes with Escape and keeps an initially unwrapped block unwrapped", async () => {
    renderViewer(false);
    const trigger = screen.getByRole("button", { name: "Open code focus mode" });
    fireEvent.click(trigger);
    expect(screen.getByRole("button", { name: "Enable word wrap" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
