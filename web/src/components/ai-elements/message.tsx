"use client";

import { Button } from "@/components/ui/button";
import { ButtonGroup, ButtonGroupText } from "@/components/ui/button-group";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { copyText } from "@/lib/clipboard";
import type { CodeSnapshotOrigin } from "@/lib/codeSnapshotsApi";
import { cn } from "@/lib/utils";
import type { UIMessage } from "ai";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, CopyIcon, WrapTextIcon } from "lucide-react";
import type {
  ComponentProps,
  ComponentType,
  HTMLAttributes,
  PointerEvent as ReactPointerEvent,
  ReactElement,
  ReactNode,
} from "react";
import {
  cloneElement,
  createContext,
  forwardRef,
  isValidElement,
  memo,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Block as StreamdownBlock,
  type BlockProps as StreamdownBlockProps,
  parseMarkdownIntoBlocks,
  Streamdown,
  type ExtraProps,
  type StreamdownProps,
} from "streamdown";

import {
  AutoCardViewer,
  CodeSnapshotBlockProvider,
  CodeSnapshotDropZone,
  CodeSnapshotToolbarControls,
  useCodeSnapshotBlock,
} from "@/components/code-snapshots/CodeSnapshots";

import {
  CHAT_LINK_SAFETY,
  FILE_LINK_STREAMDOWN_REHYPE_PLUGINS,
  SECURE_STREAMDOWN_REHYPE_PLUGINS,
  STREAMDOWN_PLUGINS,
} from "./streamdown-security";
import { CodeFocusViewer } from "./CodeFocusViewer";

export type MessageProps = HTMLAttributes<HTMLDivElement> & {
  from: UIMessage["role"];
};

export const Message = ({ className, from, ...props }: MessageProps) => (
  <div
    className={cn(
      // min-w-0 lets this flex item shrink below its content's intrinsic width instead of widening the column.
      "group flex w-full min-w-0 max-w-[95%] flex-col gap-2",
      from === "user" ? "is-user ml-auto justify-end" : "is-assistant",
      className,
    )}
    {...props}
  />
);

export type MessageContentProps = HTMLAttributes<HTMLDivElement>;

export const MessageContent = ({ children, className, ...props }: MessageContentProps) => (
  <div
    className={cn(
      // User and assistant prose share the settings-driven interface text
      // token, so their size and line-height stay in lockstep.
      "is-user:dark flex w-fit min-w-0 max-w-full flex-col gap-2 text-ui",
      "group-[.is-user]:ml-auto group-[.is-user]:overflow-hidden group-[.is-user]:rounded-2xl group-[.is-user]:bg-muted group-[.is-user]:px-3 group-[.is-user]:py-2 group-[.is-user]:text-foreground group-[.is-user]:ring-1 group-[.is-user]:ring-border/60",
      // Tighter than the user bubble's gap-2 so muted single-line tool
      // ("See N steps") / reasoning rows don't look orphaned between prose.
      "group-[.is-assistant]:gap-1.5 group-[.is-assistant]:text-foreground",
      className,
    )}
    {...props}
  >
    {children}
  </div>
);

export type MessageActionsProps = ComponentProps<"div">;

export const MessageActions = ({ className, children, ...props }: MessageActionsProps) => (
  <div className={cn("flex items-center gap-3", className)} {...props}>
    {children}
  </div>
);

export type MessageActionProps = ComponentProps<typeof Button> & {
  tooltip?: string;
  label?: string;
};

export const MessageAction = forwardRef<HTMLButtonElement, MessageActionProps>(
  function MessageAction(
    { tooltip, children, label, className, variant = "ghost", size = "icon-sm", ...props },
    ref,
  ) {
    const button = (
      <Button
        ref={ref}
        size={size}
        type="button"
        variant={variant}
        className={cn("text-muted-foreground hover:text-foreground", className)}
        {...props}
      >
        {children}
        <span className="sr-only">{label || tooltip}</span>
      </Button>
    );

    if (tooltip) {
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>{button}</TooltipTrigger>
            <TooltipContent>
              <p>{tooltip}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    }

    return button;
  },
);

interface MessageBranchContextType {
  currentBranch: number;
  totalBranches: number;
  goToPrevious: () => void;
  goToNext: () => void;
  branches: ReactElement[];
  setBranches: (branches: ReactElement[]) => void;
}

const MessageBranchContext = createContext<MessageBranchContextType | null>(null);

const useMessageBranch = () => {
  const context = useContext(MessageBranchContext);

  if (!context) {
    throw new Error("MessageBranch components must be used within MessageBranch");
  }

  return context;
};

