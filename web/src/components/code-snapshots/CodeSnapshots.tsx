"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CameraIcon,
  CheckIcon,
  ChevronLeftIcon,
  ImagesIcon,
  Loader2Icon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import * as DialogPrimitive from "radix-ui/dialog";
import type {
  CSSProperties,
  PointerEvent as ReactPointerEvent,
  ReactNode,
  SyntheticEvent,
} from "react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/button";
import { showToast } from "@/components/ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import {
  codeSnapshotsQueryKey,
  createCodeSnapshot,
  deleteCodeSnapshot,
  fetchCodeSnapshots,
  type CodeSnapshot,
  type CodeSnapshotOrigin,
  type SnapshotCaptureType,
} from "@/lib/codeSnapshotsApi";
import { captureViewportRegion, captureVisibleElement } from "@/lib/domCapture";
import { getEmbedRoot } from "@/lib/host";
import { authenticatedFetch } from "@/lib/identity";
import { isAndroidShell, isIOSShell } from "@/lib/nativeBridge";
import { isMobileWebDevice } from "@/lib/mobileDevice";
import { cn } from "@/lib/utils";
import { onResponseSignalNavigation } from "@/lib/responseSignals";

const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const SUPPORTED_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

interface CodeSnapshotContextValue {
  origin: CodeSnapshotOrigin;
  snapshots: CodeSnapshot[];
  isLoading: boolean;
  isSaving: boolean;
  save: (file: File | Blob, captureType: SnapshotCaptureType) => Promise<void>;
  remove: (snapshotId: string) => Promise<void>;
}

const CodeSnapshotContext = createContext<CodeSnapshotContextValue | null>(null);

