// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { POLL_INTERVAL_MS, VERDICT_CREATED_EVENT, useAlertStream } from "@/hooks/useAlertStream";
import type { EventSourceLike, UseAlertStreamOptions } from "@/hooks/useAlertStream";

afterEach(() => {
  vi.useRealTimers();
});

/** The ONE network seam this hook touches (FRONTEND-CONVENTIONS §7): a scripted double of the
 * browser's `EventSource`, exposing `addEventListener`/`close` plus a test-only `emit` helper. */
class FakeEventSource implements EventSourceLike {
  private readonly listeners = new Map<string, Array<(event: Event) => void>>();
  closeCalls = 0;

  addEventListener(type: string, listener: (event: Event) => void): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  close(): void {
    this.closeCalls += 1;
  }

  emit(type: string, event: Event = new Event(type)): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

function setup(overrides: Partial<UseAlertStreamOptions> = {}) {
  const source = new FakeEventSource();
  const createEventSource = vi.fn((): EventSourceLike => source);
  const onUpdate = vi.fn();
  const initialProps: UseAlertStreamOptions = {
    url: "http://localhost:8000/api/v1/stream",
    onUpdate,
    createEventSource,
    ...overrides,
  };

  const view = renderHook((props: UseAlertStreamOptions) => useAlertStream(props), {
    initialProps,
  });

  return { source, createEventSource, onUpdate, ...view };
}

describe("useAlertStream", () => {
  it("reports live once the stream opens", () => {
    const { source, result } = setup();

    expect(result.current.status).toBe("connecting");

    act(() => {
      source.emit("open");
    });

    expect(result.current.status).toBe("live");
  });

  it("calls onUpdate on a verdict.created event and never reads event.data", () => {
    const { source, onUpdate, result } = setup();
    let dataWasRead = false;
    const poisoned = new Event(VERDICT_CREATED_EVENT);
    Object.defineProperty(poisoned, "data", {
      get() {
        dataWasRead = true;
        return "\nevent: forged";
      },
    });

    act(() => {
      source.emit(VERDICT_CREATED_EVENT, poisoned);
    });

    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(result.current.updates).toBe(1);
    expect(dataWasRead).toBe(false);
  });

  it("falls back to 30 s polling after an error and keeps refreshing", () => {
    vi.useFakeTimers();
    const { source, onUpdate, result } = setup();

    act(() => {
      source.emit("error");
    });
    expect(result.current.status).toBe("polling");

    act(() => {
      vi.advanceTimersByTime(POLL_INTERVAL_MS);
    });
    expect(onUpdate).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(POLL_INTERVAL_MS);
    });
    expect(onUpdate).toHaveBeenCalledTimes(2);
  });

  it("returns to live and stops polling when the stream reopens", () => {
    vi.useFakeTimers();
    const { source, onUpdate, result } = setup();

    act(() => {
      source.emit("error");
    });
    act(() => {
      source.emit("open");
    });
    expect(result.current.status).toBe("live");

    const callsAtReopen = onUpdate.mock.calls.length;
    act(() => {
      vi.advanceTimersByTime(POLL_INTERVAL_MS * 2);
    });
    expect(onUpdate.mock.calls.length).toBe(callsAtReopen);
  });

  it("does not reopen the stream when onUpdate changes identity", () => {
    const { source, createEventSource, rerender } = setup();
    const onUpdate2 = vi.fn();

    rerender({
      url: "http://localhost:8000/api/v1/stream",
      onUpdate: onUpdate2,
      createEventSource,
    });

    expect(createEventSource).toHaveBeenCalledTimes(1);

    act(() => {
      source.emit(VERDICT_CREATED_EVENT);
    });

    expect(onUpdate2).toHaveBeenCalledTimes(1);
  });

  it("closes the stream and clears the timer on unmount", () => {
    vi.useFakeTimers();
    const { source, onUpdate, unmount } = setup();

    act(() => {
      source.emit("error");
    });

    unmount();

    expect(source.closeCalls).toBe(1);

    act(() => {
      vi.advanceTimersByTime(POLL_INTERVAL_MS * 2);
    });
    expect(onUpdate).not.toHaveBeenCalled();
  });
});