export type MessageBranchProps = HTMLAttributes<HTMLDivElement> & {
  defaultBranch?: number;
  onBranchChange?: (branchIndex: number) => void;
};

export const MessageBranch = ({
  defaultBranch = 0,
  onBranchChange,
  className,
  ...props
}: MessageBranchProps) => {
  const [currentBranch, setCurrentBranch] = useState(defaultBranch);
  const [branches, setBranches] = useState<ReactElement[]>([]);

  const handleBranchChange = useCallback(
    (newBranch: number) => {
      setCurrentBranch(newBranch);
      onBranchChange?.(newBranch);
    },
    [onBranchChange],
  );

  const goToPrevious = useCallback(() => {
    const newBranch = currentBranch > 0 ? currentBranch - 1 : branches.length - 1;
    handleBranchChange(newBranch);
  }, [currentBranch, branches.length, handleBranchChange]);

  const goToNext = useCallback(() => {
    const newBranch = currentBranch < branches.length - 1 ? currentBranch + 1 : 0;
    handleBranchChange(newBranch);
  }, [currentBranch, branches.length, handleBranchChange]);

  const contextValue = useMemo<MessageBranchContextType>(
    () => ({
      branches,
      currentBranch,
      goToNext,
      goToPrevious,
      setBranches,
      totalBranches: branches.length,
    }),
    [branches, currentBranch, goToNext, goToPrevious],
  );

  return (
    <MessageBranchContext.Provider value={contextValue}>
      <div className={cn("grid w-full gap-2 [&>div]:pb-0", className)} {...props} />
    </MessageBranchContext.Provider>
  );
};

export type MessageBranchContentProps = HTMLAttributes<HTMLDivElement>;

export const MessageBranchContent = ({ children, ...props }: MessageBranchContentProps) => {
  const { currentBranch, setBranches, branches } = useMessageBranch();
  const childrenArray = useMemo(
    () => (Array.isArray(children) ? children : [children]),
    [children],
  );

  // Use useEffect to update branches when they change
  useEffect(() => {
    if (branches.length !== childrenArray.length) {
      setBranches(childrenArray);
    }
  }, [childrenArray, branches, setBranches]);

  return childrenArray.map((branch, index) => (
    <div
      className={cn(
        "grid gap-2 overflow-hidden [&>div]:pb-0",
        index === currentBranch ? "block" : "hidden",
      )}
      key={branch.key}
      {...props}
    >
      {branch}
    </div>
  ));
};

export type MessageBranchSelectorProps = ComponentProps<typeof ButtonGroup>;

export const MessageBranchSelector = ({ className, ...props }: MessageBranchSelectorProps) => {
  const { totalBranches } = useMessageBranch();

  // Don't render if there's only one branch
  if (totalBranches <= 1) {
    return null;
  }

  return (
    <ButtonGroup
      className={cn(
        "[&>*:not(:first-child)]:rounded-l-md [&>*:not(:last-child)]:rounded-r-md",
        className,
      )}
      orientation="horizontal"
      {...props}
    />
  );
};

export type MessageBranchPreviousProps = ComponentProps<typeof Button>;

export const MessageBranchPrevious = ({ children, ...props }: MessageBranchPreviousProps) => {
  const { goToPrevious, totalBranches } = useMessageBranch();

  return (
    <Button
      aria-label="Previous branch"
      disabled={totalBranches <= 1}
      onClick={goToPrevious}
      size="icon-sm"
      type="button"
      variant="ghost"
      {...props}
    >
      {children ?? <ChevronLeftIcon size={14} />}
    </Button>
  );
};

export type MessageBranchNextProps = ComponentProps<typeof Button>;

export const MessageBranchNext = ({ children, ...props }: MessageBranchNextProps) => {
  const { goToNext, totalBranches } = useMessageBranch();

  return (
    <Button
      aria-label="Next branch"
      disabled={totalBranches <= 1}
      onClick={goToNext}
      size="icon-sm"
      type="button"
      variant="ghost"
      {...props}
    >
      {children ?? <ChevronRightIcon size={14} />}
    </Button>
  );
};

export type MessageBranchPageProps = HTMLAttributes<HTMLSpanElement>;

export const MessageBranchPage = ({ className, ...props }: MessageBranchPageProps) => {
  const { currentBranch, totalBranches } = useMessageBranch();

  return (
    <ButtonGroupText
      className={cn("border-none bg-transparent text-muted-foreground shadow-none", className)}
      {...props}
    >
      {currentBranch + 1} of {totalBranches}
    </ButtonGroupText>
  );
};