export function CodeSnapshotBlockProvider({
  origin,
  children,
}: {
  origin: CodeSnapshotOrigin;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const queryKey = codeSnapshotsQueryKey(origin);
  const query = useQuery({
    queryKey,
    queryFn: () => fetchCodeSnapshots(origin),
    staleTime: 10_000,
  });
  const createMutation = useMutation({
    mutationFn: ({ file, captureType }: { file: File | Blob; captureType: SnapshotCaptureType }) =>
      createCodeSnapshot(origin, file, captureType),
    onSuccess: (snapshot) => {
      queryClient.setQueryData<CodeSnapshot[]>(queryKey, (current = []) => [...current, snapshot]);
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (snapshotId: string) => deleteCodeSnapshot(origin.conversationId, snapshotId),
    onSuccess: (_, snapshotId) => {
      queryClient.setQueryData<CodeSnapshot[]>(queryKey, (current = []) =>
        current.filter((snapshot) => snapshot.id !== snapshotId),
      );
    },
  });

  const save = useCallback(
    async (file: File | Blob, captureType: SnapshotCaptureType) => {
      try {
        await createMutation.mutateAsync({ file, captureType });
      } catch (error) {
        showToast(`Snapshot failed: ${error instanceof Error ? error.message : String(error)}`);
        throw error;
      }
    },
    [createMutation],
  );
  const remove = useCallback(
    async (snapshotId: string) => {
      try {
        await deleteMutation.mutateAsync(snapshotId);
      } catch (error) {
        showToast(`Delete failed: ${error instanceof Error ? error.message : String(error)}`);
        throw error;
      }
    },
    [deleteMutation],
  );

  const value = useMemo<CodeSnapshotContextValue>(
    () => ({
      origin,
      snapshots: query.data ?? [],
      isLoading: query.isLoading,
      isSaving: createMutation.isPending,
      save,
      remove,
    }),
    [createMutation.isPending, origin, query.data, query.isLoading, remove, save],
  );

  return <CodeSnapshotContext.Provider value={value}>{children}</CodeSnapshotContext.Provider>;
}

function useCodeSnapshotBlock(): CodeSnapshotContextValue {
  const context = useContext(CodeSnapshotContext);
  if (!context) throw new Error("Code snapshot controls require CodeSnapshotBlockProvider");
  return context;
}

function validateImage(file: File): string | null {
  if (!SUPPORTED_IMAGE_TYPES.has(file.type)) {
    return `${file.name || "Image"} is not a supported PNG, JPEG, GIF, or WebP image.`;
  }
  if (file.size > MAX_IMAGE_BYTES) {
    return `${file.name || "Image"} exceeds the 5 MB snapshot limit.`;
  }
  return null;
}

function imageFiles(files: FileList | File[]): File[] {
  return Array.from(files).filter((file) => SUPPORTED_IMAGE_TYPES.has(file.type));
}

async function importImages(
  files: File[],
  captureType: "uploaded_image" | "clipboard_image",
  save: CodeSnapshotContextValue["save"],
): Promise<void> {
  const validFiles: File[] = [];
  for (const file of files) {
    const error = validateImage(file);
    if (error) {
      showToast(error);
      continue;
    }
    validFiles.push(file);
  }
  await Promise.all(validFiles.map((file) => save(file, captureType)));
}

export function CodeSnapshotDropZone({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const { origin, save } = useCodeSnapshotBlock();
  const isMobile = useIsMobileViewport() || isIOSShell() || isAndroidShell() || isMobileWebDevice();
  const [dragDepth, setDragDepth] = useState(0);
  const enabled = origin.canEdit && !isMobile;
  const dragActive = enabled && dragDepth > 0;

  return (
    <div
      className={cn("relative", className)}
      data-testid="code-snapshot-drop-zone"
      onDragEnter={(event) => {
        if (!enabled || !event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        setDragDepth((depth) => depth + 1);
      }}
      onDragLeave={(event) => {
        if (!enabled) return;
        event.preventDefault();
        setDragDepth((depth) => Math.max(0, depth - 1));
      }}
      onDragOver={(event) => {
        if (!enabled || !event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }}
      onDrop={(event) => {
        if (!enabled) return;
        event.preventDefault();
        setDragDepth(0);
        const files = imageFiles(event.dataTransfer.files);
        if (files.length === 0) {
          showToast("Drop a PNG, JPEG, GIF, or WebP image.");
          return;
        }
        void importImages(files, "uploaded_image", save);
      }}
    >
      {children}
      {dragActive && (
        <div
          className="pointer-events-none absolute inset-0 z-30 flex items-center justify-center rounded-xl border-2 border-dashed border-primary/60 bg-background/75 text-sm font-medium text-foreground backdrop-blur-xs"
          data-testid="code-snapshot-drop-target"
        >
          Drop to add snapshot
        </div>
      )}
    </div>
  );
}

interface Point {
  x: number;
  y: number;
}

function RegionCaptureOverlay({
  onCapture,
  onCancel,
}: {
  onCapture: (rect: { left: number; top: number; width: number; height: number }) => void;
  onCancel: () => void;
}) {
  const [start, setStart] = useState<Point | null>(null);
  const [current, setCurrent] = useState<Point | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onCancel();
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [onCancel]);

  const selection =
    start && current
      ? {
          left: Math.min(start.x, current.x),
          top: Math.min(start.y, current.y),
          width: Math.abs(current.x - start.x),
          height: Math.abs(current.y - start.y),
        }
      : null;

  const overlay = (
    <div
      ref={overlayRef}
      aria-label="Select snapshot region; press Escape to cancel"
      className={cn(
        "pointer-events-auto fixed inset-0 z-[200] cursor-crosshair touch-none",
        !selection && "bg-black/10",
      )}
      data-code-snapshot-exclude="true"
      data-testid="code-snapshot-capture-overlay"
      role="application"
      onContextMenu={(event) => event.preventDefault()}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        const point = { x: event.clientX, y: event.clientY };
        setStart(point);
        setCurrent(point);
      }}
      onPointerMove={(event) => {
        if (!start) return;
        event.preventDefault();
        setCurrent({ x: event.clientX, y: event.clientY });
      }}
      onPointerUp={(event) => {
        if (!start) return;
        event.preventDefault();
        const end = { x: event.clientX, y: event.clientY };
        const rect = {
          left: Math.min(start.x, end.x),
          top: Math.min(start.y, end.y),
          width: Math.abs(end.x - start.x),
          height: Math.abs(end.y - start.y),
        };
        if (rect.width < 4 || rect.height < 4) {
          onCancel();
          return;
        }
        if (overlayRef.current) overlayRef.current.style.display = "none";
        onCapture(rect);
      }}
    >
      {selection && (
        <div
          className="absolute border border-white/90 shadow-[0_0_0_9999px_rgb(0_0_0/0.28)]"
          data-testid="code-snapshot-selection"
          style={selection}
        />
      )}
      {!start && (
        <div className="pointer-events-none absolute top-4 left-1/2 -translate-x-1/2 rounded-full bg-black/65 px-3 py-1.5 text-sm text-white shadow-md">
          Drag to capture · Esc to cancel
        </div>
      )}
    </div>
  );
  return createPortal(overlay, getEmbedRoot() ?? document.body);
}

const IMAGE_CACHE_LIMIT = 64;
const imageUrlCache = new Map<string, string>();
const imageRequests = new Map<string, Promise<string>>();

function cacheImage(path: string, url: string) {
  imageUrlCache.set(path, url);
  while (imageUrlCache.size > IMAGE_CACHE_LIMIT) {
    const oldest = imageUrlCache.keys().next();
    if (oldest.done) break;
    const urlToRevoke = imageUrlCache.get(oldest.value);
    imageUrlCache.delete(oldest.value);
    if (urlToRevoke) URL.revokeObjectURL(urlToRevoke);
  }
}

function loadSnapshotImage(path: string): Promise<string> {
  const cached = imageUrlCache.get(path);
  if (cached) return Promise.resolve(cached);
  const inFlight = imageRequests.get(path);
  if (inFlight) return inFlight;
  const request = authenticatedFetch(path)
    .then(async (response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return URL.createObjectURL(await response.blob());
    })
    .then((url) => {
      cacheImage(path, url);
      return url;
    })
    .finally(() => imageRequests.delete(path));
  imageRequests.set(path, request);
  return request;
}

function SnapshotImage({
  snapshot,
  className,
  style,
  onLoad,
  testId,
}: {
  snapshot: CodeSnapshot;
  className?: string;
  style?: CSSProperties;
  onLoad?: (event: SyntheticEvent<HTMLImageElement>) => void;
  testId?: string;
}) {
  const [src, setSrc] = useState(() => imageUrlCache.get(snapshot.content_url) ?? null);
  useEffect(() => {
    let cancelled = false;
    void loadSnapshotImage(snapshot.content_url).then(
      (url) => {
        if (!cancelled) setSrc(url);
      },
      () => {
        if (!cancelled) setSrc(null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [snapshot.content_url]);

  if (!src) {
    return (
      <div
        aria-label="Loading snapshot"
        role="status"
        className={cn("animate-pulse bg-muted", className)}
      />
    );
  }
  return (
    <img
      src={src}
      alt="Code snapshot"
      draggable={false}
      className={className}
      style={style}
      data-testid={testId}
      onLoad={onLoad}
    />
  );
}

interface SnapshotViewState {
  zoom: number;
  offset: Point;
}

interface SnapshotViewportBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface SnapshotImageSize {
  width: number;
  height: number;
}

interface ViewerPinchState {
  distance: number;
  zoom: number;
  anchor: Point;
}

interface ViewerPointerState {
  pointers: Map<number, Point>;
  start: Point | null;
  last: Point | null;
  pinch: ViewerPinchState | null;
  hadMultiplePointers: boolean;
}

const SNAPSHOT_MIN_ZOOM = 1;
const SNAPSHOT_MAX_ZOOM = 8;
const SNAPSHOT_DOUBLE_TAP_ZOOM = 2.5;

function clampSnapshotZoom(zoom: number): number {
  return Math.min(SNAPSHOT_MAX_ZOOM, Math.max(SNAPSHOT_MIN_ZOOM, zoom));
}

function snapshotViewportBox(element: HTMLElement): SnapshotViewportBox {
  const rect = element.getBoundingClientRect();
  return {
    left: rect.left,
    top: rect.top,
    width: rect.width || element.clientWidth || window.innerWidth,
    height: rect.height || element.clientHeight || window.innerHeight,
  };
}

function fittedSnapshotSize(
  viewport: SnapshotViewportBox,
  image: SnapshotImageSize,
): SnapshotImageSize {
  if (image.width <= 0 || image.height <= 0) return { width: 0, height: 0 };
  const fit = Math.min(1, viewport.width / image.width, viewport.height / image.height);
  return { width: image.width * fit, height: image.height * fit };
}

function clampSnapshotOffset(
  offset: Point,
  zoom: number,
  viewport: SnapshotViewportBox,
  image: SnapshotImageSize,
): Point {
  const fitted = fittedSnapshotSize(viewport, image);
  if (fitted.width === 0 || fitted.height === 0) return { x: 0, y: 0 };
  const maxX = Math.max(0, (fitted.width * zoom - viewport.width) / 2);
  const maxY = Math.max(0, (fitted.height * zoom - viewport.height) / 2);
  return {
    x: Math.min(maxX, Math.max(-maxX, offset.x)),
    y: Math.min(maxY, Math.max(-maxY, offset.y)),
  };
}

function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

function pointDistance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function SnapshotViewer({
  snapshots,
  index,
  onIndexChange,
  onBack,
  onClose,
}: {
  snapshots: CodeSnapshot[];
  index: number;
  onIndexChange: (index: number) => void;
  onBack: () => void;
  onClose: () => void;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const imageSizeRef = useRef<SnapshotImageSize>({ width: 0, height: 0 });
  const [view, setView] = useState<SnapshotViewState>({
    zoom: SNAPSHOT_MIN_ZOOM,
    offset: { x: 0, y: 0 },
  });
  const viewRef = useRef(view);
  const [animateTransform, setAnimateTransform] = useState(false);
  const [chromeVisible, setChromeVisible] = useState(true);
  const pointerRef = useRef<ViewerPointerState>({
    pointers: new Map(),
    start: null,
    last: null,
    pinch: null,
    hadMultiplePointers: false,
  });
  const lastTapRef = useRef<{ at: number; point: Point } | null>(null);
  const tapTimerRef = useRef<number | null>(null);

  const updateView = useCallback((zoom: number, offset: Point, animate = false) => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const clampedZoom = clampSnapshotZoom(zoom);
    const next = {
      zoom: clampedZoom,
      offset: clampSnapshotOffset(
        offset,
        clampedZoom,
        snapshotViewportBox(viewport),
        imageSizeRef.current,
      ),
    };
    viewRef.current = next;
    setAnimateTransform(animate);
    setView(next);
  }, []);

  const resetView = useCallback(
    (animate = true) => updateView(SNAPSHOT_MIN_ZOOM, { x: 0, y: 0 }, animate),
    [updateView],
  );

  const zoomAtPoint = useCallback(
    (nextZoom: number, focalPoint: Point, animate = true) => {
      const viewport = viewportRef.current;
      if (!viewport) return;
      const box = snapshotViewportBox(viewport);
      const current = viewRef.current;
      const center = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
      const anchor = {
        x: (focalPoint.x - center.x - current.offset.x) / current.zoom,
        y: (focalPoint.y - center.y - current.offset.y) / current.zoom,
      };
      const zoom = clampSnapshotZoom(nextZoom);
      updateView(
        zoom,
        {
          x: focalPoint.x - center.x - anchor.x * zoom,
          y: focalPoint.y - center.y - anchor.y * zoom,
        },
        animate,
      );
    },
    [updateView],
  );

  const changeIndex = useCallback(
    (next: number) => {
      if (next < 0 || next >= snapshots.length) return;
      imageSizeRef.current = { width: 0, height: 0 };
      viewRef.current = { zoom: SNAPSHOT_MIN_ZOOM, offset: { x: 0, y: 0 } };
      setView(viewRef.current);
      setAnimateTransform(false);
      onIndexChange(next);
    },
    [onIndexChange, snapshots.length],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "ArrowLeft") changeIndex(index - 1);
      else if (event.key === "ArrowRight") changeIndex(index + 1);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [changeIndex, index]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const direction = event.deltaY < 0 ? 1 : -1;
      zoomAtPoint(viewRef.current.zoom + direction * 0.5, {
        x: event.clientX,
        y: event.clientY,
      });
    };
    viewport.addEventListener("wheel", onWheel, { passive: false });
    return () => viewport.removeEventListener("wheel", onWheel);
  }, [zoomAtPoint]);

  useEffect(() => {
    const onResize = () => {
      const current = viewRef.current;
      updateView(current.zoom, current.offset, true);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [updateView]);

  useEffect(
    () => () => {
      if (tapTimerRef.current !== null) window.clearTimeout(tapTimerRef.current);
    },
    [],
  );

  const updatePointer = (event: ReactPointerEvent<HTMLDivElement>) => {
    pointerRef.current.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  };

  const handleTap = (point: Point) => {
    const now = Date.now();
    const previous = lastTapRef.current;
    if (previous && now - previous.at <= 320 && pointDistance(previous.point, point) <= 32) {
      if (tapTimerRef.current !== null) window.clearTimeout(tapTimerRef.current);
      tapTimerRef.current = null;
      lastTapRef.current = null;
      if (viewRef.current.zoom > SNAPSHOT_MIN_ZOOM + 0.01) resetView();
      else zoomAtPoint(SNAPSHOT_DOUBLE_TAP_ZOOM, point);
      return;
    }
    lastTapRef.current = { at: now, point };
    if (tapTimerRef.current !== null) window.clearTimeout(tapTimerRef.current);
    tapTimerRef.current = window.setTimeout(() => {
      setChromeVisible((visible) => !visible);
      tapTimerRef.current = null;
      lastTapRef.current = null;
    }, 260);
  };

  return (
    <div
      className="absolute inset-0 overflow-hidden bg-black text-white"
      data-testid="snapshot-viewer"
    >
      <div
        ref={viewportRef}
        aria-label="Snapshot image viewport"
        className="absolute inset-0 flex touch-none items-center justify-center overflow-hidden overscroll-none"
        data-testid="snapshot-viewer-viewport"
        data-zoom={view.zoom.toFixed(3)}
        data-offset-x={view.offset.x.toFixed(1)}
        data-offset-y={view.offset.y.toFixed(1)}
        onDoubleClick={(event) => event.preventDefault()}
        onPointerDown={(event) => {
          try {
            event.currentTarget.setPointerCapture?.(event.pointerId);
          } catch {
            // Synthetic events and older embedded browsers may not expose an
            // active native pointer to capture; document-local tracking still works.
          }
          setAnimateTransform(false);
          updatePointer(event);
          const point = { x: event.clientX, y: event.clientY };
          const state = pointerRef.current;
          if (state.pointers.size === 1) {
            state.start = point;
            state.last = point;
            state.pinch = null;
            state.hadMultiplePointers = false;
          }
          if (state.pointers.size === 2) {
            const [a, b] = Array.from(state.pointers.values());
            const viewport = viewportRef.current;
            if (!viewport) return;
            const box = snapshotViewportBox(viewport);
            const focalPoint = midpoint(a!, b!);
            const center = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
            const current = viewRef.current;
            state.pinch = {
              distance: pointDistance(a!, b!),
              zoom: current.zoom,
              anchor: {
                x: (focalPoint.x - center.x - current.offset.x) / current.zoom,
                y: (focalPoint.y - center.y - current.offset.y) / current.zoom,
              },
            };
            state.hadMultiplePointers = true;
          }
        }}
        onPointerMove={(event) => {
          const state = pointerRef.current;
          if (!state.pointers.has(event.pointerId)) return;
          const prior = state.pointers.get(event.pointerId)!;
          updatePointer(event);
          if (state.pointers.size >= 2) {
            const [a, b] = Array.from(state.pointers.values());
            const pinch = state.pinch;
            const viewport = viewportRef.current;
            if (pinch && pinch.distance > 0 && viewport) {
              const box = snapshotViewportBox(viewport);
              const center = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
              const focalPoint = midpoint(a!, b!);
              const zoom = clampSnapshotZoom(pinch.zoom * (pointDistance(a!, b!) / pinch.distance));
              updateView(
                zoom,
                {
                  x: focalPoint.x - center.x - pinch.anchor.x * zoom,
                  y: focalPoint.y - center.y - pinch.anchor.y * zoom,
                },
                false,
              );
            }
          } else if (viewRef.current.zoom > SNAPSHOT_MIN_ZOOM + 0.01) {
            updateView(
              viewRef.current.zoom,
              {
                x: viewRef.current.offset.x + event.clientX - prior.x,
                y: viewRef.current.offset.y + event.clientY - prior.y,
              },
              false,
            );
          }
          state.last = { x: event.clientX, y: event.clientY };
        }}
        onPointerUp={(event) => {
          const state = pointerRef.current;
          const start = state.start;
          const end = { x: event.clientX, y: event.clientY };
          state.pointers.delete(event.pointerId);
          if (state.pointers.size === 1) {
            state.last = Array.from(state.pointers.values())[0] ?? null;
            state.start = null;
            state.pinch = null;
            return;
          }
          if (state.pointers.size === 0 && start && !state.hadMultiplePointers) {
            const dx = end.x - start.x;
            const dy = end.y - start.y;
            if (
              viewRef.current.zoom <= SNAPSHOT_MIN_ZOOM + 0.01 &&
              Math.abs(dx) > 55 &&
              Math.abs(dx) > Math.abs(dy) * 1.25
            ) {
              changeIndex(index + (dx < 0 ? 1 : -1));
            } else if (Math.hypot(dx, dy) < 8) {
              handleTap(end);
            }
          }
          if (state.pointers.size === 0) {
            state.start = null;
            state.last = null;
            state.pinch = null;
            state.hadMultiplePointers = false;
          }
        }}
        onPointerCancel={(event) => {
          const state = pointerRef.current;
          state.pointers.delete(event.pointerId);
          if (state.pointers.size === 0) {
            state.start = null;
            state.last = null;
            state.pinch = null;
            state.hadMultiplePointers = false;
          }
        }}
      >
        <SnapshotImage
          key={snapshots[index]!.id}
          snapshot={snapshots[index]!}
          className="max-h-full max-w-full origin-center select-none object-contain"
          testId="snapshot-viewer-image"
          onLoad={(event) => {
            imageSizeRef.current = {
              width: event.currentTarget.naturalWidth,
              height: event.currentTarget.naturalHeight,
            };
            const current = viewRef.current;
            updateView(current.zoom, current.offset, false);
          }}
          style={{
            transform: `translate3d(${view.offset.x}px, ${view.offset.y}px, 0) scale(${view.zoom})`,
            transition: animateTransform
              ? "transform 180ms cubic-bezier(0.2, 0.8, 0.2, 1)"
              : "none",
            willChange: "transform",
          }}
        />
        <span className="sr-only" aria-live="polite">
          Zoom {Math.round(view.zoom * 100)} percent
        </span>
      </div>
      <header
        className={cn(
          "absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/70 to-transparent p-3 transition-opacity",
          chromeVisible ? "opacity-100" : "pointer-events-none opacity-0",
        )}
        style={{ paddingTop: "max(0.75rem, var(--omnigent-safe-top, 0px))" }}
      >
        <Button
          type="button"
          variant="ghost"
          className="text-white hover:bg-white/15"
          onClick={onBack}
        >
          <ChevronLeftIcon className="size-4" />
          Back
        </Button>
        <span className="text-sm tabular-nums" aria-live="polite">
          {index + 1} of {snapshots.length}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="text-white hover:bg-white/15"
          aria-label="Close snapshot viewer"
          onClick={onClose}
        >
          <XIcon className="size-5" />
        </Button>
      </header>
      <button
        type="button"
        aria-label="Previous snapshot"
        className="sr-only"
        disabled={index === 0}
        onClick={() => changeIndex(index - 1)}
      />
      <button
        type="button"
        aria-label="Next snapshot"
        className="sr-only"
        disabled={index === snapshots.length - 1}
        onClick={() => changeIndex(index + 1)}
      />
    </div>
  );
}

function SnapshotGallery({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { origin, snapshots, isSaving, save, remove } = useCodeSnapshotBlock();
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [dragActive, setDragActive] = useState(false);

  useEffect(() => {
    if (!open) setSelectedIndex(null);
  }, [open]);

  const acceptFiles = useCallback(
    (files: File[], captureType: "uploaded_image" | "clipboard_image") => {
      if (!origin.canEdit || files.length === 0) return;
      void importImages(files, captureType, save);
    },
    [origin.canEdit, save],
  );

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal container={getEmbedRoot() ?? undefined}>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[120] bg-black/70 backdrop-blur-xs" />
        <DialogPrimitive.Content
          data-testid="snapshot-gallery"
          aria-describedby={undefined}
          className="fixed inset-0 z-[130] flex flex-col overflow-hidden bg-background text-foreground outline-none md:inset-6 md:rounded-2xl md:border md:border-border md:shadow-2xl"
          onInteractOutside={(event) => event.preventDefault()}
          onPaste={(event) => {
            if (!origin.canEdit) return;
            const files = Array.from(event.clipboardData.items)
              .filter((item) => item.kind === "file")
              .map((item) => item.getAsFile())
              .filter(
                (file): file is File => file !== null && SUPPORTED_IMAGE_TYPES.has(file.type),
              );
            if (files.length > 0) {
              event.preventDefault();
              acceptFiles(files, "clipboard_image");
            }
          }}
          onDragOver={(event) => {
            if (!origin.canEdit || !event.dataTransfer.types.includes("Files")) return;
            event.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={(event) => {
            if (event.currentTarget === event.target) setDragActive(false);
          }}
          onDrop={(event) => {
            if (!origin.canEdit) return;
            event.preventDefault();
            setDragActive(false);
            acceptFiles(imageFiles(event.dataTransfer.files), "uploaded_image");
          }}
        >
          <DialogPrimitive.Title className="sr-only">Code snapshot gallery</DialogPrimitive.Title>
          {selectedIndex === null ? (
            <>
              <header
                className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4"
                style={{ paddingTop: "var(--omnigent-safe-top, 0px)" }}
              >
                <div>
                  <h2 className="font-semibold">Code snapshots</h2>
                  <p className="text-sm text-muted-foreground">
                    {snapshots.length} {snapshots.length === 1 ? "snapshot" : "snapshots"}
                  </p>
                </div>
                <DialogPrimitive.Close asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="Close snapshot gallery"
                  >
                    <XIcon className="size-5" />
                  </Button>
                </DialogPrimitive.Close>
              </header>
              <div className="relative min-h-0 flex-1 overflow-auto p-3 md:p-5">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
                  {snapshots.map((snapshot, index) => (
                    <div
                      key={snapshot.id}
                      className="group/snapshot relative aspect-square overflow-hidden rounded-xl border border-border bg-muted"
                    >
                      <button
                        type="button"
                        aria-label={`Open snapshot ${index + 1} of ${snapshots.length}`}
                        className="size-full focus-visible:ring-2 focus-visible:ring-ring"
                        onClick={() => setSelectedIndex(index)}
                      >
                        <SnapshotImage snapshot={snapshot} className="size-full object-cover" />
                      </button>
                      {origin.canEdit && (
                        <Button
                          type="button"
                          variant="secondary"
                          size="icon-sm"
                          aria-label={`Delete snapshot ${index + 1}`}
                          className="absolute top-1.5 right-1.5 opacity-90 md:opacity-0 md:group-hover/snapshot:opacity-100 md:group-focus-within/snapshot:opacity-100"
                          onClick={() => void remove(snapshot.id)}
                        >
                          <Trash2Icon className="size-4" />
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
                {dragActive && (
                  <div className="pointer-events-none absolute inset-3 z-10 flex items-center justify-center rounded-xl border-2 border-dashed border-primary/60 bg-background/80 font-medium backdrop-blur-xs">
                    Drop to add snapshot
                  </div>
                )}
                {isSaving && (
                  <div
                    role="status"
                    className="fixed right-4 bottom-4 rounded-full bg-popover px-3 py-1.5 text-sm shadow-lg ring-1 ring-border"
                  >
                    Saving snapshot…
                  </div>
                )}
              </div>
            </>
          ) : (
            <SnapshotViewer
              snapshots={snapshots}
              index={Math.min(selectedIndex, snapshots.length - 1)}
              onIndexChange={setSelectedIndex}
              onBack={() => setSelectedIndex(null)}
              onClose={() => onOpenChange(false)}
            />
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function CodeSnapshotToolbarControls({
  className,
  getQuickCaptureTarget,
}: {
  className: string;
  getQuickCaptureTarget: () => HTMLElement | null;
}) {
  const { origin, snapshots, isLoading, isSaving, save } = useCodeSnapshotBlock();
  const isMobile = useIsMobileViewport() || isIOSShell() || isAndroidShell() || isMobileWebDevice();
  const [captureOpen, setCaptureOpen] = useState(false);
  const [galleryOpen, setGalleryOpen] = useState(false);
  const [quickCapturePhase, setQuickCapturePhase] = useState<
    "idle" | "capturing" | "saving" | "saved" | "failed"
  >("idle");
  const quickCaptureTimer = useRef<number | null>(null);

  const settleQuickCapture = useCallback((phase: "saved" | "failed") => {
    setQuickCapturePhase(phase);
    if (quickCaptureTimer.current !== null) window.clearTimeout(quickCaptureTimer.current);
    quickCaptureTimer.current = window.setTimeout(() => {
      setQuickCapturePhase("idle");
      quickCaptureTimer.current = null;
    }, 1_500);
  }, []);

  useEffect(
    () => () => {
      if (quickCaptureTimer.current !== null) window.clearTimeout(quickCaptureTimer.current);
    },
    [],
  );

  useEffect(
    () =>
      onResponseSignalNavigation(() => {
        setCaptureOpen(false);
        setGalleryOpen(false);
      }),
    [],
  );

  const captureRegion = useCallback(
    async (rect: { left: number; top: number; width: number; height: number }) => {
      setCaptureOpen(false);
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      });
      try {
        await save(await captureViewportRegion(rect), "region_capture");
      } catch {
        // The provider already surfaced a user-facing error.
      }
    },
    [save],
  );

  const clickCamera = useCallback(async () => {
    if (!isMobile) {
      setCaptureOpen(true);
      return;
    }
    const target = getQuickCaptureTarget();
    if (!target) {
      showToast("No visible code content to capture.");
      return;
    }
    setQuickCapturePhase("capturing");
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => resolve());
    });
    try {
      const image = await captureVisibleElement(target);
      setQuickCapturePhase("saving");
      await save(image, "mobile_quick_capture");
      settleQuickCapture("saved");
    } catch {
      settleQuickCapture("failed");
      // The provider already surfaced a user-facing error.
    }
  }, [getQuickCaptureTarget, isMobile, save, settleQuickCapture]);

  if (!origin.canEdit && snapshots.length === 0) return null;

  const quickCaptureBusy = quickCapturePhase === "capturing" || quickCapturePhase === "saving";
  const quickCaptureLabel =
    quickCapturePhase === "capturing"
      ? "Capturing visible code…"
      : quickCapturePhase === "saving"
        ? "Saving snapshot…"
        : quickCapturePhase === "saved"
          ? "Snapshot saved"
          : quickCapturePhase === "failed"
            ? "Snapshot failed"
            : null;

  return (
    <>
      {origin.canEdit && (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className={className}
                aria-label={isMobile ? "Take quick code snapshot" : "Capture snapshot region"}
                aria-busy={isMobile && quickCaptureBusy}
                title={isMobile ? "Quick snapshot" : "Capture snapshot"}
                disabled={isSaving || quickCaptureBusy}
                onClick={() => void clickCamera()}
              >
                {isMobile && quickCaptureBusy ? (
                  <Loader2Icon className="animate-spin" size={14} />
                ) : quickCapturePhase === "saved" ? (
                  <CheckIcon size={14} />
                ) : (
                  <CameraIcon size={14} />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{isMobile ? "Quick snapshot" : "Capture snapshot"}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}
      {!isLoading && snapshots.length > 0 && (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className={cn(className, "w-auto gap-1 px-2")}
                aria-label={`Open ${snapshots.length} code snapshots`}
                title="Open snapshot gallery"
                onClick={() => setGalleryOpen(true)}
              >
                <ImagesIcon size={14} />
                <span className="text-xs tabular-nums">{snapshots.length}</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>Open snapshot gallery</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}
      {captureOpen && (
        <RegionCaptureOverlay
          onCapture={(rect) => void captureRegion(rect)}
          onCancel={() => setCaptureOpen(false)}
        />
      )}
      {isMobile && quickCaptureLabel
        ? createPortal(
            <div
              className={cn(
                "pointer-events-none fixed top-[max(4.5rem,calc(var(--omnigent-safe-top,0px)+4.5rem))] left-1/2 z-[250] flex -translate-x-1/2 items-center gap-2 rounded-full bg-popover/95 px-3 py-2 text-sm font-medium whitespace-nowrap text-popover-foreground shadow-lg ring-1 ring-border backdrop-blur",
                quickCapturePhase === "failed" && "text-destructive",
              )}
              data-code-snapshot-exclude="true"
              data-testid="quick-snapshot-status"
              role="status"
              aria-live="polite"
            >
              {quickCaptureBusy ? (
                <Loader2Icon className="size-4 animate-spin" aria-hidden="true" />
              ) : quickCapturePhase === "saved" ? (
                <CheckIcon className="size-4 text-emerald-500" aria-hidden="true" />
              ) : (
                <XIcon className="size-4" aria-hidden="true" />
              )}
              {quickCaptureLabel}
            </div>,
            getEmbedRoot() ?? document.body,
          )
        : null}
      <SnapshotGallery open={galleryOpen} onOpenChange={setGalleryOpen} />
    </>
  );
}
