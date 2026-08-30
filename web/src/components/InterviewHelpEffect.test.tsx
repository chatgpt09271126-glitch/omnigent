import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { emitResponseEffectArrival } from "@/lib/responseSignals";
import { InterviewHelpEffect } from "./InterviewHelpEffect";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("InterviewHelpEffect", () => {
  it("fills the receiver viewport for two seconds and then clears", () => {
    vi.useFakeTimers();
    render(<InterviewHelpEffect />);

    act(() => {
      emitResponseEffectArrival({
        effectType: "help",
        conversationId: "conv_a",
        responseId: "resp_a",
        requestId: "help_1",
        requestedBy: "mobile@example.com",
        requestedAt: 123,
      });
    });

    const effect = screen.getByTestId("interview-help-effect");
    expect(effect).toHaveAttribute("aria-label", "Help requested by another participant");
    expect(effect.querySelectorAll(".interview-help-effect-word")).toHaveLength(42);

    act(() => vi.advanceTimersByTime(1_999));
    expect(screen.getByTestId("interview-help-effect")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByTestId("interview-help-effect")).toBeNull();
  });

  it("uses a distinct readability message for screenshot requests", () => {
    render(<InterviewHelpEffect />);

    act(() => {
      emitResponseEffectArrival({
        effectType: "screenshot",
        conversationId: "conv_a",
        responseId: "resp_a",
        requestId: "screenshot_1",
        requestedBy: "mobile@example.com",
        requestedAt: 124,
      });
    });

    const effect = screen.getByTestId("interview-help-effect");
    expect(effect).toHaveAttribute("aria-label", "Screenshot requested by another participant");
    expect(effect).toHaveAttribute("data-effect", "screenshot");
    expect(screen.getAllByText("SCREENSHOT PLS")).toHaveLength(42);
  });
});
