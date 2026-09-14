import { useEffect, useRef, useState } from "react";

import { CACHE_SETTLE_MS, POLL_INTERVAL_MS } from "@/lib/stream";
import type { StreamStatus } from "@/lib/stream";

// The SSE `event:` name the api's `verdict_event_stream` publishes (api/routes/stream.py).
export const VERDICT_CREATED_EVENT = "verdict.created";

// Re-exported so this hook stays the one import surface for its own callers and tests; the
// definitions live in `@/lib/stream`, which components may import directly (review t01 N-M2).
export { CACHE_SETTLE_MS, POLL_INTERVAL_MS };
export type { StreamStatus };

export type EventSourceLike = {
  addEventListener(type: string, listener: (event: Event) => void): void;
  close(): void;
};

export type UseAlertStreamOptions = {
  url: string;
  onUpdate: () => void;
  pollIntervalMs?: number;
  settleDelayMs?: number;
  createEventSource?: (url: string) => EventSourceLike;
};

export type AlertStreamState = { status: StreamStatus; updates: number };

function defaultCreateEventSource(url: string): EventSourceLike {
  return new EventSource(url);
}

/**
 * Subscribe to `url` via `EventSource` (or `createEventSource`'s double in tests), calling
 * `onUpdate` on every `verdict.created` event and falling back to polling `onUpdate` every
 * `pollIntervalMs` while the stream is erroring.
 *
 * `event.data` is NEVER read: the SSE payload is a hint, never the rendered data (m5 task-04
 * review M4) — the page re-reads the database through `onUpdate` (`router.refresh()`), so no
 * attacker-influenced text from the channel is ever rendered from this path (PRD §10.6).
 *
 * Every event also (re)starts one coalescing `settleDelayMs` timer that calls `onUpdate` once
 * more after the API's list cache has turned over (`CACHE_SETTLE_MS`, review I1) — the immediate
 * refresh can otherwise read a cache hit and leave the list unchanged.
 *
 * `onUpdate` and `createEventSource` are held in refs, refreshed in their own no-dependency
 * effects on every render, so a new callback identity never tears down and reopens the
 * `EventSource` — only `url`/`pollIntervalMs`/`settleDelayMs` changing does (the subscribing
 * effect's own dependency array); the settle timer fires through the same `onUpdate` ref.
 *
 * Per the EventSource specification, a non-200 response (e.g. a 429 or 503 from `StreamGate`/
 * `StreamUnavailableError`) fails the connection permanently rather than retrying — no
 * behaviour change here, but it means a tab rejected at open time drops to polling every
 * `pollIntervalMs` and stays there until the page is reloaded (ruling R18).
 */
export function useAlertStream(options: UseAlertStreamOptions): AlertStreamState {
  const { url, pollIntervalMs = POLL_INTERVAL_MS, settleDelayMs = CACHE_SETTLE_MS } = options;
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [updates, setUpdates] = useState(0);

  const onUpdateRef = useRef(options.onUpdate);
  useEffect(() => {
    onUpdateRef.current = options.onUpdate;
  });

  const createEventSourceRef = useRef(options.createEventSource ?? defaultCreateEventSource);
  useEffect(() => {
    createEventSourceRef.current = options.createEventSource ?? defaultCreateEventSource;
  });

  useEffect(() => {
    const source = createEventSourceRef.current(url);
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let settleTimer: ReturnType<typeof setTimeout> | null = null;

    const stopPolling = () => {
      if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const stopSettle = () => {
      if (settleTimer !== null) {
        clearTimeout(settleTimer);
        settleTimer = null;
      }
    };

    source.addEventListener("open", () => {
      setStatus("live");
      stopPolling();
    });

    source.addEventListener(VERDICT_CREATED_EVENT, () => {
      setUpdates((count) => count + 1);
      onUpdateRef.current();
      // One coalescing trailing refresh: restarted by every event, so a burst settles into a
      // single extra read once the list cache has turned over.
      stopSettle();
      settleTimer = setTimeout(() => {
        onUpdateRef.current();
      }, settleDelayMs);
    });

    source.addEventListener("error", () => {
      setStatus("polling");
      if (pollTimer === null) {
        pollTimer = setInterval(() => {
          onUpdateRef.current();
        }, pollIntervalMs);
      }
    });

    return () => {
      source.close();
      stopPolling();
      stopSettle();
    };
  }, [url, pollIntervalMs, settleDelayMs]);

  return { status, updates };
}
