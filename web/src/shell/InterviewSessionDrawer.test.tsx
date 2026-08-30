import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type * as UseConversationsModule from "@/hooks/useConversations";
import { InterviewSessionDrawer } from "./InterviewSessionDrawer";

vi.mock("@/hooks/useConversations", async (importOriginal) => ({
  ...(await importOriginal<typeof UseConversationsModule>()),
  useConversations: () => ({
    isLoading: false,
    data: {
      pages: [
        {
          data: [
            { id: "conv_a", title: "Current interview", labels: {} },
            { id: "conv_b", title: "Earlier session", labels: {} },
          ],
        },
      ],
    },
  }),
}));

afterEach(cleanup);

describe("InterviewSessionDrawer", () => {
  it("makes the closed drawer inert without relying on a desktop breakpoint", () => {
    render(
      <MemoryRouter initialEntries={["/c/conv_a"]}>
        <Routes>
          <Route
            path="/c/:conversationId"
            element={<InterviewSessionDrawer open={false} onClose={vi.fn()} />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("interview-session-drawer")).toHaveAttribute("inert");
    expect(screen.getByTestId("interview-session-drawer")).toHaveAttribute("aria-hidden", "true");
  });

  it("shows only scrollable session navigation and closes on selection", () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter initialEntries={["/c/conv_a"]}>
        <Routes>
          <Route
            path="/c/:conversationId"
            element={<InterviewSessionDrawer open onClose={onClose} />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("navigation", { name: "Session list" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Current interview" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.queryByText("Files")).toBeNull();
    expect(screen.queryByText("Shells")).toBeNull();

    fireEvent.click(screen.getByRole("link", { name: "Earlier session" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
