import { describe, expect, it } from "vitest";

import {
  categoryRows,
  escalationRate,
  humanizeCategory,
  severityRows,
  triagedCount,
  volumeRows,
} from "@/lib/stats";
import type { StatsOut } from "@/types/api";

function makeStats(overrides: Partial<StatsOut> = {}): StatsOut {
  return {
    total_alerts: 0,
    by_status: { pending: 0, triaged: 0, failed: 0 },
    by_severity: { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 },
    by_category: {
      scanning: 0,
      brute_force: 0,
      successful_intrusion: 0,
      malware_delivery: 0,
      persistence_attempt: 0,
      reconnaissance: 0,
      other: 0,
    },
    escalated_count: 0,
    volume_by_day: [],
    cost_total_usd: "0",
    cost_mean_usd: "0.000000",
    latency_p50_ms: 0,
    latency_p95_ms: 0,
    last_alert_at: null,
    // `cost_by_day` is added to `StatsOut` by this same task (Interfaces, "Produces") — included
    // here so this fixture stays valid once the generated schema requires it.
    cost_by_day: [],
    // m8b task-05 (pinned-file conflict, implementer-report-flagged): the daily token-budget
    // circuit breaker's read surface, non-optional on the generated `StatsOut` type
    // (`openapi-typescript`'s `defaultNonNullable`) — included so this fixture stays valid.
    budget_exhausted: false,
    tokens_today: 0,
    daily_token_budget: 0,
    ...overrides,
  };
}

describe("humanizeCategory", () => {
  it("humanizes every VerdictCategory key", () => {
    expect(humanizeCategory("scanning")).toBe("Scanning");
    expect(humanizeCategory("brute_force")).toBe("Brute force");
    expect(humanizeCategory("successful_intrusion")).toBe("Successful intrusion");
    expect(humanizeCategory("malware_delivery")).toBe("Malware delivery");
    expect(humanizeCategory("persistence_attempt")).toBe("Persistence attempt");
    expect(humanizeCategory("reconnaissance")).toBe("Reconnaissance");
    expect(humanizeCategory("other")).toBe("Other");
    // An unknown key still passes through `_` -> space + an initial capital.
    expect(humanizeCategory("weird_thing")).toBe("Weird thing");
  });
});

describe("triagedCount / escalationRate", () => {
  it("computes the escalation rate over triaged alerts and returns 0 with none", () => {
    const stats = makeStats({
      by_status: { pending: 0, triaged: 12, failed: 0 },
      escalated_count: 3,
    });

    expect(triagedCount(stats)).toBe(12);
    expect(escalationRate(stats)).toBe(0.25);

    // `by_status` without a `triaged` key at all (the `noUncheckedIndexedAccess` branch) — never
    // a division by zero.
    const noTriaged = makeStats({
      by_status: { pending: 5, failed: 0 },
      escalated_count: 0,
    });

    expect(triagedCount(noTriaged)).toBe(0);
    expect(escalationRate(noTriaged)).toBe(0);
  });
});

describe("severityRows", () => {
  it("always returns five severity rows in ascending order, zero-filling gaps", () => {
    const stats = makeStats({ by_severity: { "1": 3, "4": 1 } });

    const rows = severityRows(stats);

    expect(rows.map((r) => r.key)).toEqual(["1", "2", "3", "4", "5"]);
    expect(rows.map((r) => r.label)).toEqual([
      "Severity 1",
      "Severity 2",
      "Severity 3",
      "Severity 4",
      "Severity 5",
    ]);
    expect(rows.map((r) => r.count)).toEqual([3, 0, 0, 1, 0]);
    // share denominator: the sum of the series' own counts (3 + 1 = 4).
    expect(rows[0]?.share).toBeCloseTo(0.75);
    // bar denominator: the series' largest count (3).
    expect(rows[0]?.bar).toBe(100);
    expect(rows[3]?.bar).toBe(33);
  });
});

describe("categoryRows", () => {
  it("orders categories by count desc then key asc and drops empty buckets", () => {
    const stats = makeStats({
      by_category: {
        scanning: 2,
        brute_force: 2,
        other: 0,
        reconnaissance: 5,
      },
    });

    const rows = categoryRows(stats);

    // "brute_force" and "scanning" tie at count 2; the key-asc tiebreak orders "brute_force"
    // first ('b' < 's'). "other" is dropped for its zero count.
    expect(rows.map((r) => r.key)).toEqual(["reconnaissance", "brute_force", "scanning"]);
    expect(rows.map((r) => r.count)).toEqual([5, 2, 2]);
    // M1: the label must be the humanized form, not the raw key — `label: key` would still pass
    // every assertion above.
    expect(rows.map((r) => r.label)).toEqual(["Reconnaissance", "Brute force", "Scanning"]);
  });
});

describe("volumeRows", () => {
  it("keeps the API's day order and labels each row with its day", () => {
    const stats = makeStats({
      volume_by_day: [
        { day: "2026-09-01", count: 4 },
        { day: "2026-09-02", count: 8 },
      ],
    });

    const rows = volumeRows(stats);

    expect(rows.map((r) => r.key)).toEqual(["2026-09-01", "2026-09-02"]);
    expect(rows.map((r) => r.label)).toEqual(["2026-09-01", "2026-09-02"]);
    // bar relative to the busiest day (8).
    expect(rows[0]?.bar).toBe(50);
    expect(rows[1]?.bar).toBe(100);
  });
});

describe("zero denominators", () => {
  it("returns share 0 and bar 0 for an all-zero series", () => {
    const stats = makeStats();

    for (const row of severityRows(stats)) {
      expect(row.share).toBe(0);
      expect(row.bar).toBe(0);
      expect(Number.isFinite(row.share)).toBe(true);
      expect(Number.isFinite(row.bar)).toBe(true);
    }
  });
});
