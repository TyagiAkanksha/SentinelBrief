// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { CostTable } from "@/components/stats/CostTable";
import type { DayCost } from "@/types/api";

function makeRows(): DayCost[] {
  return [
    { day: "2026-09-01", alerts: 2, cost_usd: "0.000300", mean_cost_usd: "0.000150" },
    { day: "2026-09-02", alerts: 1, cost_usd: "0.000228", mean_cost_usd: "0.000228" },
  ];
}

describe("CostTable", () => {
  it("renders days newest first with formatted costs", () => {
    render(<CostTable rows={makeRows()} emptyMessage="No cost data yet" />);

    const table = screen.getByRole("table", { name: "Cost per day" });
    const dataRows = within(table).getAllByRole("row").slice(1);

    // Newest day first — the reverse of the ascending API order the fixture is given in.
    expect(dataRows).toHaveLength(2);
    expect(dataRows[0]).toHaveTextContent("2026-09-02");
    expect(dataRows[1]).toHaveTextContent("2026-09-01");

    expect(screen.getByText("$0.000228")).toBeInTheDocument();
    expect(screen.getByText("$0.000300")).toBeInTheDocument();
  });

  it("renders the empty message when there are no rows", () => {
    render(<CostTable rows={[]} emptyMessage="No cost data yet" />);

    expect(screen.getByText("No cost data yet")).toBeInTheDocument();
    expect(screen.queryByText("2026-09-01")).not.toBeInTheDocument();
  });
});
