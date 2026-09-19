import {
  CELL_NUMERIC,
  TABLE_CAPTION,
  TABLE_ELEMENT,
  TABLE_FRAME,
  TABLE_HEAD_CELL,
  TABLE_SCROLL,
} from "@/components/ui/tableStyles";
import { formatCount, formatUsd } from "@/lib/format";

import type { CostTableProps } from "./interface";

export function CostTable({ rows, emptyMessage }: CostTableProps) {
  // Newest day first — the reverse of the ascending order `core.services.alerts_read.get_stats`
  // returns; this is presentation, so the component does the reversal, not the API.
  const newestFirst = [...rows].reverse();

  return (
    <div className={TABLE_FRAME}>
      <div className={TABLE_SCROLL}>
        <table className={TABLE_ELEMENT}>
          <caption className={TABLE_CAPTION}>Cost per day</caption>
          <thead>
            <tr>
              <th scope="col" className={TABLE_HEAD_CELL}>
                Day
              </th>
              <th scope="col" className={`${TABLE_HEAD_CELL} ${CELL_NUMERIC}`}>
                Alerts
              </th>
              <th scope="col" className={`${TABLE_HEAD_CELL} ${CELL_NUMERIC}`}>
                Total
              </th>
              <th scope="col" className={`${TABLE_HEAD_CELL} ${CELL_NUMERIC}`}>
                Mean per alert
              </th>
            </tr>
          </thead>
          <tbody>
            {newestFirst.length === 0 ? (
              <tr>
                <td colSpan={4} className="text-center text-muted">
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              newestFirst.map((row) => (
                <tr key={row.day}>
                  <th scope="row" className="font-normal">
                    {row.day}
                  </th>
                  <td className={CELL_NUMERIC}>{formatCount(row.alerts)}</td>
                  <td className={CELL_NUMERIC}>{formatUsd(row.cost_usd)}</td>
                  <td className={CELL_NUMERIC}>{formatUsd(row.mean_cost_usd)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