export type MessageResponseProps = Omit<StreamdownProps, "rehypePlugins"> & {
  /**
   * Hand file-path links to the `a` component override instead of letting the
   * harden pass turn them into app-origin navigations or " [blocked]" text.
   * Opt-in: only callers that supply that override may set it.
   */
  markFileLinks?: boolean;
  codeSnapshotContext?: Omit<CodeSnapshotOrigin, "codeBlockStartOffset" | "language"> | null;
};

const CodeSnapshotRenderContext = createContext<MessageResponseProps["codeSnapshotContext"]>(null);
const CodeSnapshotBlockStartContext = createContext<number | null>(null);

interface CodeSnapshotBlockRenderContextValue {
  blockComponent: ComponentType<StreamdownBlockProps>;
  blockStartOffsets: readonly number[];
}

const CodeSnapshotBlockRenderContext = createContext<CodeSnapshotBlockRenderContextValue | null>(
  null,
);

function DefaultStreamdownBlock(props: StreamdownBlockProps) {
  return <StreamdownBlock {...props} />;
}

function CodeSnapshotStreamdownBlock(props: StreamdownBlockProps) {
  const context = useContext(CodeSnapshotBlockRenderContext);
  const BlockComponent = context?.blockComponent ?? DefaultStreamdownBlock;
  const blockStartOffset = context?.blockStartOffsets[props.index] ?? null;

  return (
    <CodeSnapshotBlockStartContext.Provider value={blockStartOffset}>
      <BlockComponent {...props} />
    </CodeSnapshotBlockStartContext.Provider>
  );
}

function getMarkdownBlockStartOffsets(
  markdown: string,
  parseBlocks: (markdown: string) => string[],
): number[] {
  let nextOffset = 0;
  return parseBlocks(markdown).map((block) => {
    const foundOffset = markdown.indexOf(block, nextOffset);
    const blockStartOffset = foundOffset >= 0 ? foundOffset : nextOffset;
    nextOffset = blockStartOffset + block.length;
    return blockStartOffset;
  });
}

function getChatCodeControls(controls: StreamdownProps["controls"]): StreamdownProps["controls"] {
  if (typeof controls === "object" && controls !== null) {
    const codeControls = controls.code;
    return {
      ...controls,
      code: {
        ...(typeof codeControls === "object" && codeControls !== null ? codeControls : {}),
        copy: false,
        download: true,
      },
    };
  }

  return { code: { copy: false, download: true } };
}

function extractCodeText(children: ReactNode): string {
  if (typeof children === "string" || typeof children === "number") {
    return String(children);
  }

  if (Array.isArray(children)) {
    return children.map(extractCodeText).join("");
  }

  if (isValidElement(children)) {
    const props = children.props as { children?: ReactNode; code?: unknown };
    if (typeof props.code === "string") {
      return props.code;
    }
    return extractCodeText(props.children);
  }

  return "";
}

function extractCodeLanguage(children: ReactNode): string | null {
  if (Array.isArray(children)) {
    for (const child of children) {
      const language = extractCodeLanguage(child);
      if (language) return language;
    }
  }
  if (isValidElement(children)) {
    const props = children.props as {
      children?: ReactNode;
      className?: unknown;
      language?: unknown;
      "data-language"?: unknown;
    };
    if (typeof props.language === "string" && props.language) return props.language;
    if (typeof props["data-language"] === "string" && props["data-language"]) {
      return props["data-language"];
    }
    if (typeof props.className === "string") {
      const match = props.className.match(/(?:^|\s)language-([^\s]+)/);
      if (match?.[1]) return match[1];
    }
    return extractCodeLanguage(props.children);
  }
  return null;
}

// Shared visual style for the buttons overlaid on a chat code block (copy,
// wrap toggle). The frosted/ghost look matches the rest of the chat surface;
// positioning lives on the container in ChatCodeBlockPre, not here, so the
// buttons stay layout-agnostic.
const CODE_BLOCK_OVERLAY_BUTTON_CLASS =
  "size-8 bg-sidebar/80 text-muted-foreground hover:text-foreground supports-[backdrop-filter]:bg-sidebar/70 supports-[backdrop-filter]:backdrop-blur";

