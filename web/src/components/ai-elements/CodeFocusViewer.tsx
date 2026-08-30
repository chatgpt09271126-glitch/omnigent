"use client";

import { Button } from "@/components/ui/button";
import { CodeSnapshotToolbarControls } from "@/components/code-snapshots/CodeSnapshots";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { getEmbedRoot } from "@/lib/host";
import { cn } from "@/lib/utils";
import { onResponseSignalNavigation } from "@/lib/responseSignals";
import {
  Maximize2Icon,
  MinusIcon,
  PlusIcon,
  RotateCcwIcon,
  WrapTextIcon,
  XIcon,
} from "lucide-react";
import * as DialogPrimitive from "radix-ui/dialog";
import type { CSSProperties, ReactNode } from "react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

const MIN_ZOOM = 0.6;
const MAX_ZOOM = 2.5;
const ZOOM_STEP = 0.1;

interface CodeFocusViewerProps {
  renderedCode: ReactNode;
  initialWrap: boolean;
  snapshotsEnabled?: boolean;
}

interface ScrollPosition {
  element: HTMLElement;
  left: number;
  top: number;
}

interface ScrollLock {
  release: () => void;
  restore: () => void;
}

interface ZoomAnchor {
  clientX: number;
  clientY: number;
  contentXRatio: number;
  contentYRatio: number;
  textNode: Text | null;
  textOffset: number;
  textLeft: number;
  textTop: number;
}

interface PinchState {
  distance: number;
  zoom: number;
}

type CaretDocument = Document & {
  caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
  caretRangeFromPoint?: (x: number, y: number) => Range | null;
};

