// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { formatUtc } from "@/lib/format";
import type { StatsOut } from "@/types/api";

vi.mock("@/lib/api/server", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/server")>("@/lib/api/server");
  return {
    ...actual,
    getJson: vi.fn(),
  };
});

import StatsPage, { metadata } from "@/app/stats/page";
import { ApiError, getJson } from "@/lib/api/server";

const mockGetJson = vi.mocked(getJson);

afterEach(() => {
  mockGetJson.mockReset();
});

// Ruling R22: every number here is pairwise distinguishable from every other number the page
// prints, so a mutation that feeds a card or a table the wrong field has nowhere to hide behind
// an accidental coincidence (e.g. `total_alerts` must never equal `triaged`, `cost_mean_usd` must
// never equal `cost_total_usd`, `latency_p50_ms` must never equal `latency_p95_ms`).
function makeStats(overrides: Partial<StatsOut> = {}): StatsOut {
  return {
    total_alerts: 12,
    by_status: { pending: 1, triaged: 9, failed: 2 },
    by_severity: { "1": 4, "2": 2, "3": 1, "4": 1, "5": 1 },
    by_category: {
      scanning: 4,
      brute_force: 2,
      successful_intrusion: 1,
      malware_delivery: 1,
      persistence_attempt: 0,
      reconnaissance: 1,
      other: 0,
    },
    escalated_count: 3,
    volume_by_day: [{ day: "2026-09-01", count: 6 }],
    cost_total_usd: "0.002736",
    cost_mean_usd: "0.000228",
    latency_p50_ms: 812,
    latency_p95_ms: 2450,
    last_alert_at: "2026-09-06T00:57:00.000Z",
    cost_by_day: [
      { day: "2026-09-01", alerts: 6, cost_usd: "0.000684", mean_cost_usd: "0.000114" },
    ],
    ...overrides,
  };
}

describe("StatsPage", () => {
  it("sets the document title via metadata.title (t03 M-meta)", () => {
    expect(metadata.title).toBe("Stats — SentinelBrief");
  });

  it("renders the headline stats and all four tables", async () => {
    mockGetJson.mockResolvedValueOnce(makeStats());

    const element = await StatsPage();
    render(element);

    // Every card's VALUE is pinned, not only its label (ruling R22) — a mutation feeding a card
    // the wrong field has no coincidental match to hide behind (see the fixture's comment above).
    expect(screen.getByText("Total alerts")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();

    expect(screen.getByText("Triaged")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument();

    expect(screen.getByText("Escalation rate")).toBeInTheDocument();
    expect(screen.getByText("33%")).toBeInTheDocument();
    expect(screen.getByText("3 of 9 triaged")).toBeInTheDocument();

    expect(screen.getByText("Mean cost per alert")).toBeInTheDocument();
    expect(screen.getByText("$0.000228")).toBeInTheDocument();
    expect(screen.getByText("Total $0.002736")).toBeInTheDocument();

    expect(screen.getByText("Latency p50")).toBeInTheDocument();
    expect(screen.getByText("812 ms")).toBeInTheDocument();
    expect(screen.getByText("Latency p95")).toBeInTheDocument();
    expect(screen.getByText("2,450 ms")).toBeInTheDocument();

    expect(screen.getByText("Last alert")).toBeInTheDocument();
    expect(screen.getByText(formatUtc("2026-09-06T00:57:00.000Z"))).toBeInTheDocument();

    expect(screen.getByRole("table", { name: "Alerts per day" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Cost per day" })).toBeInTheDocument();

    // A mutation that swaps `severityRows`/`categoryRows` between these two tables must fail:
    // the severity table only ever prints "Severity N" labels, the category table only ever
    // prints humanized category labels.
    const severityTable = screen.getByRole("table", { name: "Severity distribution" });
    expect(within(severityTable).getByText("Severity 1")).toBeInTheDocument();
    expect(within(severityTable).getByText("Severity 5")).toBeInTheDocument();
    expect(within(severityTable).queryByText("Brute force")).not.toBeInTheDocument();

    const categoryTable = screen.getByRole("table", { name: "Category distribution" });
    expect(within(categoryTable).getByText("Brute force")).toBeInTheDocument();
    expect(within(categoryTable).queryByText("Severity 1")).not.toBeInTheDocument();
  });

  it("renders the empty state when the database has no alerts", async () => {
    mockGetJson.mockResolvedValueOnce(makeStats({ total_alerts: 0 }));

    const element = await StatsPage();
    render(element);

    expect(screen.getByText("No alerts yet — run scripts/seed_dev.py")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders the API error envelope message when the fetch fails", async () => {
    mockGetJson.mockRejectedValueOnce(
      new ApiError(503, { error: { code: "internal_error", message: "db down" } }),
    );

    const element = await StatsPage();
    render(element);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("API error 503");
    expect(alert).toHaveTextContent("db down");
  });
});

describe("primary nav", () => {
  it("links to Stats from the primary nav", () => {
    // Rendering the full `<html>` `RootLayout` document inside jsdom produces nesting warnings
    // (dispatch note); asserting on the source text is the accepted alternative here.
    const layoutPath = path.join(process.cwd(), "src", "app", "layout.tsx");
    const source = readFileSync(layoutPath, "utf-8");

    expect(source).toMatch(/<Link\s+href="\/stats">\s*Stats\s*<\/Link>/);
    expect(source.indexOf('href="/alerts"')).toBeLessThan(source.indexOf('href="/stats"'));
  });
});