function ChatCodeBlockCopyButton({ getCode }: { getCode: () => string }) {
  const [isCopied, setIsCopied] = useState(false);
  const timeoutRef = useRef<number>(0);

  const handleClick = useCallback(() => {
    if (isCopied) return;

    try {
      const copyResult = copyText(getCode());
      void copyResult.then(
        () => {
          setIsCopied(true);
          timeoutRef.current = window.setTimeout(() => setIsCopied(false), 2000);
        },
        (error) => {
          console.warn("Failed to copy code block", error);
        },
      );
    } catch (error) {
      console.warn("Failed to copy code block", error);
    }
  }, [getCode, isCopied]);

  useEffect(
    () => () => {
      window.clearTimeout(timeoutRef.current);
    },
    [],
  );

  const Icon = isCopied ? CheckIcon : CopyIcon;

  return (
    <Button
      aria-label="Copy Code"
      className={CODE_BLOCK_OVERLAY_BUTTON_CLASS}
      onClick={handleClick}
      size="icon-sm"
      title="Copy Code"
      type="button"
      variant="ghost"
    >
      <Icon size={14} />
    </Button>
  );
}

function ChatCodeBlockWrapToggle({ wrap, onToggle }: { wrap: boolean; onToggle: () => void }) {
  return (
    <Button
      aria-label="Toggle word wrap"
      aria-pressed={wrap}
      // Brighten when active so the pressed state reads at a glance.
      className={cn(CODE_BLOCK_OVERLAY_BUTTON_CLASS, wrap && "text-foreground")}
      onClick={onToggle}
      size="icon-sm"
      title={wrap ? "Disable word wrap" : "Enable word wrap"}
      type="button"
      variant="ghost"
    >
      <WrapTextIcon size={14} />
    </Button>
  );
}

// Tapping a code block's own body opens its auto-generated code cards
// directly in the full-screen viewer, scoped to just this block, bypassing
// the grid gallery. This wraps only `block` (never the sibling toolbar
// overlay) because React bubbles synthetic events along the component tree,
// not the DOM tree — the toolbar's gallery dialog is rendered through a
// portal, so wrapping it here would also catch clicks inside that gallery.
// Distance (px) within which mousedown/mouseup are still considered the same
// point rather than a drag gesture.
const TAP_DRAG_THRESHOLD_PX = 4;

function ChatCodeBlockTapToOpen({ block }: { block: ReactNode }) {
  const { snapshots } = useCodeSnapshotBlock();
  const [viewerOpen, setViewerOpen] = useState(false);
  const downPointRef = useRef<{ x: number; y: number } | null>(null);

  const handlePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    downPointRef.current = { x: event.clientX, y: event.clientY };
  }, []);

  const handlePointerUp = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const downPoint = downPointRef.current;
      downPointRef.current = null;
      if (snapshots.length === 0 || !downPoint) return;

      const dx = event.clientX - downPoint.x;
      const dy = event.clientY - downPoint.y;
      const isDrag = Math.hypot(dx, dy) > TAP_DRAG_THRESHOLD_PX;
      if (isDrag) return;

      // A non-empty selection means the click was for selecting text, not
      // opening the viewer, even if mousedown/mouseup landed at one point
      // (e.g. a double-click that selected a word).
      const hasSelection = Boolean(window.getSelection()?.toString());
      if (hasSelection) return;

      setViewerOpen(true);
    },
    [snapshots.length],
  );

  return (
    <>
      <div onPointerDown={handlePointerDown} onPointerUp={handlePointerUp}>
        {block}
      </div>
      <AutoCardViewer open={viewerOpen} onOpenChange={setViewerOpen} />
    </>
  );
}