function clampZoom(zoom: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

function distanceBetweenTouches(touches: TouchList): number {
  const x = touches[0].clientX - touches[1].clientX;
  const y = touches[0].clientY - touches[1].clientY;
  return Math.hypot(x, y);
}

function midpointBetweenTouches(touches: TouchList): { x: number; y: number } {
  return {
    x: (touches[0].clientX + touches[1].clientX) / 2,
    y: (touches[0].clientY + touches[1].clientY) / 2,
  };
}

function isScrollable(element: HTMLElement): boolean {
  const style = window.getComputedStyle(element);
  const canScrollY = /^(auto|scroll|overlay)$/.test(style.overflowY);
  const canScrollX = /^(auto|scroll|overlay)$/.test(style.overflowX);
  return (
    (canScrollY && element.scrollHeight > element.clientHeight) ||
    (canScrollX && element.scrollWidth > element.clientWidth)
  );
}

function lockScrollAncestors(trigger: HTMLElement): ScrollLock {
  const positions: ScrollPosition[] = [];
  const seen = new Set<HTMLElement>();
  let ancestor: HTMLElement | null = trigger.parentElement;

  while (ancestor) {
    if (isScrollable(ancestor)) {
      seen.add(ancestor);
      positions.push({ element: ancestor, left: ancestor.scrollLeft, top: ancestor.scrollTop });
    }
    ancestor = ancestor.parentElement;
  }

  const scrollingElement = document.scrollingElement;
  if (scrollingElement instanceof HTMLElement && !seen.has(scrollingElement)) {
    positions.push({
      element: scrollingElement,
      left: scrollingElement.scrollLeft,
      top: scrollingElement.scrollTop,
    });
  }

  const windowLeft = window.scrollX;
  const windowTop = window.scrollY;
  const pinElement = (position: ScrollPosition) => {
    if (position.element.scrollLeft !== position.left) position.element.scrollLeft = position.left;
    if (position.element.scrollTop !== position.top) position.element.scrollTop = position.top;
  };
  const listeners = positions.map((position) => {
    const pin = () => pinElement(position);
    position.element.addEventListener("scroll", pin);
    return { element: position.element, pin };
  });
  const pinWindow = () => {
    if (window.scrollX !== windowLeft || window.scrollY !== windowTop) {
      window.scrollTo(windowLeft, windowTop);
    }
  };
  window.addEventListener("scroll", pinWindow, { passive: true });

  return {
    release: () => {
      for (const { element, pin } of listeners) element.removeEventListener("scroll", pin);
      window.removeEventListener("scroll", pinWindow);
    },
    restore: () => {
      for (const position of positions) pinElement(position);
      pinWindow();
    },
  };
}

function caretAtPoint(x: number, y: number): { textNode: Text; offset: number } | null {
  const caretDocument = document as CaretDocument;
  const position = caretDocument.caretPositionFromPoint?.(x, y);
  if (position?.offsetNode instanceof Text) {
    return { textNode: position.offsetNode, offset: position.offset };
  }

  const range = caretDocument.caretRangeFromPoint?.(x, y);
  if (range?.startContainer instanceof Text) {
    return { textNode: range.startContainer, offset: range.startOffset };
  }

  return null;
}

function textPosition(textNode: Text, offset: number): DOMRect | null {
  if (!textNode.isConnected) return null;
  const range = document.createRange();
  range.setStart(textNode, Math.min(offset, textNode.length));
  range.collapse(true);
  const rect = range.getClientRects()[0] ?? range.getBoundingClientRect();
  return rect.width > 0 || rect.height > 0 ? rect : null;
}

function captureZoomAnchor(viewport: HTMLElement, clientX: number, clientY: number): ZoomAnchor {
  const viewportRect = viewport.getBoundingClientRect();
  const localX = Math.min(viewport.clientWidth, Math.max(0, clientX - viewportRect.left));
  const localY = Math.min(viewport.clientHeight, Math.max(0, clientY - viewportRect.top));
  const caret = caretAtPoint(clientX, clientY);
  const textNode = caret?.textNode ?? null;
  const caretRect = textNode ? textPosition(textNode, caret?.offset ?? 0) : null;

  return {
    clientX,
    clientY,
    contentXRatio: (viewport.scrollLeft + localX) / Math.max(1, viewport.scrollWidth),
    contentYRatio: (viewport.scrollTop + localY) / Math.max(1, viewport.scrollHeight),
    textNode: textNode && viewport.contains(textNode) ? textNode : null,
    textOffset: caret?.offset ?? 0,
    textLeft: caretRect?.left ?? clientX,
    textTop: caretRect?.top ?? clientY,
  };
}

function restoreZoomAnchor(viewport: HTMLElement, anchor: ZoomAnchor): void {
  if (anchor.textNode && viewport.contains(anchor.textNode)) {
    const nextPosition = textPosition(anchor.textNode, anchor.textOffset);
    if (nextPosition) {
      // Counter the caret's layout displacement so the same code character
      // stays under the pinch point while its real font size changes.
      viewport.scrollLeft += nextPosition.left - anchor.textLeft;
      viewport.scrollTop += nextPosition.top - anchor.textTop;
      return;
    }
  }

  const viewportRect = viewport.getBoundingClientRect();
  const localX = Math.min(viewport.clientWidth, Math.max(0, anchor.clientX - viewportRect.left));
  const localY = Math.min(viewport.clientHeight, Math.max(0, anchor.clientY - viewportRect.top));
  viewport.scrollLeft = anchor.contentXRatio * viewport.scrollWidth - localX;
  viewport.scrollTop = anchor.contentYRatio * viewport.scrollHeight - localY;
}

export function CodeFocusViewer({
  renderedCode,
  initialWrap,
  snapshotsEnabled = false,
}: CodeFocusViewerProps) {
  const [open, setOpen] = useState(false);
  const [wrap, setWrap] = useState(initialWrap);
  const [zoom, setZoom] = useState(1);
  const [viewportElement, setViewportElement] = useState<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const wrapButtonRef = useRef<HTMLButtonElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const scrollLockRef = useRef<ScrollLock | null>(null);
  const pendingAnchorRef = useRef<ZoomAnchor | null>(null);
  const pinchRef = useRef<PinchState | null>(null);
  const zoomRef = useRef(zoom);
  const restoreFramesRef = useRef<number[]>([]);

  const setViewportNode = useCallback((element: HTMLDivElement | null) => {
    viewportRef.current = element;
    setViewportElement(element);
  }, []);

  const cancelRestoreFrames = useCallback(() => {
    for (const frame of restoreFramesRef.current) window.cancelAnimationFrame(frame);
    restoreFramesRef.current = [];
  }, []);

  const restoreAfterClose = useCallback(() => {
    cancelRestoreFrames();
    const scrollLock = scrollLockRef.current;
    scrollLockRef.current = null;
    scrollLock?.release();

    const restore = () => {
      scrollLock?.restore();
      triggerRef.current?.focus({ preventScroll: true });
      scrollLock?.restore();
    };
    restore();
    const firstFrame = window.requestAnimationFrame(() => {
      restore();
      const secondFrame = window.requestAnimationFrame(restore);
      restoreFramesRef.current = [secondFrame];
    });
    restoreFramesRef.current = [firstFrame];
  }, [cancelRestoreFrames]);

  useEffect(
    () => () => {
      cancelRestoreFrames();
      const scrollLock = scrollLockRef.current;
      scrollLockRef.current = null;
      scrollLock?.release();
      scrollLock?.restore();
    },
    [cancelRestoreFrames],
  );

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (nextOpen) {
        cancelRestoreFrames();
        if (triggerRef.current) scrollLockRef.current = lockScrollAncestors(triggerRef.current);
        pendingAnchorRef.current = null;
        pinchRef.current = null;
        zoomRef.current = 1;
        setZoom(1);
        setWrap(initialWrap);
      }
      setOpen(nextOpen);
    },
    [cancelRestoreFrames, initialWrap],
  );

  const applyZoom = useCallback((nextZoom: number, clientX?: number, clientY?: number) => {
    const viewport = viewportRef.current;
    const clamped = clampZoom(nextZoom);
    if (!viewport || Math.abs(clamped - zoomRef.current) < 0.001) return;

    const rect = viewport.getBoundingClientRect();
    pendingAnchorRef.current = captureZoomAnchor(
      viewport,
      clientX ?? rect.left + rect.width / 2,
      clientY ?? rect.top + rect.height / 2,
    );
    zoomRef.current = clamped;
    setZoom(clamped);
  }, []);

  const toggleWrap = useCallback(() => {
    const viewport = viewportRef.current;
    if (viewport) {
      const rect = viewport.getBoundingClientRect();
      pendingAnchorRef.current = captureZoomAnchor(
        viewport,
        rect.left + rect.width / 2,
        rect.top + rect.height / 2,
      );
    }
    setWrap((current) => !current);
  }, []);

  useLayoutEffect(() => {
    zoomRef.current = zoom;
    const viewport = viewportRef.current;
    const anchor = pendingAnchorRef.current;
    pendingAnchorRef.current = null;
    if (viewport && anchor) restoreZoomAnchor(viewport, anchor);
  }, [wrap, zoom]);

  useEffect(() => {
    if (!open || !viewportElement) return;
    const viewport = viewportElement;

    const onTouchStart = (event: TouchEvent) => {
      if (event.touches.length !== 2) return;
      pinchRef.current = {
        distance: distanceBetweenTouches(event.touches),
        zoom: zoomRef.current,
      };
    };
    const onTouchMove = (event: TouchEvent) => {
      const pinch = pinchRef.current;
      if (!pinch || event.touches.length !== 2 || pinch.distance === 0) return;
      event.preventDefault();
      const midpoint = midpointBetweenTouches(event.touches);
      applyZoom(
        pinch.zoom * (distanceBetweenTouches(event.touches) / pinch.distance),
        midpoint.x,
        midpoint.y,
      );
    };
    const onTouchEnd = (event: TouchEvent) => {
      pinchRef.current =
        event.touches.length === 2
          ? { distance: distanceBetweenTouches(event.touches), zoom: zoomRef.current }
          : null;
    };
    const preventSafariGesture = (event: Event) => event.preventDefault();

    viewport.addEventListener("touchstart", onTouchStart, { passive: true });
    viewport.addEventListener("touchmove", onTouchMove, { passive: false });
    viewport.addEventListener("touchend", onTouchEnd, { passive: true });
    viewport.addEventListener("touchcancel", onTouchEnd, { passive: true });
    viewport.addEventListener("gesturestart", preventSafariGesture, { passive: false });
    viewport.addEventListener("gesturechange", preventSafariGesture, { passive: false });
    return () => {
      viewport.removeEventListener("touchstart", onTouchStart);
      viewport.removeEventListener("touchmove", onTouchMove);
      viewport.removeEventListener("touchend", onTouchEnd);
      viewport.removeEventListener("touchcancel", onTouchEnd);
      viewport.removeEventListener("gesturestart", preventSafariGesture);
      viewport.removeEventListener("gesturechange", preventSafariGesture);
    };
  }, [applyZoom, open, viewportElement]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
      if (event.key === "+" || event.key === "=" || event.key === "Add") {
        event.preventDefault();
        applyZoom(zoomRef.current + ZOOM_STEP);
      } else if (event.key === "-" || event.key === "_" || event.key === "Subtract") {
        event.preventDefault();
        applyZoom(zoomRef.current - ZOOM_STEP);
      } else if (event.key === "0") {
        event.preventDefault();
        applyZoom(1);
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [applyZoom, open]);

  useEffect(() => onResponseSignalNavigation(() => setOpen(false)), []);

  const displayedZoom = Math.round((zoom * 100) / 5) * 5;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={handleOpenChange}>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <DialogPrimitive.Trigger asChild>
              <Button
                ref={triggerRef}
                aria-label="Open code focus mode"
                className="size-8 bg-sidebar/80 text-muted-foreground hover:text-foreground supports-[backdrop-filter]:bg-sidebar/70 supports-[backdrop-filter]:backdrop-blur"
                size="icon-sm"
                type="button"
                variant="ghost"
              >
                <Maximize2Icon size={14} />
              </Button>
            </DialogPrimitive.Trigger>
          </TooltipTrigger>
          <TooltipContent>Open code focus mode</TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <DialogPrimitive.Portal container={getEmbedRoot() ?? undefined}>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[90] bg-background" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className="code-focus-viewer fixed inset-0 z-[100] flex flex-col overflow-hidden bg-background text-foreground outline-none"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            restoreAfterClose();
          }}
          onInteractOutside={(event) => event.preventDefault()}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            wrapButtonRef.current?.focus({ preventScroll: true });
          }}
        >
          <DialogPrimitive.Title className="sr-only">Code focus mode</DialogPrimitive.Title>
          <header className="code-focus-toolbar flex shrink-0 border-b border-border bg-background/95 supports-[backdrop-filter]:backdrop-blur">
            <div className="code-focus-toolbar-scroll min-w-0 flex-1 overflow-x-auto overflow-y-hidden">
              <div className="flex h-11 w-max items-center gap-0.5 px-1">
                <Button
                  ref={wrapButtonRef}
                  aria-label={wrap ? "Disable word wrap" : "Enable word wrap"}
                  aria-pressed={wrap}
                  className={cn(
                    "h-10 px-2 transition-[opacity,background-color,color]",
                    wrap
                      ? "bg-foreground/10 text-foreground hover:bg-foreground/15"
                      : "text-muted-foreground opacity-50 hover:opacity-80",
                  )}
                  data-state={wrap ? "on" : "off"}
                  onClick={toggleWrap}
                  size="sm"
                  type="button"
                  variant="ghost"
                >
                  <WrapTextIcon className="size-4" />
                  <span>Wrap</span>
                </Button>
                <Button
                  aria-label="Zoom out"
                  className="size-10"
                  disabled={zoom <= MIN_ZOOM}
                  onClick={() => applyZoom(zoomRef.current - ZOOM_STEP)}
                  size="icon"
                  type="button"
                  variant="ghost"
                >
                  <MinusIcon className="size-4" />
                </Button>
                <output
                  aria-label={`Code zoom ${displayedZoom}%`}
                  className="min-w-14 text-center text-ui tabular-nums text-muted-foreground"
                >
                  {displayedZoom}%
                </output>
                <Button
                  aria-label="Zoom in"
                  className="size-10"
                  disabled={zoom >= MAX_ZOOM}
                  onClick={() => applyZoom(zoomRef.current + ZOOM_STEP)}
                  size="icon"
                  type="button"
                  variant="ghost"
                >
                  <PlusIcon className="size-4" />
                </Button>
                <Button
                  aria-label="Reset zoom"
                  className="h-10 px-2"
                  disabled={Math.abs(zoom - 1) < 0.001}
                  onClick={() => applyZoom(1)}
                  size="sm"
                  type="button"
                  variant="ghost"
                >
                  <RotateCcwIcon className="size-4" />
                  <span>Reset</span>
                </Button>
              </div>
            </div>
            {snapshotsEnabled && (
              <div className="flex shrink-0 items-center gap-0.5">
                <CodeSnapshotToolbarControls
                  className="size-10"
                  getQuickCaptureTarget={() =>
                    viewportRef.current?.querySelector<HTMLElement>(
                      '[data-streamdown="code-block-body"]',
                    ) ?? null
                  }
                />
              </div>
            )}
            <DialogPrimitive.Close asChild>
              <Button
                aria-label="Close code focus mode"
                className="m-0.5 size-10"
                size="icon"
                type="button"
                variant="ghost"
              >
                <XIcon className="size-4" />
              </Button>
            </DialogPrimitive.Close>
          </header>
          <div
            ref={setViewportNode}
            aria-label="Code content"
            className="code-focus-viewport min-h-0 flex-1 overflow-auto"
            onDragStart={(event) => event.preventDefault()}
          >
            <div
              className={cn("code-focus-content", wrap && "code-focus-wrap")}
              style={{ "--code-focus-zoom": zoom } as CSSProperties}
            >
              {renderedCode}
            </div>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
