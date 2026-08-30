import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { emitResponseSignalArrival } from "@/lib/responseSignals";
import { useChatStore } from "@/store/chatStore";
import { InterviewAttentionNotice } from "./InterviewAttentionNotice";

const realLoadMoreHistory = useChatStore.getState().loadMoreHistory;

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  useChatStore.setState({
    conversationId: null,
    hasMoreHistory: false,
    loadingMoreHistory: false,
    loadMoreHistory: realLoadMoreHistory,
  });
});

describe("InterviewAttentionNotice", () => {
  it("shows remote Attention without reacting to a local optimistic signal", () => {
    render(
      <MemoryRouter>
        <InterviewAttentionNotice conversationId="conv_a" />
      </MemoryRouter>,
    );

    act(() => {
      emitResponseSignalArrival({
        conversationId: "conv_a",
        responseId: "resp_1",
        signalType: "attention",
        active: true,
        source: "local",
      });
    });
    expect(screen.queryByTestId("interview-attention-notice")).toBeNull();

    act(() => {
      emitResponseSignalArrival({
        conversationId: "conv_a",
        responseId: "resp_1",
        signalType: "attention",
        active: true,
        source: "remote",
      });
    });
    expect(screen.getByTestId("interview-attention-notice")).toHaveTextContent(
      "Attention requested",
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss attention notice" }));
    expect(screen.queryByTestId("interview-attention-notice")).toBeNull();
  });

  it("pages older history while seeking the targeted response", () => {
    vi.useFakeTimers();
    vi.stubGlobal("CSS", { escape: (value: string) => value });
    const loadMoreHistory = vi.fn().mockResolvedValue(undefined);
    useChatStore.setState({
      conversationId: "conv_a",
      hasMoreHistory: true,
      loadingMoreHistory: false,
      loadMoreHistory,
    });
    render(
      <MemoryRouter>
        <InterviewAttentionNotice conversationId="conv_a" />
      </MemoryRouter>,
    );
    act(() => {
      emitResponseSignalArrival({
        conversationId: "conv_a",
        responseId: "resp_older",
        signalType: "attention",
        active: true,
        source: "remote",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "Go to response requesting attention" }));

    expect(loadMoreHistory).toHaveBeenCalledOnce();
  });

  it("scrolls and focuses the targeted response immediately", () => {
    const target = document.createElement("article");
    target.dataset.responseId = "resp_visible";
    target.tabIndex = -1;
    const responseScrollIntoView = vi.fn();
    Object.defineProperty(target, "scrollIntoView", {
      configurable: true,
      value: responseScrollIntoView,
    });
    const attentionTarget = document.createElement("span");
    attentionTarget.dataset.responseAttentionTarget = "resp_visible";
    attentionTarget.tabIndex = -1;
    const attentionScrollIntoView = vi.fn();
    Object.defineProperty(attentionTarget, "scrollIntoView", {
      configurable: true,
      value: attentionScrollIntoView,
    });
    const focus = vi.spyOn(attentionTarget, "focus");
    target.append(attentionTarget);
    document.body.append(target);
    render(
      <MemoryRouter>
        <InterviewAttentionNotice conversationId="conv_a" />
      </MemoryRouter>,
    );
    act(() => {
      emitResponseSignalArrival({
        conversationId: "conv_a",
        responseId: "resp_visible",
        signalType: "attention",
        active: true,
        source: "remote",
        signaledAt: 300,
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "Go to response requesting attention" }));

    expect(attentionScrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "center" });
    expect(responseScrollIntoView).not.toHaveBeenCalled();
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
    target.remove();
  });

  it("keeps Attention requested available in the normal thread until click or dismiss", () => {
    vi.useFakeTimers();
    const target = document.createElement("article");
    target.dataset.responseId = "resp_visible";
    Object.defineProperty(target, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ top: 100, bottom: 300 }),
    });
    document.body.append(target);
    render(
      <MemoryRouter>
        <InterviewAttentionNotice conversationId="conv_a" />
      </MemoryRouter>,
    );

    act(() => {
      emitResponseSignalArrival({
        conversationId: "conv_a",
        responseId: "resp_visible",
        signalType: "attention",
        active: true,
        source: "remote",
        signaledAt: 350,
      });
    });
    act(() => vi.advanceTimersByTime(5_000));

    expect(screen.getByTestId("interview-attention-notice")).toHaveTextContent(
      "Attention requested",
    );
    expect(
      screen.getByRole("button", { name: "Go to response requesting attention" }),
    ).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss attention notice" }));
    expect(screen.queryByTestId("interview-attention-notice")).toBeNull();
    target.remove();
  });

  it("keeps a dismissed delivery closed if the same event is delivered twice", () => {
    render(
      <MemoryRouter>
        <InterviewAttentionNotice conversationId="conv_a" />
      </MemoryRouter>,
    );
    const arrival = {
      conversationId: "conv_a",
      responseId: "resp_duplicate",
      signalType: "attention" as const,
      active: true,
      source: "remote" as const,
      signaledAt: 400,
    };
    act(() => emitResponseSignalArrival(arrival));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss attention notice" }));
    act(() => emitResponseSignalArrival(arrival));

    expect(screen.queryByTestId("interview-attention-notice")).toBeNull();
  });

  it("notifies an operator about Shorter and links to the exact response", () => {
    const target = document.createElement("article");
    target.dataset.responseId = "resp_shorter";
    target.tabIndex = -1;
    const scrollIntoView = vi.spyOn(target, "scrollIntoView");
    document.body.append(target);
    render(
      <MemoryRouter>
        <InterviewAttentionNotice conversationId="conv_a" showAttention={false} />
      </MemoryRouter>,
    );

    act(() => {
      emitResponseSignalArrival({
        conversationId: "conv_a",
        responseId: "resp_shorter",
        signalType: "attention",
        active: true,
        source: "remote",
        signaledAt: 500,
      });
    });
    expect(screen.queryByTestId("interview-attention-notice")).toBeNull();

    act(() => {
      emitResponseSignalArrival({
        conversationId: "conv_a",
        responseId: "resp_shorter",
        signalType: "shorter",
        active: true,
        source: "remote",
        signaledAt: 501,
      });
    });
    expect(screen.getByTestId("interview-attention-notice")).toHaveTextContent("Shorter requested");
    fireEvent.click(screen.getByRole("button", { name: "Go to response requesting Shorter" }));
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "center" });
    target.remove();
  });

  it("presents detail requests prominently, compacts them, then removes them", () => {
    vi.useFakeTimers();
    render(
      <MemoryRouter>
        <InterviewAttentionNotice conversationId="conv_a" showAttention={false} />
      </MemoryRouter>,
    );
    act(() => {
      emitResponseSignalArrival({
        conversationId: "conv_a",
        responseId: "resp_detail",
        signalType: "more_detail",
        active: true,
        source: "remote",
        signaledAt: 600,
      });
    });

    expect(screen.getByTestId("interview-attention-notice")).toHaveClass("response-request-notice");
    expect(screen.getByText("More detail requested")).toHaveClass("text-lg");

    act(() => vi.advanceTimersByTime(1_400));
    expect(screen.getByTestId("interview-attention-notice")).not.toHaveClass(
      "response-request-notice",
    );
    expect(screen.getByText("More detail")).toHaveClass("text-sm");

    act(() => vi.advanceTimersByTime(1_200));
    expect(screen.queryByTestId("interview-attention-notice")).toBeNull();
  });
});
