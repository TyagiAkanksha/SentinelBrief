import { formatPercent } from "@/lib/format";

import type { DistributionTableProps } from "./interface";

export function DistributionTable({
  caption,
  labelHeader,
  rows,
  emptyMessage,
}: DistributionTableProps) {
  return (
    <table className="w-full text-sm">
      <caption className="text-left text-xs text-muted">{caption}</caption>
      <thead>
        <tr>
          <th scope="col" className="text-left">
            {labelHeader}
          </th>
          <th scope="col" className="text-left">
            Count
          </th>
          <th scope="col" className="text-left">
            Share
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr>
            <td colSpan={3}>{emptyMessage}</td>
          </tr>
        ) : (
          rows.map((row) => (
            <tr key={row.key}>
              <td>{row.label}</td>
              <td>{row.count}</td>
              <td>
                {formatPercent(row.share)}
                <div
                  aria-hidden="true"
                  className="mt-0.5 h-1 rounded bg-accent"
                  style={{ width: `${row.bar}%` }}
                />
              </td>
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}
