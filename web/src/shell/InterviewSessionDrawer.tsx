import { useEffect, useMemo, useRef } from "react";
import { MenuIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useConversations } from "@/hooks/useConversations";
import { Link, useParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { conversationDisplayLabel } from "./sidebarNav";

interface InterviewSessionDrawerProps {
  open: boolean;
  onClose: () => void;
  dragProgress?: number | null;
}

/** Reader-first mobile session navigation used only by Interview Mode. */
export function InterviewSessionDrawer({
  open,
  onClose,
  dragProgress = null,
}: InterviewSessionDrawerProps) {
  const { conversationId } = useParams<{ conversationId: string }>();
  const query = useConversations("", true);
  const conversations = useMemo(
    () => query.data?.pages.flatMap((page) => page.data) ?? [],
    [query.data],
  );
  const interactiveDrag = dragProgress !== null;
  const visible = open || interactiveDrag;
  const drawerRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    returnFocusRef.current = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = drawerRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      returnFocusRef.current?.focus();
      returnFocusRef.current = null;
    };
  }, [onClose, open]);

  return (
    <div
      className={cn("fixed inset-0 z-50", !visible && "pointer-events-none")}
      aria-hidden={!visible}
      inert={visible ? undefined : ("true" as unknown as boolean)}
      data-testid="interview-session-drawer"
    >
      <button
        type="button"
        aria-label="Close session drawer"
        className="absolute inset-0 bg-black/35 transition-opacity"
        style={{ opacity: interactiveDrag ? dragProgress : open ? 1 : 0 }}
        onClick={onClose}
        tabIndex={visible ? 0 : -1}
      />
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label="Sessions"
        className={cn(
          "absolute inset-y-0 left-0 flex w-[min(88vw,22rem)] flex-col bg-card shadow-2xl transition-transform duration-200",
          !open && !interactiveDrag && "-translate-x-full",
        )}
        style={
          interactiveDrag
            ? { transform: `translateX(${Math.round((dragProgress - 1) * 100)}%)` }
            : undefined
        }
      >
        <div
          className="flex h-14 shrink-0 items-center justify-between border-b border-border px-3"
          style={{ paddingTop: "var(--omnigent-safe-top, 0px)" }}
        >
          <span className="flex items-center gap-2 font-medium">
            <MenuIcon className="size-4" aria-hidden="true" /> Sessions
          </span>
          <Button
            ref={closeButtonRef}
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Close"
            onClick={onClose}
          >
            <XIcon className="size-4" />
          </Button>
        </div>
        <nav className="min-h-0 flex-1 overflow-y-auto px-2 py-2" aria-label="Session list">
          {query.isLoading && <p className="px-2 py-3 text-sm text-muted-foreground">Loading…</p>}
          {!query.isLoading && conversations.length === 0 && (
            <p className="px-2 py-3 text-sm text-muted-foreground">No sessions yet.</p>
          )}
          {conversations.map((conversation) => (
            <Link
              key={conversation.id}
              to={`/c/${conversation.id}`}
              onClick={onClose}
              aria-current={conversation.id === conversationId ? "page" : undefined}
              className={cn(
                "block truncate rounded-lg px-3 py-2.5 text-sm text-foreground/80 hover:bg-muted",
                conversation.id === conversationId && "bg-muted font-medium text-foreground",
              )}
            >
              {conversationDisplayLabel(conversation)}
            </Link>
          ))}
        </nav>
        <div style={{ height: "var(--omnigent-safe-bottom, 0px)" }} aria-hidden="true" />
      </aside>
    </div>
  );
}
