import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type * as NativeBridgeModule from "@/lib/nativeBridge";

const { isIOSShellMock, isAndroidShellMock, isMobileWebDeviceMock } = vi.hoisted(() => ({
  isIOSShellMock: vi.fn(() => false),
  isAndroidShellMock: vi.fn(() => false),
  isMobileWebDeviceMock: vi.fn(() => false),
}));

vi.mock("@/lib/host", () => ({
  getEmbedRoot: () => null,
}));
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: vi.fn(),
}));
vi.mock("@/lib/domCapture", () => ({
  captureViewportRegion: vi.fn(),
  captureVisibleElement: vi.fn(),
}));
vi.mock("@/lib/nativeBridge", async (importOriginal) => ({
  ...(await importOriginal<typeof NativeBridgeModule>()),
  isIOSShell: () => isIOSShellMock(),
  isAndroidShell: () => isAndroidShellMock(),
}));
vi.mock("@/lib/mobileDevice", () => ({
  isMobileWebDevice: () => isMobileWebDeviceMock(),
}));

import { MessageResponse } from "@/components/ai-elements/message";
import { BlockRenderer } from "@/components/blocks/BlockRenderer";
import type { CodeSnapshot } from "@/lib/codeSnapshotsApi";
import { captureViewportRegion, captureVisibleElement } from "@/lib/domCapture";
import { authenticatedFetch } from "@/lib/identity";
import { emitResponseSignalArrival, emitResponseSignalNavigation } from "@/lib/responseSignals";

const PNG = new Blob(["snapshot"], { type: "image/png" });
const ORIGIN = {
  conversationId: "conversation-1",
  responseId: "response-1",
  itemId: "a2ab4699da644cde8c4b768444aa243d",
};
const createObjectUrlDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const revokeObjectUrlDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

function snapshot(id: string, captureType: CodeSnapshot["capture_type"] = "region_capture") {
  return {
    id,
    conversation_id: ORIGIN.conversationId,
    response_id: ORIGIN.responseId,
    item_id: ORIGIN.itemId,
    code_block_start_offset: 0,
    language: "ts",
    created_by: "alice@example.com",
    created_at: 1,
    capture_type: captureType,
    content_type: "image/png",
    bytes: 8,
    content_url: `/v1/sessions/${ORIGIN.conversationId}/code-snapshots/${id}/content`,
  } satisfies CodeSnapshot;
}

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installSnapshotApi(initial: CodeSnapshot[]) {
  const stored = [...initial];
  const forms: FormData[] = [];
  vi.mocked(authenticatedFetch).mockImplementation(async (input, init) => {
    const url = input.toString();
    if (url.endsWith("/content")) {
      return new Response("snapshot", {
        status: 200,
        headers: { "Content-Type": "image/png" },
      });
    }
    if (init?.method === "POST") {
      const form = init.body as FormData;
      forms.push(form);
      const created = snapshot(`snapshot-${stored.length + 1}`, form.get("capture_type") as never);
      stored.push(created);
      return jsonResponse(created, 201);
    }
    if (init?.method === "DELETE") {
      const id = url.split("/").at(-1);
      const index = stored.findIndex((item) => item.id === id);
      if (index >= 0) stored.splice(index, 1);
      return jsonResponse({ id, deleted: true });
    }
    return jsonResponse({ object: "list", data: stored });
  });
  return { forms, stored };
}

function setMobile(matches: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query) =>
      ({
        matches,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }) as MediaQueryList,
  );
}

