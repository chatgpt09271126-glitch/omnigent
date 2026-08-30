import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getEmbedRoot } from "@/lib/host";
import {
  emitResponseSignalNavigation,
  onResponseSignalArrival,
  type ResponseSignalArrival,
} from "@/lib/responseSignals";
import { useNavigate } from "@/lib/routing";
import { useChatStore } from "@/store/chatStore";

export function InterviewAttentionNotice({
  conversationId,
  showAttention = true,
}: {
  conversationId: string | undefined;
  showAttention?: boolean;
}) {
  const navigate = useNavigate();
  const [notice, setNotice] = useState<ResponseSignalArrival | null>(null);
  const [expanded, setExpanded] = useState(false);
  const pendingTarget = useRef<ResponseSignalArrival | null>(null);
  const dismissedKey = useRef<string | null>(null);
  const collapseTimer = useRef<number | null>(null);
  const removeTimer = useRef<number | null>(null);
  const focusTimer = useRef<number | null>(null);

  useEffect(
    () =>
      onResponseSignalArrival((arrival) => {
        const isResponseRequest =
          arrival.signalType === "shorter" || arrival.signalType === "more_detail";
        if (
          arrival.source !== "remote" ||
          (!isResponseRequest && !(showAttention && arrival.signalType === "attention"))
        ) {
          return;
        }
        const arrivalKey = noticeKey(arrival);
        if (!arrival.active) {
          dismissedKey.current = null;
          if (collapseTimer.current !== null) window.clearTimeout(collapseTimer.current);
          if (removeTimer.current !== null) window.clearTimeout(removeTimer.current);
          setNotice((current) =>
            current?.conversationId === arrival.conversationId &&
            current.responseId === arrival.responseId &&
            current.signalType === arrival.signalType
              ? null
              : current,
          );
          return;
        }
        if (dismissedKey.current === arrivalKey) return;
        setNotice(arrival);
        setExpanded(true);
        if (collapseTimer.current !== null) window.clearTimeout(collapseTimer.current);
        if (removeTimer.current !== null) window.clearTimeout(removeTimer.current);
        collapseTimer.current = window.setTimeout(
          () => {
            setExpanded(false);
            if (isResponseRequest) {
              removeTimer.current = window.setTimeout(() => {
                setNotice((current) =>
                  current && noticeKey(current) === arrivalKey ? null : current,
                );
                removeTimer.current = null;
              }, 1_200);
            }
          },
          isResponseRequest ? 1_400 : 1_800,
        );
      }),
    [conversationId, showAttention],
  );

  useEffect(
    () => () => {
      if (collapseTimer.current !== null) window.clearTimeout(collapseTimer.current);
      if (removeTimer.current !== null) window.clearTimeout(removeTimer.current);
      if (focusTimer.current !== null) window.clearInterval(focusTimer.current);
    },
    [],
  );

  const focusPendingTarget = useCallback(() => {
    const target = pendingTarget.current;
    if (!target) return;
    let attempts = 0;
    if (focusTimer.current !== null) window.clearInterval(focusTimer.current);
    const seek = () => {
      attempts += 1;
      const element = findResponseElement(target.responseId);
      if (element) {
        if (focusTimer.current !== null) window.clearInterval(focusTimer.current);
        focusTimer.current = null;
        pendingTarget.current = null;
        element.scrollIntoView({ behavior: "smooth", block: "center" });
        element.focus({ preventScroll: true });
        return;
      }
      const state = useChatStore.getState();
      if (
        state.conversationId === target.conversationId &&
        state.hasMoreHistory &&
        !state.loadingMoreHistory
      ) {
        void state.loadMoreHistory();
      }
      if (attempts >= 300) {
        if (focusTimer.current !== null) window.clearInterval(focusTimer.current);
        focusTimer.current = null;
        pendingTarget.current = null;
      }
    };
    seek();
    if (pendingTarget.current) focusTimer.current = window.setInterval(seek, 100);
  }, []);

  if (!notice) return null;

  const dismiss = () => {
    dismissedKey.current = noticeKey(notice);
    if (collapseTimer.current !== null) window.clearTimeout(collapseTimer.current);
    if (removeTimer.current !== null) window.clearTimeout(removeTimer.current);
    collapseTimer.current = null;
    removeTimer.current = null;
    setExpanded(false);
    setNotice(null);
  };

  const isResponseRequest = notice.signalType === "shorter" || notice.signalType === "more_detail";
  const prominent = isResponseRequest && expanded;
  const overlay = (
    <div
      className={`pointer-events-auto fixed right-3 z-[300] flex touch-manipulation items-center border border-border bg-popover/95 shadow-lg backdrop-blur transition-[padding,border-radius,box-shadow,transform] duration-300 ${
        notice.signalType === "attention"
          ? "response-attention-notice rounded-full p-1"
          : prominent
            ? "response-request-notice rounded-2xl p-2 ring-2 ring-primary/35 shadow-2xl"
            : "rounded-full p-1"
      }`}
      style={{ top: "max(4.25rem, calc(var(--omnigent-safe-top, 0px) + 3rem))" }}
      role="status"
      aria-live={expanded ? "assertive" : "off"}
      data-testid="interview-attention-notice"
      data-signal-type={notice.signalType}
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        className={`flex items-center gap-2 rounded-full font-medium transition-[min-height,padding,font-size] duration-300 ${
          prominent ? "min-h-14 px-5 text-lg" : "min-h-9 px-3 text-sm"
        }`}
        aria-label={navigationLabel(notice)}
        onClick={() => {
          const target = notice;
          emitResponseSignalNavigation(target);
          pendingTarget.current = target;
          dismiss();
          if (target.conversationId !== conversationId) {
            navigate(`/c/${target.conversationId}`);
          }
          focusPendingTarget();
        }}
      >
        {notice.signalType === "attention" && (
          <span className="text-base" aria-hidden="true">
            ‼
          </span>
        )}
        {noticeText(notice, expanded)}
      </button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-10 shrink-0 rounded-full"
        aria-label={
          notice.signalType === "attention"
            ? "Dismiss attention notice"
            : `Dismiss ${signalLabel(notice).toLowerCase()} request notification`
        }
        onClick={dismiss}
      >
        <XIcon className="size-4" />
      </Button>
    </div>
  );
  return createPortal(overlay, getEmbedRoot() ?? document.body);
}

