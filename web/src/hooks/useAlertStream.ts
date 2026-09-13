import { useEffect, useRef, useState } from "react";

// The SSE `event:` name the api's `verdict_event_stream` publishes (api/routes/stream.py).
export const VERDICT_CREATED_EVENT = "verdict.created";

// PRD §9: "falls back to 30 s polling".
export const POLL_INTERVAL_MS = 30_000;

export type StreamStatus = "connecting" | "live" | "polling";

export type EventSourceLike = {
  addEventListener(type: string, listener: (event: Event) => void): void;
  close(): void;
};

export type UseAlertStreamOptions = {
  url: string;
  onUpdate: () => void;
  pollIntervalMs?: number;
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
 * `onUpdate` and `createEventSource` are held in refs, refreshed in their own no-dependency
 * effects on every render, so a new callback identity never tears down and reopens the
 * `EventSource` — only `url`/`pollIntervalMs` changing does (the subscribing effect's own
 * dependency array).
 *
 * Per the EventSource specification, a non-200 response (e.g. a 429 or 503 from `StreamGate`/
 * `StreamUnavailableError`) fails the connection permanently rather than retrying — no
 * behaviour change here, but it means a tab rejected at open time drops to polling every
 * `pollIntervalMs` and stays there until the page is reloaded (ruling R18).
 */
export function useAlertStream(options: UseAlertStreamOptions): AlertStreamState {
  const { url, pollIntervalMs = POLL_INTERVAL_MS } = options;
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

    const stopPolling = () => {
      if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    source.addEventListener("open", () => {
      setStatus("live");
      stopPolling();
    });

    source.addEventListener(VERDICT_CREATED_EVENT, () => {
      setUpdates((count) => count + 1);
      onUpdateRef.current();
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
    };
  }, [url, pollIntervalMs]);

  return { status, updates };
}
