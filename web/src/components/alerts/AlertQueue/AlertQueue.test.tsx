// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ALERT_COLUMNS, AlertQueue } from "@/components/alerts/AlertQueue";
import { ApiError } from "@/lib/api/server";
import type { AlertSummary, PaginatedAlerts, VerdictSummary } from "@/types/api";

function makeVerdict(overrides: Partial<VerdictSummary> = {}): VerdictSummary {
  return {
    category: "brute_force",
    confidence: 0.9,
    created_at: "2026-09-06T00:57:00.000Z",
    escalate: false,
    reasoning_excerpt: "repeated failed logins from a known scanner",
    severity: 4,
    ...overrides,
  };
}

function makeAlert(overrides: Partial<AlertSummary> = {}): AlertSummary {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    source: "cowrie",
    src_ip: "203.0.113.7",
    sensor: "sensor-1",
    event_time: "2026-09-06T00:55:00.000Z",
    received_at: "2026-09-06T00:57:00.000Z",
    status: "triaged",
    verdict: makeVerdict(),
    ...overrides,
  };
}

const now = new Date("2026-09-06T01:00:00.000Z");
const hrefForPage = (page: number): string => `/alerts?page=${page}`;

describe("AlertQueue", () => {
  it("renders the empty state when items is empty", () => {
    const page: PaginatedAlerts = { items: [], page: 1, page_size: 25, total: 0 };

    render(<AlertQueue page={page} error={null} now={now} hrefForPage={hrefForPage} />);

    expect(screen.getByText("No alerts yet — run scripts/seed_dev.py")).toBeInTheDocument();
  });

  it("renders the ApiError envelope message in the error state", () => {
    const error = new ApiError(503, { error: { code: "internal_error", message: "db down" } });

    render(<AlertQueue page={null} error={error} now={now} hrefForPage={hrefForPage} />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("API error 503");
    expect(alert).toHaveTextContent("db down");
  });

  it("renders the unreachable state for a non-API error", () => {
    const error = new Error("ECONNREFUSED");

    render(<AlertQueue page={null} error={error} now={now} hrefForPage={hrefForPage} />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("API unreachable");
    expect(alert).toHaveTextContent("ECONNREFUSED");
  });

  it("renders a table with one row per alert and pagination", () => {
    const page: PaginatedAlerts = {
      items: [makeAlert({ id: "1" }), makeAlert({ id: "2" })],
      page: 1,
      page_size: 25,
      total: 2,
    };

    render(<AlertQueue page={page} error={null} now={now} hrefForPage={hrefForPage} />);

    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByRole("navigation", { name: "Pagination" })).toBeInTheDocument();
  });

  it("renders the six column headers in ALERT_COLUMNS order", () => {
    expect(ALERT_COLUMNS.map((column) => column.key)).toEqual([
      "severity",
      "category",
      "src_ip",
      "sensor",
      "reasoning",
      "received",
    ]);

    const page: PaginatedAlerts = { items: [makeAlert()], page: 1, page_size: 25, total: 1 };

    render(<AlertQueue page={page} error={null} now={now} hrefForPage={hrefForPage} />);

    const headers = screen.getAllByRole("columnheader").map((header) => header.textContent);
    expect(headers).toEqual(ALERT_COLUMNS.map((column) => column.header));
  });
});
