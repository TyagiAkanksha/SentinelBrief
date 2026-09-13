// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import type { StatsOut } from "@/types/api";

vi.mock("@/lib/api/server", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/server")>("@/lib/api/server");
  return {
    ...actual,
    getJson: vi.fn(),
  };
});

import StatsPage from "@/app/stats/page";
import { ApiError, getJson } from "@/lib/api/server";

const mockGetJson = vi.mocked(getJson);

afterEach(() => {
  mockGetJson.mockReset();
});

function makeStats(overrides: Partial<StatsOut> = {}): StatsOut {
  return {
    total_alerts: 10,
    by_status: { pending: 1, triaged: 9, failed: 0 },
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
    escalated_count: 2,
    volume_by_day: [{ day: "2026-09-01", count: 10 }],
    cost_total_usd: "0.001000",
    cost_mean_usd: "0.000100",
    latency_p50_ms: 120,
    latency_p95_ms: 480,
    last_alert_at: "2026-09-06T00:57:00.000Z",
    cost_by_day: [
      { day: "2026-09-01", alerts: 10, cost_usd: "0.001000", mean_cost_usd: "0.000100" },
    ],
    ...overrides,
  };
}

describe("StatsPage", () => {
  it("renders the headline stats and all four tables", async () => {
    mockGetJson.mockResolvedValueOnce(makeStats());

    const element = await StatsPage();
    render(element);

    expect(screen.getByText("Total alerts")).toBeInTheDocument();
    expect(screen.getByText("Triaged")).toBeInTheDocument();
    expect(screen.getByText("Escalation rate")).toBeInTheDocument();
    expect(screen.getByText("Mean cost per alert")).toBeInTheDocument();
    expect(screen.getByText("Latency p50")).toBeInTheDocument();
    expect(screen.getByText("Latency p95")).toBeInTheDocument();
    expect(screen.getByText("Last alert")).toBeInTheDocument();

    expect(screen.getByRole("table", { name: "Alerts per day" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Severity distribution" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Category distribution" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Cost per day" })).toBeInTheDocument();
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
    const layoutPath = fileURLToPath(new URL("../layout.tsx", import.meta.url));
    const source = readFileSync(layoutPath, "utf-8");

    expect(source).toMatch(/<Link\s+href="\/stats">\s*Stats\s*<\/Link>/);
    expect(source.indexOf('href="/alerts"')).toBeLessThan(source.indexOf('href="/stats"'));
  });
});