function ChatCodeBlockPre({ children, node }: ComponentProps<"pre"> & ExtraProps) {
  const snapshotContext = useContext(CodeSnapshotRenderContext);
  const streamdownBlockStartOffset = useContext(CodeSnapshotBlockStartContext);
  const localCodeBlockStartOffset = node?.position?.start.offset ?? null;
  const codeBlockStartOffset =
    streamdownBlockStartOffset !== null && localCodeBlockStartOffset !== null
      ? streamdownBlockStartOffset + localCodeBlockStartOffset
      : null;
  const wrapperRef = useRef<HTMLDivElement>(null);
  const code = extractCodeText(children);
  const language = extractCodeLanguage(children);
  const getCode = useCallback(() => code, [code]);
  // Soft-wrap long lines by default so users don't have to scroll horizontally
  // to read code blocks. The toggle restores Streamdown's native
  // horizontal-scroll view for when column alignment matters.
  const [wrap, setWrap] = useState(true);
  const toggleWrap = useCallback(() => setWrap((w) => !w), []);
  const block = isValidElement(children)
    ? cloneElement(children, { "data-block": "true" } as Record<string, unknown>)
    : children;

  const tapToOpenEnabled = Boolean(snapshotContext && codeBlockStartOffset !== null);
  const rendered = (
    <div className={cn("relative", wrap && "chat-code-wrap")}>
      {tapToOpenEnabled ? <ChatCodeBlockTapToOpen block={block} /> : block}
      {/* Overlay actions, anchored left of Streamdown's own download button
          (which sits at the header's right edge). A flex row lets the buttons
          self-arrange, so neither needs a hardcoded horizontal offset. */}
      <div className="absolute top-2 right-12 z-10 flex items-center gap-1">
        <ChatCodeBlockWrapToggle onToggle={toggleWrap} wrap={wrap} />
        <CodeFocusViewer
          initialWrap={wrap}
          renderedCode={block}
          snapshotsEnabled={Boolean(snapshotContext && codeBlockStartOffset !== null)}
        />
        {snapshotContext && codeBlockStartOffset !== null && (
          <CodeSnapshotToolbarControls
            className={CODE_BLOCK_OVERLAY_BUTTON_CLASS}
            getQuickCaptureTarget={() =>
              wrapperRef.current?.querySelector<HTMLElement>(
                '[data-streamdown="code-block-body"]',
              ) ?? null
            }
          />
        )}
        <ChatCodeBlockCopyButton getCode={getCode} />
      </div>
    </div>
  );

  if (!snapshotContext || codeBlockStartOffset === null) return rendered;
  const origin: CodeSnapshotOrigin = {
    ...snapshotContext,
    codeBlockStartOffset,
    language,
  };
  return (
    <CodeSnapshotBlockProvider origin={origin}>
      <CodeSnapshotDropZone>
        <div ref={wrapperRef}>{rendered}</div>
      </CodeSnapshotDropZone>
    </CodeSnapshotBlockProvider>
  );
}

export const MessageResponse = memo(
  ({
    className,
    components,
    controls,
    markFileLinks = false,
    codeSnapshotContext = null,
    children,
    BlockComponent = DefaultStreamdownBlock,
    parseMarkdownIntoBlocksFn = parseMarkdownIntoBlocks,
    ...props
  }: MessageResponseProps) => {
    const messageComponents = useMemo(
      () => ({ ...components, pre: ChatCodeBlockPre }),
      [components],
    );

    const messageControls = useMemo(() => getChatCodeControls(controls), [controls]);
    const blockStartOffsets = useMemo(
      () =>
        codeSnapshotContext && typeof children === "string"
          ? getMarkdownBlockStartOffsets(children, parseMarkdownIntoBlocksFn)
          : [],
      [children, codeSnapshotContext, parseMarkdownIntoBlocksFn],
    );
    const blockRenderContext = useMemo<CodeSnapshotBlockRenderContextValue>(
      () => ({ blockComponent: BlockComponent, blockStartOffsets }),
      [BlockComponent, blockStartOffsets],
    );

    return (
      <CodeSnapshotRenderContext.Provider value={codeSnapshotContext}>
        <CodeSnapshotBlockRenderContext.Provider value={blockRenderContext}>
          <Streamdown
            // wrap-anywhere is inherited, giving every prose descendant (including inline code) a break opportunity.
            className={cn(
              "size-full wrap-anywhere [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
              className,
            )}
            plugins={STREAMDOWN_PLUGINS}
            // Let links open on a plain click (and cmd/ctrl-click in a new tab)
            // instead of Streamdown's default "Open external link?" modal.
            linkSafety={CHAT_LINK_SAFETY}
            {...props}
            BlockComponent={CodeSnapshotStreamdownBlock}
            components={messageComponents}
            controls={messageControls}
            // Block remote image fetches that can exfiltrate data through URLs.
            rehypePlugins={
              markFileLinks ? FILE_LINK_STREAMDOWN_REHYPE_PLUGINS : SECURE_STREAMDOWN_REHYPE_PLUGINS
            }
          >
            {children}
          </Streamdown>
        </CodeSnapshotBlockRenderContext.Provider>
      </CodeSnapshotRenderContext.Provider>
    );
  },
);

MessageResponse.displayName = "MessageResponse";

export type MessageToolbarProps = ComponentProps<"div">;

export const MessageToolbar = ({ className, children, ...props }: MessageToolbarProps) => (
  <div className={cn("mt-4 flex w-full items-center justify-between gap-4", className)} {...props}>
    {children}
  </div>
);