function TestQueryProvider({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function renderCodeBlock(canEdit = true, wrapper?: (children: ReactNode) => ReactNode) {
  const content = (
    <MessageResponse codeSnapshotContext={{ ...ORIGIN, canEdit }}>
      {"```ts\nconst answer = 42;\n```"}
    </MessageResponse>
  );
  return render(<TestQueryProvider>{wrapper ? wrapper(content) : content}</TestQueryProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  isIOSShellMock.mockReturnValue(false);
  isAndroidShellMock.mockReturnValue(false);
  isMobileWebDeviceMock.mockReturnValue(false);
  setMobile(false);
  vi.mocked(captureViewportRegion).mockResolvedValue(PNG);
  vi.mocked(captureVisibleElement).mockResolvedValue(PNG);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  Object.defineProperties(URL, {
    createObjectURL: { configurable: true, value: vi.fn(() => "blob:snapshot") },
    revokeObjectURL: { configurable: true, value: vi.fn() },
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  if (createObjectUrlDescriptor) {
    Object.defineProperty(URL, "createObjectURL", createObjectUrlDescriptor);
  } else {
    Reflect.deleteProperty(URL, "createObjectURL");
  }
  if (revokeObjectUrlDescriptor) {
    Object.defineProperty(URL, "revokeObjectURL", revokeObjectUrlDescriptor);
  } else {
    Reflect.deleteProperty(URL, "revokeObjectURL");
  }
});

describe("code snapshots", () => {
  it("waits for a stable persisted assistant item before enabling snapshots", async () => {
    installSnapshotApi([]);
    const codeItem = {
      kind: "text" as const,
      itemId: ORIGIN.itemId,
      text: "```ts\nconst answer = 42;\n```",
      final: true,
    };
    const { rerender } = render(
      <TestQueryProvider>
        <BlockRenderer
          items={[codeItem]}
          sessionStatus="idle"
          snapshotConversationId={ORIGIN.conversationId}
          snapshotResponseId={ORIGIN.responseId}
          canEditSnapshots
          snapshotsStable={false}
        />
      </TestQueryProvider>,
    );

    await waitFor(() =>
      expect(document.querySelector('[data-streamdown="code-block-body"]')).not.toBeNull(),
    );
    expect(screen.queryByRole("button", { name: "Capture snapshot region" })).toBeNull();
    expect(authenticatedFetch).not.toHaveBeenCalled();

    rerender(
      <TestQueryProvider>
        <BlockRenderer
          items={[codeItem]}
          sessionStatus="idle"
          snapshotConversationId={ORIGIN.conversationId}
          snapshotResponseId={ORIGIN.responseId}
          canEditSnapshots
          snapshotsStable
        />
      </TestQueryProvider>,
    );

    expect(await screen.findByRole("button", { name: "Capture snapshot region" })).toBeVisible();
  });

  it("captures a desktop region, cancels with Escape, and reveals a separate gallery count", async () => {
    const api = installSnapshotApi([]);
    renderCodeBlock();

    const camera = await screen.findByRole("button", { name: "Capture snapshot region" });
    expect(screen.queryByRole("button", { name: /Open \d+ code snapshots/ })).toBeNull();

    fireEvent.click(camera);
    expect(screen.getByTestId("code-snapshot-capture-overlay")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("code-snapshot-capture-overlay")).toBeNull();
    expect(captureViewportRegion).not.toHaveBeenCalled();

    fireEvent.click(camera);
    const overlay = screen.getByTestId("code-snapshot-capture-overlay");
    fireEvent.pointerDown(overlay, { button: 0, clientX: 10, clientY: 20, pointerId: 1 });
    fireEvent.pointerMove(overlay, { clientX: 110, clientY: 120, pointerId: 1 });
    fireEvent.pointerUp(overlay, { clientX: 110, clientY: 120, pointerId: 1 });

    await waitFor(() =>
      expect(captureViewportRegion).toHaveBeenCalledWith({
        left: 10,
        top: 20,
        width: 100,
        height: 100,
      }),
    );
    expect(api.forms[0]?.get("capture_type")).toBe("region_capture");
    expect(await screen.findByRole("button", { name: "Open 1 code snapshots" })).toHaveTextContent(
      "1",
    );
  });

  it("uses distinct full-message source offsets for separate Streamdown blocks", async () => {
    installSnapshotApi([]);
    const markdown =
      "First\n\n```ts\nconst first = 1;\n```\n\nSecond\n\n```ts\nconst second = 2;\n```";
    render(
      <TestQueryProvider>
        <MessageResponse codeSnapshotContext={{ ...ORIGIN, canEdit: true }}>
          {markdown}
        </MessageResponse>
      </TestQueryProvider>,
    );

    expect(await screen.findAllByRole("button", { name: "Capture snapshot region" })).toHaveLength(
      2,
    );
    await waitFor(() => {
      const offsets = vi
        .mocked(authenticatedFetch)
        .mock.calls.filter(([, init]) => !init?.method)
        .map(([input]) =>
          new URL(input.toString(), "http://omnigent.test").searchParams.get(
            "code_block_start_offset",
          ),
        );
      expect(new Set(offsets)).toEqual(
        new Set([String(markdown.indexOf("```")), String(markdown.lastIndexOf("```ts"))]),
      );
    });
  });

  it("uses one-tap visible-code capture on mobile in normal and Code Focus views", async () => {
    setMobile(true);
    const api = installSnapshotApi([]);
    renderCodeBlock();

    fireEvent.click(await screen.findByRole("button", { name: "Take quick code snapshot" }));
    await waitFor(() => expect(captureVisibleElement).toHaveBeenCalledTimes(1));
    const normalTarget = vi.mocked(captureVisibleElement).mock.calls[0]![0] as HTMLElement;
    expect(normalTarget.dataset.streamdown).toBe("code-block-body");
    expect(api.forms[0]?.get("capture_type")).toBe("mobile_quick_capture");

    fireEvent.click(screen.getByRole("button", { name: "Open code focus mode" }));
    const focus = screen.getByRole("dialog", { name: "Code focus mode" });
    fireEvent.click(within(focus).getByRole("button", { name: "Take quick code snapshot" }));
    await waitFor(() => expect(captureVisibleElement).toHaveBeenCalledTimes(2));
    expect(vi.mocked(captureVisibleElement).mock.calls[1]?.[0]).toBe(
      focus.querySelector('[data-streamdown="code-block-body"]'),
    );
    expect(api.forms[1]?.get("capture_type")).toBe("mobile_quick_capture");
  });

  it("immediately acknowledges a mobile capture and blocks duplicate taps until saved", async () => {
    setMobile(true);
    installSnapshotApi([]);
    let finishCapture: ((blob: Blob) => void) | undefined;
    vi.mocked(captureVisibleElement).mockImplementationOnce(
      () =>
        new Promise<Blob>((resolve) => {
          finishCapture = resolve;
        }),
    );
    renderCodeBlock();

    const camera = await screen.findByRole("button", { name: "Take quick code snapshot" });
    fireEvent.click(camera);

    expect(screen.getByTestId("quick-snapshot-status")).toHaveTextContent(
      "Capturing visible code…",
    );
    expect(camera).toBeDisabled();
    expect(camera).toHaveAttribute("aria-busy", "true");
    await waitFor(() => expect(captureVisibleElement).toHaveBeenCalledOnce());
    fireEvent.click(camera);
    expect(captureVisibleElement).toHaveBeenCalledOnce();

    act(() => finishCapture?.(PNG));
    expect(await screen.findByText("Snapshot saved")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Open 1 code snapshots" })).toBeVisible();
  });

  it("uses Quick Snapshot in a wide native mobile shell", async () => {
    isIOSShellMock.mockReturnValue(true);
    const api = installSnapshotApi([]);
    renderCodeBlock();

    fireEvent.click(await screen.findByRole("button", { name: "Take quick code snapshot" }));

    await waitFor(() => expect(captureVisibleElement).toHaveBeenCalledOnce());
    expect(captureViewportRegion).not.toHaveBeenCalled();
    expect(api.forms[0]?.get("capture_type")).toBe("mobile_quick_capture");
  });

  it("uses Quick Snapshot in a wide mobile web browser", async () => {
    isMobileWebDeviceMock.mockReturnValue(true);
    const api = installSnapshotApi([]);
    renderCodeBlock();

    fireEvent.click(await screen.findByRole("button", { name: "Take quick code snapshot" }));

    await waitFor(() => expect(captureVisibleElement).toHaveBeenCalledOnce());
    expect(captureViewportRegion).not.toHaveBeenCalled();
    expect(api.forms[0]?.get("capture_type")).toBe("mobile_quick_capture");
  });

  it("imports dropped and pasted images into only this block", async () => {
    const api = installSnapshotApi([]);
    renderCodeBlock();
    await screen.findByRole("button", { name: "Capture snapshot region" });
    const image = new File(["png"], "desktop.png", { type: "image/png" });
    const files = [image] as unknown as FileList;
    const dataTransfer = { files, types: ["Files"], dropEffect: "none" };
    const dropZone = screen.getByTestId("code-snapshot-drop-zone");

    fireEvent.dragEnter(dropZone, { dataTransfer });
    expect(screen.getByText("Drop to add snapshot")).toBeInTheDocument();
    fireEvent.drop(dropZone, { dataTransfer });
    await waitFor(() => expect(api.forms).toHaveLength(1));
    expect(api.forms[0]?.get("capture_type")).toBe("uploaded_image");

    fireEvent.click(await screen.findByRole("button", { name: "Open 1 code snapshots" }));
    const gallery = screen.getByRole("dialog", { name: "Code snapshot gallery" });
    const clipboardItem = { kind: "file", getAsFile: () => image };
    fireEvent.paste(gallery, { clipboardData: { items: [clipboardItem] } });
    await waitFor(() => expect(api.forms).toHaveLength(2));
    expect(api.forms[1]?.get("capture_type")).toBe("clipboard_image");
    expect(await screen.findByText("2 snapshots")).toBeInTheDocument();
  });

  it("keeps viewer navigation inside the block and deletes one snapshot", async () => {
    const api = installSnapshotApi([snapshot("first"), snapshot("second")]);
    renderCodeBlock();

    fireEvent.click(await screen.findByRole("button", { name: "Open 2 code snapshots" }));
    const gallery = screen.getByRole("dialog", { name: "Code snapshot gallery" });
    fireEvent.click(within(gallery).getByRole("button", { name: "Open snapshot 1 of 2" }));
    expect(within(gallery).getByText("1 of 2")).toBeInTheDocument();
    fireEvent.click(within(gallery).getByRole("button", { name: "Next snapshot" }));
    expect(within(gallery).getByText("2 of 2")).toBeInTheDocument();
    expect(within(gallery).getByRole("button", { name: "Next snapshot" })).toBeDisabled();
    fireEvent.click(within(gallery).getByRole("button", { name: "Back" }));
    fireEvent.click(within(gallery).getByRole("button", { name: "Delete snapshot 1" }));

    await waitFor(() => expect(api.stored).toHaveLength(1));
    expect(await within(gallery).findByText("1 snapshot")).toBeInTheDocument();
  });

  it("preserves conversation scroll when the gallery closes", async () => {
    installSnapshotApi([snapshot("first")]);
    const { getByTestId } = renderCodeBlock(true, (children) => (
      <div data-testid="conversation-scroll" style={{ overflowY: "auto" }}>
        {children}
      </div>
    ));
    const scroller = getByTestId("conversation-scroll");
    scroller.scrollTop = 219;
    fireEvent.click(await screen.findByRole("button", { name: "Open 1 code snapshots" }));
    fireEvent.click(screen.getByRole("button", { name: "Close snapshot gallery" }));

    expect(screen.queryByRole("dialog", { name: "Code snapshot gallery" })).toBeNull();
    expect(scroller.scrollTop).toBe(219);
  });

  it("keeps the gallery open on Attention arrival until navigation is requested", async () => {
    installSnapshotApi([snapshot("first")]);
    renderCodeBlock();
    fireEvent.click(await screen.findByRole("button", { name: "Open 1 code snapshots" }));
    const attention = {
      conversationId: ORIGIN.conversationId,
      responseId: ORIGIN.responseId,
      signalType: "attention" as const,
      active: true,
      source: "remote" as const,
    };

    act(() => emitResponseSignalArrival(attention));
    expect(screen.getByRole("dialog", { name: "Code snapshot gallery" })).toBeInTheDocument();

    act(() => emitResponseSignalNavigation(attention));
    expect(screen.queryByRole("dialog", { name: "Code snapshot gallery" })).toBeNull();
  });

  it("anchors pinch zoom under the fingers and clamps one-finger panning to image bounds", async () => {
    installSnapshotApi([snapshot("first")]);
    renderCodeBlock();
    fireEvent.click(await screen.findByRole("button", { name: "Open 1 code snapshots" }));
    const gallery = screen.getByRole("dialog", { name: "Code snapshot gallery" });
    fireEvent.click(within(gallery).getByRole("button", { name: "Open snapshot 1 of 1" }));

    const viewport = screen.getByTestId("snapshot-viewer-viewport");
    vi.spyOn(viewport, "getBoundingClientRect").mockReturnValue({
      bottom: 600,
      height: 600,
      left: 0,
      right: 300,
      top: 0,
      width: 300,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    const image = await screen.findByTestId("snapshot-viewer-image");
    Object.defineProperties(image, {
      naturalWidth: { configurable: true, value: 300 },
      naturalHeight: { configurable: true, value: 600 },
    });
    fireEvent.load(image);

    fireEvent.pointerDown(viewport, { pointerId: 1, clientX: 40, clientY: 300 });
    fireEvent.pointerDown(viewport, { pointerId: 2, clientX: 120, clientY: 300 });
    fireEvent.pointerMove(viewport, { pointerId: 2, clientX: 200, clientY: 300 });

    expect(viewport).toHaveAttribute("data-zoom", "2.000");
    expect(viewport).toHaveAttribute("data-offset-x", "110.0");

    fireEvent.pointerUp(viewport, { pointerId: 2, clientX: 200, clientY: 300 });
    fireEvent.pointerUp(viewport, { pointerId: 1, clientX: 40, clientY: 300 });
    fireEvent.pointerDown(viewport, { pointerId: 3, clientX: 100, clientY: 300 });
    fireEvent.pointerMove(viewport, { pointerId: 3, clientX: 1_000, clientY: 300 });

    // At 2x, a 300px-wide image may move only 150px from center. No empty
    // canvas can be dragged into view past the image edge.
    expect(viewport).toHaveAttribute("data-offset-x", "150.0");
  });

  it("double-tap zooms around the tapped code and a second double-tap returns to fit", async () => {
    installSnapshotApi([snapshot("first")]);
    renderCodeBlock();
    fireEvent.click(await screen.findByRole("button", { name: "Open 1 code snapshots" }));
    const gallery = screen.getByRole("dialog", { name: "Code snapshot gallery" });
    fireEvent.click(within(gallery).getByRole("button", { name: "Open snapshot 1 of 1" }));

    const viewport = screen.getByTestId("snapshot-viewer-viewport");
    vi.spyOn(viewport, "getBoundingClientRect").mockReturnValue({
      bottom: 600,
      height: 600,
      left: 0,
      right: 300,
      top: 0,
      width: 300,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    const image = await screen.findByTestId("snapshot-viewer-image");
    Object.defineProperties(image, {
      naturalWidth: { configurable: true, value: 300 },
      naturalHeight: { configurable: true, value: 600 },
    });
    fireEvent.load(image);

    const tap = (pointerId: number) => {
      fireEvent.pointerDown(viewport, { pointerId, clientX: 50, clientY: 300 });
      fireEvent.pointerUp(viewport, { pointerId, clientX: 50, clientY: 300 });
    };
    tap(1);
    tap(2);

    expect(viewport).toHaveAttribute("data-zoom", "2.500");
    expect(viewport).toHaveAttribute("data-offset-x", "150.0");

    tap(3);
    tap(4);
    expect(viewport).toHaveAttribute("data-zoom", "1.000");
    expect(viewport).toHaveAttribute("data-offset-x", "0.0");
  });

  it("renders visible, clickable prev/next chevrons instead of screen-reader-only buttons", async () => {
    installSnapshotApi([snapshot("first"), snapshot("second"), snapshot("third")]);
    renderCodeBlock();

    fireEvent.click(await screen.findByRole("button", { name: "Open 3 code snapshots" }));
    const gallery = screen.getByRole("dialog", { name: "Code snapshot gallery" });
    fireEvent.click(within(gallery).getByRole("button", { name: "Open snapshot 2 of 3" }));

    const previous = within(gallery).getByRole("button", { name: "Previous snapshot" });
    const next = within(gallery).getByRole("button", { name: "Next snapshot" });
    expect(previous.className).not.toMatch(/\bsr-only\b/);
    expect(next.className).not.toMatch(/\bsr-only\b/);

    fireEvent.click(next);
    expect(within(gallery).getByText("3 of 3")).toBeInTheDocument();
  });

  function setupSwipeViewport() {
    const viewport = screen.getByTestId("snapshot-viewer-viewport");
    vi.spyOn(viewport, "getBoundingClientRect").mockReturnValue({
      bottom: 600,
      height: 600,
      left: 0,
      right: 300,
      top: 0,
      width: 300,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    return viewport;
  }

  it("swiping left past the threshold at 1x zoom navigates to the next snapshot", async () => {
    installSnapshotApi([snapshot("first"), snapshot("second"), snapshot("third")]);
    renderCodeBlock();
    fireEvent.click(await screen.findByRole("button", { name: "Open 3 code snapshots" }));
    const gallery = screen.getByRole("dialog", { name: "Code snapshot gallery" });
    fireEvent.click(within(gallery).getByRole("button", { name: "Open snapshot 1 of 3" }));
    const viewport = setupSwipeViewport();

    fireEvent.pointerDown(viewport, { pointerId: 1, clientX: 250, clientY: 300 });
    fireEvent.pointerMove(viewport, { pointerId: 1, clientX: 100, clientY: 300 });
    fireEvent.pointerUp(viewport, { pointerId: 1, clientX: 100, clientY: 300 });

    expect(within(gallery).getByText("2 of 3")).toBeInTheDocument();
  });

  it("swiping right past the threshold at 1x zoom navigates to the previous snapshot", async () => {
    installSnapshotApi([snapshot("first"), snapshot("second"), snapshot("third")]);
    renderCodeBlock();
    fireEvent.click(await screen.findByRole("button", { name: "Open 3 code snapshots" }));
    const gallery = screen.getByRole("dialog", { name: "Code snapshot gallery" });
    fireEvent.click(within(gallery).getByRole("button", { name: "Open snapshot 2 of 3" }));
    const viewport = setupSwipeViewport();

    fireEvent.pointerDown(viewport, { pointerId: 1, clientX: 60, clientY: 300 });
    fireEvent.pointerMove(viewport, { pointerId: 1, clientX: 220, clientY: 300 });
    fireEvent.pointerUp(viewport, { pointerId: 1, clientX: 220, clientY: 300 });

    expect(within(gallery).getByText("1 of 3")).toBeInTheDocument();
  });

  it("a small drag below the swipe threshold does not change the index", async () => {
    installSnapshotApi([snapshot("first"), snapshot("second"), snapshot("third")]);
    renderCodeBlock();
    fireEvent.click(await screen.findByRole("button", { name: "Open 3 code snapshots" }));
    const gallery = screen.getByRole("dialog", { name: "Code snapshot gallery" });
    fireEvent.click(within(gallery).getByRole("button", { name: "Open snapshot 2 of 3" }));
    const viewport = setupSwipeViewport();

    fireEvent.pointerDown(viewport, { pointerId: 1, clientX: 150, clientY: 300 });
    fireEvent.pointerMove(viewport, { pointerId: 1, clientX: 130, clientY: 300 });
    fireEvent.pointerUp(viewport, { pointerId: 1, clientX: 130, clientY: 300 });

    expect(within(gallery).getByText("2 of 3")).toBeInTheDocument();
  });

  it("pinch-zooming (two pointers) does not trigger navigation", async () => {
    installSnapshotApi([snapshot("first"), snapshot("second"), snapshot("third")]);
    renderCodeBlock();
    fireEvent.click(await screen.findByRole("button", { name: "Open 3 code snapshots" }));
    const gallery = screen.getByRole("dialog", { name: "Code snapshot gallery" });
    fireEvent.click(within(gallery).getByRole("button", { name: "Open snapshot 2 of 3" }));
    const viewport = setupSwipeViewport();
    const image = await screen.findByTestId("snapshot-viewer-image");
    Object.defineProperties(image, {
      naturalWidth: { configurable: true, value: 300 },
      naturalHeight: { configurable: true, value: 600 },
    });
    fireEvent.load(image);

    fireEvent.pointerDown(viewport, { pointerId: 1, clientX: 40, clientY: 300 });
    fireEvent.pointerDown(viewport, { pointerId: 2, clientX: 260, clientY: 300 });
    fireEvent.pointerMove(viewport, { pointerId: 1, clientX: 10, clientY: 300 });
    fireEvent.pointerMove(viewport, { pointerId: 2, clientX: 290, clientY: 300 });
    fireEvent.pointerUp(viewport, { pointerId: 1, clientX: 10, clientY: 300 });
    fireEvent.pointerUp(viewport, { pointerId: 2, clientX: 290, clientY: 300 });

    expect(within(gallery).getByText("2 of 3")).toBeInTheDocument();
  });

  it("swiping while zoomed in pans the image instead of navigating", async () => {
    installSnapshotApi([snapshot("first"), snapshot("second"), snapshot("third")]);
    renderCodeBlock();
    fireEvent.click(await screen.findByRole("button", { name: "Open 3 code snapshots" }));
    const gallery = screen.getByRole("dialog", { name: "Code snapshot gallery" });
    fireEvent.click(within(gallery).getByRole("button", { name: "Open snapshot 2 of 3" }));
    const viewport = setupSwipeViewport();
    const image = await screen.findByTestId("snapshot-viewer-image");
    Object.defineProperties(image, {
      naturalWidth: { configurable: true, value: 300 },
      naturalHeight: { configurable: true, value: 600 },
    });
    fireEvent.load(image);

    // Pinch-zoom to 2x, then release both pointers.
    fireEvent.pointerDown(viewport, { pointerId: 1, clientX: 40, clientY: 300 });
    fireEvent.pointerDown(viewport, { pointerId: 2, clientX: 120, clientY: 300 });
    fireEvent.pointerMove(viewport, { pointerId: 2, clientX: 200, clientY: 300 });
    expect(viewport).toHaveAttribute("data-zoom", "2.000");
    fireEvent.pointerUp(viewport, { pointerId: 2, clientX: 200, clientY: 300 });
    fireEvent.pointerUp(viewport, { pointerId: 1, clientX: 40, clientY: 300 });

    // Now a single-pointer horizontal drag well past the swipe threshold.
    const offsetXBeforeDrag = viewport.getAttribute("data-offset-x");
    fireEvent.pointerDown(viewport, { pointerId: 3, clientX: 250, clientY: 300 });
    fireEvent.pointerMove(viewport, { pointerId: 3, clientX: 100, clientY: 300 });
    fireEvent.pointerUp(viewport, { pointerId: 3, clientX: 100, clientY: 300 });

    expect(within(gallery).getByText("2 of 3")).toBeInTheDocument();
    expect(viewport).toHaveAttribute("data-zoom", "2.000");
    // The pan actually moved the image, rather than the drag being a no-op.
    expect(viewport.getAttribute("data-offset-x")).not.toBe(offsetXBeforeDrag);
  });

  it("opens the viewer directly for a code block's own snapshots, without the grid", async () => {
    installSnapshotApi([snapshot("first"), snapshot("second")]);
    renderCodeBlock();

    await screen.findByRole("button", { name: "Open 2 code snapshots" });
    const codeBody = document.querySelector('[data-streamdown="code-block-body"]');
    expect(codeBody).not.toBeNull();
    fireEvent.pointerDown(codeBody!, { clientX: 50, clientY: 50 });
    fireEvent.pointerUp(codeBody!, { clientX: 50, clientY: 50 });

    expect(await screen.findByTestId("snapshot-viewer")).toBeVisible();
    expect(screen.queryByTestId("snapshot-gallery")).toBeNull();
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
  });

  it("shows a pending indicator until auto cards arrive, then becomes tappable", async () => {
    const { stored } = installSnapshotApi([]);
    renderCodeBlock();

    await screen.findByTestId("auto-card-pending-indicator");
    const codeBody = document.querySelector('[data-streamdown="code-block-body"]');
    expect(codeBody).not.toBeNull();

    fireEvent.pointerDown(codeBody!, { clientX: 50, clientY: 50 });
    fireEvent.pointerUp(codeBody!, { clientX: 50, clientY: 50 });
    expect(screen.queryByTestId("snapshot-viewer")).toBeNull();

    stored.push(snapshot("first"));

    await waitFor(() => expect(screen.queryByTestId("auto-card-pending-indicator")).toBeNull(), {
      timeout: 5000,
    });

    fireEvent.pointerDown(codeBody!, { clientX: 50, clientY: 50 });
    fireEvent.pointerUp(codeBody!, { clientX: 50, clientY: 50 });
    expect(await screen.findByTestId("snapshot-viewer")).toBeVisible();
  }, 10000);

  it("stops polling after repeated fetch errors and stays non-tappable with no error surfaced", async () => {
    vi.mocked(authenticatedFetch).mockImplementation(async () => {
      throw new Error("network error");
    });
    renderCodeBlock();

    await screen.findByTestId("auto-card-pending-indicator");

    // Wait for the poll to give up (bounded attempts, ~1.5s apart) and the
    // pending indicator to disappear on its own, with no error UI shown.
    await waitFor(() => expect(screen.queryByTestId("auto-card-pending-indicator")).toBeNull(), {
      timeout: 10000,
    });

    expect(screen.queryByText(/error/i)).toBeNull();

    const codeBody = document.querySelector('[data-streamdown="code-block-body"]');
    expect(codeBody).not.toBeNull();
    fireEvent.pointerDown(codeBody!, { clientX: 50, clientY: 50 });
    fireEvent.pointerUp(codeBody!, { clientX: 50, clientY: 50 });
    expect(screen.queryByTestId("snapshot-viewer")).toBeNull();

    // No further fetch attempts should occur once the budget is exhausted.
    const callCountAfterStop = vi.mocked(authenticatedFetch).mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 2000));
    expect(vi.mocked(authenticatedFetch).mock.calls.length).toBe(callCountAfterStop);
  }, 15000);

  it("does not open the viewer on a click-and-drag gesture used to select text", async () => {
    installSnapshotApi([snapshot("first"), snapshot("second")]);
    renderCodeBlock();

    await screen.findByRole("button", { name: "Open 2 code snapshots" });
    const codeBody = document.querySelector('[data-streamdown="code-block-body"]');
    expect(codeBody).not.toBeNull();
    fireEvent.pointerDown(codeBody!, { clientX: 10, clientY: 10 });
    fireEvent.pointerUp(codeBody!, { clientX: 90, clientY: 40 });

    expect(screen.queryByTestId("snapshot-viewer")).toBeNull();
  });

  it("does not open the viewer when the pointer up leaves an active text selection", async () => {
    installSnapshotApi([snapshot("first"), snapshot("second")]);
    renderCodeBlock();

    await screen.findByRole("button", { name: "Open 2 code snapshots" });
    const codeBody = document.querySelector('[data-streamdown="code-block-body"]');
    expect(codeBody).not.toBeNull();

    const getSelectionSpy = vi
      .spyOn(window, "getSelection")
      .mockReturnValue({ toString: () => "selected text" } as Selection);

    fireEvent.pointerDown(codeBody!, { clientX: 50, clientY: 50 });
    fireEvent.pointerUp(codeBody!, { clientX: 50, clientY: 50 });

    expect(screen.queryByTestId("snapshot-viewer")).toBeNull();
    getSelectionSpy.mockRestore();
  });

  it("keeps the toolbar's gallery button opening the grid, not the scoped viewer", async () => {
    installSnapshotApi([snapshot("first")]);
    renderCodeBlock();

    fireEvent.click(await screen.findByRole("button", { name: "Open 1 code snapshots" }));

    expect(screen.getByRole("dialog", { name: "Code snapshot gallery" })).toBeInTheDocument();
    expect(screen.queryByTestId("auto-card-viewer")).toBeNull();
  });

  it("allows read-only viewing without capture, import, or deletion controls", async () => {
    installSnapshotApi([snapshot("first")]);
    renderCodeBlock(false);

    expect(
      await screen.findByRole("button", { name: "Open 1 code snapshots" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /snapshot region|quick code snapshot/i }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open 1 code snapshots" }));
    expect(screen.queryByRole("button", { name: "Delete snapshot 1" })).toBeNull();
  });
});
