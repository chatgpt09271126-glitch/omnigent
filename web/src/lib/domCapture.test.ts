import { beforeEach, describe, expect, it, vi } from "vitest";

const domToCanvas = vi.hoisted(() => vi.fn());

vi.mock("modern-screenshot", () => ({ domToCanvas }));
vi.mock("@/lib/host", () => ({ getEmbedRoot: () => null }));

import { captureVisibleElement } from "./domCapture";

describe("captureVisibleElement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperties(window, {
      innerWidth: { configurable: true, value: 390 },
      innerHeight: { configurable: true, value: 844 },
      devicePixelRatio: { configurable: true, value: 3 },
    });
    domToCanvas.mockResolvedValue({
      toBlob: (callback: BlobCallback) => callback(new Blob(["png"], { type: "image/png" })),
    } as HTMLCanvasElement);
  });

  it("rasterizes the visible code element directly instead of the full app", async () => {
    const parent = document.createElement("div");
    parent.style.backgroundColor = "rgb(9, 9, 11)";
    const target = document.createElement("div");
    parent.append(target);
    document.body.append(parent);
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue({
      x: -20,
      y: 100,
      left: -20,
      top: 100,
      right: 300,
      bottom: 900,
      width: 320,
      height: 800,
      toJSON: () => ({}),
    });

    const blob = await captureVisibleElement(target);

    expect(blob.type).toBe("image/png");
    expect(domToCanvas).toHaveBeenCalledOnce();
    const [captured, options] = domToCanvas.mock.calls[0] as [HTMLElement, Record<string, unknown>];
    expect(captured).toBe(target);
    expect(options).toMatchObject({
      width: 300,
      height: 744,
      scale: 2,
      backgroundColor: "rgb(9, 9, 11)",
      features: { restoreScrollPosition: true },
      style: {
        width: "320px",
        height: "800px",
        transform: "translate(-20px, 0px)",
        transformOrigin: "top left",
      },
    });
    parent.remove();
  });

  it("rejects a code viewport that is not currently visible", async () => {
    const target = document.createElement("div");
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 900,
      left: 0,
      top: 900,
      right: 300,
      bottom: 1_000,
      width: 300,
      height: 100,
      toJSON: () => ({}),
    });

    await expect(captureVisibleElement(target)).rejects.toThrow(
      "No code content is currently visible to capture.",
    );
    expect(domToCanvas).not.toHaveBeenCalled();
  });

  it("clips Code Focus content to its scrolled viewport", async () => {
    const viewport = document.createElement("div");
    viewport.style.overflowX = "auto";
    viewport.style.overflowY = "auto";
    viewport.style.backgroundColor = "rgb(9, 9, 11)";
    const content = document.createElement("div");
    viewport.append(content);
    document.body.append(viewport);
    vi.spyOn(viewport, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 80,
      left: 0,
      top: 80,
      right: 390,
      bottom: 844,
      width: 390,
      height: 764,
      toJSON: () => ({}),
    });
    vi.spyOn(content, "getBoundingClientRect").mockReturnValue({
      x: -40,
      y: -120,
      left: -40,
      top: -120,
      right: 450,
      bottom: 1_080,
      width: 490,
      height: 1_200,
      toJSON: () => ({}),
    });

    await captureVisibleElement(content);

    expect(domToCanvas).toHaveBeenCalledWith(
      content,
      expect.objectContaining({
        width: 390,
        height: 764,
        style: expect.objectContaining({
          transform: "translate(-40px, -200px)",
        }),
      }),
    );
    viewport.remove();
  });
});
