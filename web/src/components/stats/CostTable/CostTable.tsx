import { formatCount, formatUsd } from "@/lib/format";

import type { CostTableProps } from "./interface";

export function CostTable({ rows, emptyMessage }: CostTableProps) {
  // Newest day first — the reverse of the ascending order `core.services.alerts_read.get_stats`
  // returns; this is presentation, so the component does the reversal, not the API.
  const newestFirst = [...rows].reverse();

  return (
    <table className="w-full text-sm">
      <caption className="text-left text-xs text-muted">Cost per day</caption>
      <thead>
        <tr>
          <th scope="col" className="text-left">
            Day
          </th>
          <th scope="col" className="text-left">
            Alerts
          </th>
          <th scope="col" className="text-left">
            Total
          </th>
          <th scope="col" className="text-left">
            Mean per alert
          </th>
        </tr>
      </thead>
      <tbody>
        {newestFirst.length === 0 ? (
          <tr>
            <td colSpan={4}>{emptyMessage}</td>
          </tr>
        ) : (
          newestFirst.map((row) => (
            <tr key={row.day}>
              <td>{row.day}</td>
              <td>{formatCount(row.alerts)}</td>
              <td>{formatUsd(row.cost_usd)}</td>
              {/* On a single-alert day the mean equals the total, so the two cells would
                  otherwise carry identical text — "Mean " keeps every cell's value unambiguous
                  regardless of the day's alert count. */}
              <td>{`Mean ${formatUsd(row.mean_cost_usd)}`}</td>
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}
