// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { AlertRow } from "@/components/alerts/AlertRow";
import type { AlertSummary, VerdictSummary } from "@/types/api";

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

describe("AlertRow", () => {
  it("renders src_ip, sensor and the reasoning excerpt as text", () => {
    const verdict = makeVerdict();
    const alert = makeAlert({ verdict });

    render(
      <table>
        <tbody>
          <AlertRow alert={alert} now={now} />
        </tbody>
      </table>,
    );

    expect(screen.getByText(alert.src_ip)).toBeInTheDocument();
    expect(screen.getByText(alert.sensor)).toBeInTheDocument();
    expect(screen.getByText(verdict.reasoning_excerpt)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: alert.src_ip })).toHaveAttribute(
      "href",
      `/alerts/${alert.id}`,
    );
  });

  it("escapes a <script> fragment in reasoning_excerpt", () => {
    const alert = makeAlert({
      verdict: makeVerdict({ reasoning_excerpt: "<script>alert(1)</script>" }),
    });

    const { container } = render(
      <table>
        <tbody>
          <AlertRow alert={alert} now={now} />
        </tbody>
      </table>,
    );

    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
  });

  it("shows the status instead of a badge when there is no verdict", () => {
    const alert = makeAlert({ verdict: null, status: "pending" });

    render(
      <table>
        <tbody>
          <AlertRow alert={alert} now={now} />
        </tbody>
      </table>,
    );

    expect(screen.getByText("pending")).toBeInTheDocument();
    expect(screen.getByText("awaiting triage")).toBeInTheDocument();
    expect(screen.queryByText(/^S[1-5]$/)).toBeNull();
  });

  it("renders a relative age with the UTC timestamp as title", () => {
    const alert = makeAlert({ received_at: "2026-09-06T00:57:00.000Z" });

    render(
      <table>
        <tbody>
          <AlertRow alert={alert} now={now} />
        </tbody>
      </table>,
    );

    const time = screen.getByText("3m ago");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("title", "2026-09-06 00:57:00Z");
    expect(time).toHaveAttribute("dateTime", alert.received_at);
  });

  it("renders exactly six cells in ALERT_COLUMNS order", () => {
    const verdict = makeVerdict({ severity: 4 });
    const alert = makeAlert({ verdict });

    render(
      <table>
        <tbody>
          <AlertRow alert={alert} now={now} />
        </tbody>
      </table>,
    );

    const cells = screen.getAllByRole("cell");
    expect(cells).toHaveLength(6);

    const [severityCell, categoryCell, ipCell, sensorCell, reasoningCell, receivedCell] = cells;

    expect(within(severityCell!).getByLabelText("Severity 4")).toBeInTheDocument();
    expect(categoryCell!).toHaveTextContent(verdict.category);
    expect(within(ipCell!).getByRole("link", { name: alert.src_ip })).toBeInTheDocument();
    expect(sensorCell!).toHaveTextContent(alert.sensor);
    expect(reasoningCell!).toHaveTextContent(verdict.reasoning_excerpt);
    expect(receivedCell!.querySelector("time")).not.toBeNull();
  });
});
