// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { DataTable } from "@/components/ui/DataTable";
import type { Column } from "@/components/ui/DataTable";

type Row = { id: string; name: string };

const columns: readonly Column[] = [
  { key: "id", header: "ID" },
  { key: "name", header: "Name" },
];

const emptyRows: readonly Row[] = [];

const rows: readonly Row[] = [
  { id: "1", name: "a" },
  { id: "2", name: "b" },
  { id: "3", name: "c" },
];

function rowKey(row: Row): string {
  return row.id;
}

function renderRow(row: Row) {
  return (
    <tr key={row.id}>
      <td>{row.id}</td>
      <td>{row.name}</td>
    </tr>
  );
}

describe("DataTable", () => {
  it("renders the caption", () => {
    render(
      <DataTable
        caption="Alert queue, sorted by severity then recency"
        columns={columns}
        rows={emptyRows}
        rowKey={rowKey}
        renderRow={renderRow}
        emptyMessage="No alerts"
      />,
    );

    const caption = screen.getByText("Alert queue, sorted by severity then recency");
    expect(caption.tagName).toBe("CAPTION");
  });

  it("renders one header cell per column", () => {
    render(
      <DataTable
        caption="caption"
        columns={columns}
        rows={emptyRows}
        rowKey={rowKey}
        renderRow={renderRow}
        emptyMessage="No alerts"
      />,
    );

    expect(screen.getAllByRole("columnheader")).toHaveLength(columns.length);
  });

  it("renders the empty message when there are no rows", () => {
    render(
      <DataTable
        caption="caption"
        columns={columns}
        rows={emptyRows}
        rowKey={rowKey}
        renderRow={renderRow}
        emptyMessage="No alerts"
      />,
    );

    const cell = screen.getByText("No alerts");
    expect(cell.tagName).toBe("TD");
    expect(cell).toHaveAttribute("colSpan", String(columns.length));
  });

  it("renders one row per item via renderRow", () => {
    render(
      <DataTable
        caption="caption"
        columns={columns}
        rows={rows}
        rowKey={rowKey}
        renderRow={renderRow}
        emptyMessage="No alerts"
      />,
    );

    expect(screen.getAllByRole("row")).toHaveLength(4);
  });
});
