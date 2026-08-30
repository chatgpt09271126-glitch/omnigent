export type ResponseSignalType = "bad" | "good" | "attention" | "shorter" | "more_detail";

export interface ResponseSignalInfo {
  signalType: ResponseSignalType;
  signaledBy: string | null;
  signaledAt: number;
}

export type ResponseSignalState = Partial<Record<ResponseSignalType, ResponseSignalInfo>>;
export type ResponseSignalsByResponse = Record<string, ResponseSignalState>;

export interface ResponseSignalArrival {
  conversationId: string;
  responseId: string;
  signalType: ResponseSignalType;
  active: boolean;
  source: "local" | "remote";
  /** Server mutation time; lets transient arrival UI ignore duplicate delivery. */
  signaledAt?: number;
}

export type ResponseEffectType = "help" | "screenshot";

export interface ResponseEffectArrival {
  effectType: ResponseEffectType;
  conversationId: string;
  responseId: string;
  requestId: string;
  requestedBy: string | null;
  requestedAt: number;
}

const arrivalListeners = new Set<(arrival: ResponseSignalArrival) => void>();
const navigationListeners = new Set<(arrival: ResponseSignalArrival) => void>();
const effectListeners = new Set<(arrival: ResponseEffectArrival) => void>();
const localEffectRequestIds = new Set<string>();

export function emitResponseSignalArrival(arrival: ResponseSignalArrival): void {
  for (const listener of arrivalListeners) listener(arrival);
}

export function onResponseSignalArrival(
  listener: (arrival: ResponseSignalArrival) => void,
): () => void {
  arrivalListeners.add(listener);
  return () => arrivalListeners.delete(listener);
}

/** Explicit user navigation request; overlays may close only for this action. */
export function emitResponseSignalNavigation(arrival: ResponseSignalArrival): void {
  for (const listener of navigationListeners) listener(arrival);
}

export function onResponseSignalNavigation(
  listener: (arrival: ResponseSignalArrival) => void,
): () => void {
  navigationListeners.add(listener);
  return () => navigationListeners.delete(listener);
}

export function emitResponseEffectArrival(arrival: ResponseEffectArrival): void {
  for (const listener of effectListeners) listener(arrival);
}

export function onResponseEffectArrival(
  listener: (arrival: ResponseEffectArrival) => void,
): () => void {
  effectListeners.add(listener);
  return () => effectListeners.delete(listener);
}

/** Suppress the server echo on the tab that initiated a transient effect. */
export function markLocalEffectRequest(requestId: string): void {
  localEffectRequestIds.add(requestId);
  if (localEffectRequestIds.size > 128) {
    const oldest = localEffectRequestIds.values().next();
    if (!oldest.done) localEffectRequestIds.delete(oldest.value);
  }
}

export function forgetLocalEffectRequest(requestId: string): void {
  localEffectRequestIds.delete(requestId);
}

export function consumeLocalEffectRequest(requestId: string): boolean {
  return localEffectRequestIds.delete(requestId);
}

export function isResponseSignalType(value: unknown): value is ResponseSignalType {
  return (
    value === "bad" ||
    value === "good" ||
    value === "attention" ||
    value === "shorter" ||
    value === "more_detail"
  );
}

export function responseStateAfterMutation(
  current: ResponseSignalState,
  signalType: ResponseSignalType,
  active: boolean,
  info: ResponseSignalInfo,
): ResponseSignalState {
  const next = { ...current };
  if (!active) {
    return Object.fromEntries(
      Object.entries(next).filter(([name]) => name !== signalType),
    ) as ResponseSignalState;
  }
  if (signalType === "bad") delete next.good;
  if (signalType === "good") delete next.bad;
  if (signalType === "shorter") delete next.more_detail;
  if (signalType === "more_detail") delete next.shorter;
  next[signalType] = info;
  return next;
}