function signalLabel(arrival: ResponseSignalArrival): string {
  if (arrival.signalType === "shorter") return "Shorter";
  if (arrival.signalType === "more_detail") return "More detail";
  return "Attention";
}

function noticeText(arrival: ResponseSignalArrival, expanded: boolean): string {
  const label = signalLabel(arrival);
  if (arrival.signalType === "attention") return "Attention requested";
  if (expanded) return `${label} requested`;
  return label;
}

function navigationLabel(arrival: ResponseSignalArrival): string {
  return arrival.signalType === "attention"
    ? "Go to response requesting attention"
    : `Go to response requesting ${signalLabel(arrival)}`;
}

function noticeKey(arrival: ResponseSignalArrival): string {
  return `${arrival.conversationId}:${arrival.responseId}:${arrival.signalType}:${arrival.signaledAt ?? "unknown"}`;
}

function findResponseElement(responseId: string): HTMLElement | null {
  const roots = [getEmbedRoot(), document].filter(
    (root): root is HTMLElement | Document => root !== null,
  );
  for (const root of roots) {
    const attentionTarget = Array.from(
      root.querySelectorAll<HTMLElement>("[data-response-attention-target]"),
    ).find((element) => element.dataset.responseAttentionTarget === responseId);
    if (attentionTarget) return attentionTarget;
    const match = Array.from(root.querySelectorAll<HTMLElement>("[data-response-id]")).find(
      (element) => element.dataset.responseId === responseId,
    );
    if (match) return match;
  }
  return null;
}
