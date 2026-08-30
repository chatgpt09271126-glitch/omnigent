import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InterviewAskAgent } from "./ChatPage";

afterEach(cleanup);

describe("InterviewAskAgent", () => {
  it("expands from one compact control, sends, and collapses again", async () => {
    const onSend = vi.fn();
    render(
      <InterviewAskAgent
        disabled={false}
        permissionLevel={2}
        readOnlyReason={null}
        onSend={onSend}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Ask agent" }));
    const textarea = await screen.findByRole("textbox", { name: "Prompt for agent" });
    fireEvent.change(textarea, { target: { value: "Please verify the last answer." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(onSend).toHaveBeenCalledWith("Please verify the last answer.");
    expect(screen.queryByTestId("interview-ask-sheet")).toBeNull();
    expect(screen.getByTestId("interview-ask-control")).toBeInTheDocument();
  });

  it("keeps the prompt path unavailable to a read-only viewer", () => {
    render(
      <InterviewAskAgent
        disabled={false}
        permissionLevel={1}
        readOnlyReason={null}
        onSend={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Ask agent" })).toBeDisabled();
  });
});
