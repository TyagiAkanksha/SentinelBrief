// The alert stream's shared vocabulary, kept below the hook layer so a dumb component can read
// it without importing a hook (review t01 N-M2).

// PRD §9: "falls back to 30 s polling".
export const POLL_INTERVAL_MS = 30_000;

// The API caches the alert list for ALERTS_LIST_CACHE_TTL_S (15 s); one trailing refresh after
// the cache has turned over makes the list catch up with the counter — the cache is never
// bypassed from the page (PRD §8, §10.1).
export const CACHE_SETTLE_MS = 16_000;

export type StreamStatus = "connecting" | "live" | "polling";
