import { type CSSProperties, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getEmbedRoot } from "@/lib/host";
import { onResponseEffectArrival, type ResponseEffectArrival } from "@/lib/responseSignals";

const HELP_WORDS = Array.from({ length: 42 }, (_, index) => index);

/** A short, receiver-only full-screen effect for an urgent human request. */
export function InterviewHelpEffect() {
  const [arrival, setArrival] = useState<ResponseEffectArrival | null>(null);
  const clearTimer = useRef<number | null>(null);

  useEffect(
    () =>
      onResponseEffectArrival((next) => {
        setArrival(next);
        if (clearTimer.current !== null) window.clearTimeout(clearTimer.current);
        clearTimer.current = window.setTimeout(() => {
          setArrival(null);
          clearTimer.current = null;
        }, 2_000);
      }),
    [],
  );

  useEffect(
    () => () => {
      if (clearTimer.current !== null) window.clearTimeout(clearTimer.current);
    },
    [],
  );

  if (!arrival) return null;
  const screenshot = arrival.effectType === "screenshot";
  const effectText = screenshot ? "SCREENSHOT PLS" : "HELP";

  const overlay = (
    <div
      key={arrival.requestId}
      className="interview-help-effect pointer-events-none fixed inset-0 z-[310] overflow-hidden"
      role="alert"
      aria-live="assertive"
      aria-label={
        screenshot
          ? "Screenshot requested by another participant"
          : "Help requested by another participant"
      }
      data-effect={arrival.effectType}
      data-testid="interview-help-effect"
    >
      <div className="interview-help-effect-grid" aria-hidden="true">
        {HELP_WORDS.map((index) => (
          <span
            key={index}
            className="interview-help-effect-word"
            style={
              {
                "--help-delay": `${(index % 7) * 45}ms`,
                "--help-rotate": `${((index * 17) % 13) - 6}deg`,
                "--help-scale": `${0.78 + (index % 5) * 0.1}`,
              } as CSSProperties
            }
          >
            {effectText}
          </span>
        ))}
      </div>
    </div>
  );
  return createPortal(overlay, getEmbedRoot() ?? document.body);
}
