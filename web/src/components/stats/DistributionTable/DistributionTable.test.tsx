// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { DistributionTable } from "@/components/stats/DistributionTable";
import type { DistributionRow } from "@/lib/stats";

function makeRows(): DistributionRow[] {
  return [
    { key: "1", label: "Severity 1", count: 3, share: 0.75, bar: 100 },
    { key: "4", label: "Severity 4", count: 1, share: 0.25, bar: 33 },
  ];
}

describe("DistributionTable", () => {
  it("renders a caption, one row per bucket and the share as text", () => {
    render(
      <DistributionTable
        caption="Severity distribution"
        labelHeader="Severity"
        rows={makeRows()}
        emptyMessage="No severities yet"
      />,
    );

    const table = screen.getByRole("table", { name: "Severity distribution" });

    // one header row + two data rows.
    expect(screen.getAllByRole("row")).toHaveLength(3);

    expect(screen.getByText("Severity 1")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();

    expect(screen.getByText("Severity 4")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();

    // FRONTEND-CONVENTIONS §9: the bar is decoration over text, never colour-only meaning —
    // it must be `aria-hidden`, and its inline width pins directly to `row.bar`.
    const bars = Array.from(table.querySelectorAll<HTMLElement>('[aria-hidden="true"]'));
    expect(bars).toHaveLength(2);
    expect(bars[0]?.style.width).toBe("100%");
    expect(bars[1]?.style.width).toBe("33%");
  });

  it("renders the empty message when there are no rows", () => {
    render(
      <DistributionTable
        caption="Category distribution"
        labelHeader="Category"
        rows={[]}
        emptyMessage="No categories yet"
      />,
    );

    expect(screen.getByText("No categories yet")).toBeInTheDocument();
    // header row + one full-width empty-message row, no data rows.
    expect(screen.getAllByRole("row")).toHaveLength(2);
  });
});
