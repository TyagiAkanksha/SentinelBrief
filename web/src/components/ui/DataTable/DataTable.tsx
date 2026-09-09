import { Fragment } from "react";

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
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className={column.className}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length}>{emptyMessage}</td>
            </tr>
          ) : (
            rows.map((row) => <Fragment key={rowKey(row)}>{renderRow(row)}</Fragment>)
          )}
        </tbody>
      </table>
    </div>
  );
}
