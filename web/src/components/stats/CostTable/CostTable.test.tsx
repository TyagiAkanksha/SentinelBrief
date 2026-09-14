// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { CostTable } from "@/components/stats/CostTable";
import type { DayCost } from "@/types/api";

// Ruling R21: every day carries ≥ 2 alerts, so Total ≠ Mean on every row and a row-scoped
// `getByText` can tell the two `$…` cells of the same row apart. A single-alert fixture makes
// Total and Mean identical by definition, which is what forced the UI's non-spec "Mean " prefix
// the first time around — the fixture, not the UI, was the defect.
function makeRows(): DayCost[] {
  return [
    { day: "2026-09-01", alerts: 2, cost_usd: "0.000456", mean_cost_usd: "0.000228" },
    { day: "2026-09-02", alerts: 3, cost_usd: "0.000900", mean_cost_usd: "0.000300" },
  ];
}

describe("CostTable", () => {
  it("renders days newest first with formatted costs", () => {
    render(<CostTable rows={makeRows()} emptyMessage="No cost data yet" />);

    // Scoped through the table's own accessible name (t02 N1): a row query that accidentally
    // picked up rows from a sibling table on the page would fail here instead of silently
    // matching the wrong table's rows.
    const table = screen.getByRole("table", { name: "Cost per day" });
    // Skip the header row; newest day first is the reverse of the ascending API order the
    // fixture is given in.
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);

    const [newest, oldest] = rows;

    // The day cell is a `rowheader` (`<th scope="row">`), not a plain `<td>` (t02 N2) — a
    // regression to a `<td>` fails this assertion even though `getByText` would still pass.
    expect(within(newest!).getByRole("rowheader", { name: "2026-09-02" })).toBeInTheDocument();

    // Row-scoped queries pin all four cells: a Mean cell that reads `cost_usd` instead of
    // `mean_cost_usd`, or an Alerts cell that renders `row.alerts + 1`, must both fail here.
    expect(within(newest!).getByText("2026-09-02")).toBeInTheDocument();
    expect(within(newest!).getByText("3")).toBeInTheDocument();
    expect(within(newest!).getByText("$0.000900")).toBeInTheDocument();
    expect(within(newest!).getByText("$0.000300")).toBeInTheDocument();

    expect(within(oldest!).getByText("2026-09-01")).toBeInTheDocument();
    expect(within(oldest!).getByText("2")).toBeInTheDocument();
    expect(within(oldest!).getByText("$0.000456")).toBeInTheDocument();
    expect(within(oldest!).getByText("$0.000228")).toBeInTheDocument();
  });

  it("renders the empty message when there are no rows", () => {
    render(<CostTable rows={[]} emptyMessage="No cost data yet" />);

    expect(screen.getByText("No cost data yet")).toBeInTheDocument();
    expect(screen.queryByText("2026-09-01")).not.toBeInTheDocument();
  });
});
