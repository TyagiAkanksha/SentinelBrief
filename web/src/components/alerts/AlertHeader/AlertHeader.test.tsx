// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { AlertHeader } from "@/components/alerts/AlertHeader";
import type { AlertDetail } from "@/types/api";

function makeAlert(overrides: Partial<AlertDetail> = {}): AlertDetail {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    source: "cowrie",
    src_ip: "203.0.113.7",
    sensor: "sensor-7",
    event_time: "2026-09-06T00:55:00.000Z",
    received_at: "2026-09-06T00:57:00.000Z",
    status: "triaged",
    raw: {},
    tool_calls: [],
    verdict: null,
    ...overrides,
  };
}

const now = new Date("2026-09-06T01:00:00.000Z");

describe("AlertHeader", () => {
  it("renders source ip, sensor, source and status", () => {
    const alert = makeAlert();

    render(<AlertHeader alert={alert} now={now} />);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(alert.src_ip);
    expect(screen.getByText(alert.sensor)).toBeInTheDocument();
    expect(screen.getByText(alert.source)).toBeInTheDocument();
    expect(screen.getByText(alert.status)).toBeInTheDocument();
  });

  it("renders event and received times in UTC with a relative hint", () => {
    const alert = makeAlert();

    const { container } = render(<AlertHeader alert={alert} now={now} />);

    const times = Array.from(container.querySelectorAll("time"));
    expect(times).toHaveLength(2);
    for (const time of times) {
      expect(time.getAttribute("title")).toMatch(/Z$/);
    }
    expect(times[1]!).toHaveTextContent("(3m ago)");
  });
});
