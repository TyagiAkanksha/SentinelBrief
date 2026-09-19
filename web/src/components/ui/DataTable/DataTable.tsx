import { Fragment } from "react";

import {
  CELL_NUMERIC,
  TABLE_ELEMENT,
  TABLE_FRAME,
  TABLE_HEAD_CELL,
  TABLE_SCROLL,
} from "@/components/ui/tableStyles";

import type { DataTableProps } from "./interface";

export function DataTable<Row>({
  caption,
  columns,
  rows,
  rowKey,
  renderRow,
  emptyMessage,
}: DataTableProps<Row>) {
  return (
    <div className={TABLE_FRAME}>
      <div className={TABLE_SCROLL}>
        <table className={TABLE_ELEMENT}>
          <caption className="sr-only">{caption}</caption>
          {/* Widths from each column.className size the whole column (header + body cells); align
              stays on the <th> below. Without this, className landed on the <th> only. */}
          <colgroup>
            {columns.map((column) => (
              <col key={column.key} className={column.className} />
            ))}
          </colgroup>
          <thead>
            <tr>
              {columns.map((column) => {
                const align = column.align === "right" ? CELL_NUMERIC : "text-left";
                return (
                  <th
                    key={column.key}
                    scope="col"
                    className={`${TABLE_HEAD_CELL} ${align}${column.className ? ` ${column.className}` : ""}`}
                  >
                    {column.header}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="text-center text-muted">
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              rows.map((row) => <Fragment key={rowKey(row)}>{renderRow(row)}</Fragment>)
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
