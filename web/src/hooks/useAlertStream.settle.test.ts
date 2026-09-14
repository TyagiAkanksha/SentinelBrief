// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CACHE_SETTLE_MS, VERDICT_CREATED_EVENT, useAlertStream } from "@/hooks/useAlertStream";
import type { EventSourceLike, UseAlertStreamOptions } from "@/hooks/useAlertStream";

/**
 * Pins the I1 fix (`.superpowers/sdd/m8-polish/m8a-final-review.md` §4 I1, ruling R28): the API
 * caches the alert list for `ALERTS_LIST_CACHE_TTL_S` (15 s), so a `router.refresh()` fired the
 * instant a `verdict.created` event arrives can read a stale cache hit and leave the list
 * unchanged. `CACHE_SETTLE_MS` (16 s = the TTL + 1 s) is one trailing, coalescing refresh that
 * fires after the cache has turned over — it never bypasses the cache from the page (PRD §8,
 * §10.1); it only asks once more, later.
 *
 * `FakeEventSource` and the fake-timer pattern are copied from the pinned
 * `useAlertStream.test.ts` (that file is not modified for this task).
 */

afterEach(() => {
  vi.useRealTimers();
});

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

describe("useAlertStream settle refresh (I1)", () => {
  it("exports CACHE_SETTLE_MS as 16 000 ms (ALERTS_LIST_CACHE_TTL_S + 1 s)", () => {
    expect(CACHE_SETTLE_MS).toBe(16_000);
  });

  it("calls onUpdate once immediately and once more after the cache has turned over", () => {
    vi.useFakeTimers();
    const { source, onUpdate } = setup({ settleDelayMs: 16_000 });

    act(() => {
      source.emit(VERDICT_CREATED_EVENT);
    });
    expect(onUpdate).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(15_999);
    });
    expect(onUpdate).toHaveBeenCalledTimes(1); // none in between

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onUpdate).toHaveBeenCalledTimes(2);
  });

  it("uses the default CACHE_SETTLE_MS when settleDelayMs is not passed", () => {
    vi.useFakeTimers();
    const { source, onUpdate } = setup();

    act(() => {
      source.emit(VERDICT_CREATED_EVENT);
    });
    expect(onUpdate).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(CACHE_SETTLE_MS);
    });
    expect(onUpdate).toHaveBeenCalledTimes(2);
  });

  it("collapses three events within 2 s into one trailing settle call after the LAST event's 16 s", () => {
    vi.useFakeTimers();
    const { source, onUpdate } = setup({ settleDelayMs: 16_000 });

    act(() => {
      source.emit(VERDICT_CREATED_EVENT);
    });
    act(() => {
      vi.advanceTimersByTime(1_000);
      source.emit(VERDICT_CREATED_EVENT);
    });
    act(() => {
      vi.advanceTimersByTime(1_000);
      source.emit(VERDICT_CREATED_EVENT);
    });
    // One immediate onUpdate call per event.
    expect(onUpdate).toHaveBeenCalledTimes(3);

    act(() => {
      vi.advanceTimersByTime(15_999);
    });
    expect(onUpdate).toHaveBeenCalledTimes(3); // the settle timer restarts on every event

    act(() => {
      vi.advanceTimersByTime(1);
    });
    // Exactly one settle call, 16 s after the LAST event, not one per event.
    expect(onUpdate).toHaveBeenCalledTimes(4);
  });

  it("clears the pending settle timer on unmount: no further calls across 60 s", () => {
    vi.useFakeTimers();
    const { source, onUpdate, unmount } = setup({ settleDelayMs: 16_000 });

    act(() => {
      source.emit(VERDICT_CREATED_EVENT);
    });
    expect(onUpdate).toHaveBeenCalledTimes(1);

    unmount();

    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(onUpdate).toHaveBeenCalledTimes(1);
  });
});
