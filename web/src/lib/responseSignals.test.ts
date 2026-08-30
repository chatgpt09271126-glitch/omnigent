import { describe, expect, it } from "vitest";
import { responseStateAfterMutation, type ResponseSignalInfo } from "./responseSignals";

function signal(signalType: ResponseSignalInfo["signalType"], signaledAt: number) {
  return { signalType, signaledBy: "participant@example.com", signaledAt };
}

describe("responseStateAfterMutation", () => {
  it("replaces Bad and Good without disturbing independent signals", () => {
    const current = {
      bad: signal("bad", 1),
      attention: signal("attention", 2),
      shorter: signal("shorter", 3),
    };

    const next = responseStateAfterMutation(current, "good", true, signal("good", 4));

    expect(next).toEqual({
      good: signal("good", 4),
      attention: signal("attention", 2),
      shorter: signal("shorter", 3),
    });
  });

  it("replaces Shorter and More detail and clears only the active request", () => {
    const replaced = responseStateAfterMutation(
      { good: signal("good", 1), shorter: signal("shorter", 2) },
      "more_detail",
      true,
      signal("more_detail", 3),
    );
    expect(replaced).toEqual({
      good: signal("good", 1),
      more_detail: signal("more_detail", 3),
    });

    expect(
      responseStateAfterMutation(replaced, "more_detail", false, signal("more_detail", 4)),
    ).toEqual({ good: signal("good", 1) });
  });
});
