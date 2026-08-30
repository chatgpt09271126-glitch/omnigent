import { getEmbedRoot } from "@/lib/host";
import { domToCanvas } from "modern-screenshot";

const MAX_CAPTURE_SCALE = 2;

export interface CaptureRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

const CLIPPING_OVERFLOWS = new Set(["auto", "scroll", "hidden", "clip"]);

function visibleElementRect(element: HTMLElement): CaptureRect | null {
  const rect = element.getBoundingClientRect();
  let left = Math.max(0, rect.left);
  let top = Math.max(0, rect.top);
  let right = Math.min(window.innerWidth, rect.right);
  let bottom = Math.min(window.innerHeight, rect.bottom);
  let ancestor = element.parentElement;
  while (ancestor) {
    const style = window.getComputedStyle(ancestor);
    const ancestorRect = ancestor.getBoundingClientRect();
    if (CLIPPING_OVERFLOWS.has(style.overflowX)) {
      left = Math.max(left, ancestorRect.left);
      right = Math.min(right, ancestorRect.right);
    }
    if (CLIPPING_OVERFLOWS.has(style.overflowY)) {
      top = Math.max(top, ancestorRect.top);
      bottom = Math.min(bottom, ancestorRect.bottom);
    }
    ancestor = ancestor.parentElement;
  }
  if (right - left < 2 || bottom - top < 2) return null;
  return { left, top, width: right - left, height: bottom - top };
}

function canvasBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("The browser could not encode the snapshot."));
    }, "image/png");
  });
}

function captureScale(): number {
  return Math.min(MAX_CAPTURE_SCALE, Math.max(1, window.devicePixelRatio || 1));
}

function visibleBackgroundColor(element: HTMLElement): string {
  let current: HTMLElement | null = element;
  while (current) {
    const color = window.getComputedStyle(current).backgroundColor;
    if (
      color &&
      color !== "transparent" &&
      color !== "rgba(0, 0, 0, 0)" &&
      !/\/\s*0\s*\)$/.test(color)
    ) {
      return color;
    }
    current = current.parentElement;
  }
  return window.getComputedStyle(document.documentElement).backgroundColor || "#09090b";
}

function snapshotFilter(node: Node): boolean {
  return !(node instanceof HTMLElement && node.dataset.codeSnapshotExclude === "true");
}

/** Rasterize the current Omnigent viewport and crop a client-coordinate region. */
export async function captureViewportRegion(rect: CaptureRect): Promise<Blob> {
  const captureRoot = getEmbedRoot() ?? document.body;
  const rootRect = captureRoot.getBoundingClientRect();
  const scale = captureScale();
  const canvas = await domToCanvas(captureRoot, {
    width: rootRect.width,
    height: rootRect.height,
    scale,
    features: { restoreScrollPosition: true },
    filter: snapshotFilter,
  });

  const relativeLeft = rect.left - rootRect.left;
  const relativeTop = rect.top - rootRect.top;
  const cropLeft = Math.max(0, relativeLeft);
  const cropTop = Math.max(0, relativeTop);
  const cropRight = Math.min(rootRect.width, relativeLeft + rect.width);
  const cropBottom = Math.min(rootRect.height, relativeTop + rect.height);
  const width = Math.max(1, Math.round((cropRight - cropLeft) * scale));
  const height = Math.max(1, Math.round((cropBottom - cropTop) * scale));
  const cropped = document.createElement("canvas");
  cropped.width = width;
  cropped.height = height;
  const context = cropped.getContext("2d");
  if (!context) throw new Error("Canvas rendering is unavailable.");
  context.drawImage(
    canvas,
    Math.round(cropLeft * scale),
    Math.round(cropTop * scale),
    width,
    height,
    0,
    0,
    width,
    height,
  );
  return canvasBlob(cropped);
}

/** Capture only the part of an element currently visible in the browser viewport. */
export async function captureVisibleElement(element: HTMLElement): Promise<Blob> {
  const elementRect = element.getBoundingClientRect();
  const visibleRect = visibleElementRect(element);
  if (!visibleRect) throw new Error("No code content is currently visible to capture.");

  // Rasterize only the code viewport. Capturing the entire app and cropping it
  // is both slow and unreliable for fixed overlays in mobile WebKit.
  const offsetLeft = visibleRect.left - elementRect.left;
  const offsetTop = visibleRect.top - elementRect.top;
  const canvas = await domToCanvas(element, {
    width: visibleRect.width,
    height: visibleRect.height,
    scale: captureScale(),
    backgroundColor: visibleBackgroundColor(element),
    features: { restoreScrollPosition: true },
    filter: snapshotFilter,
    style: {
      width: `${elementRect.width}px`,
      height: `${elementRect.height}px`,
      maxWidth: "none",
      maxHeight: "none",
      margin: "0",
      transform: `translate(${-offsetLeft}px, ${-offsetTop}px)`,
      transformOrigin: "top left",
    },
  });
  return canvasBlob(canvas);
}
