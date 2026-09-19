import {
  CELL_NUMERIC,
  TABLE_CAPTION,
  TABLE_ELEMENT,
  TABLE_FRAME,
  TABLE_HEAD_CELL,
  TABLE_SCROLL,
} from "@/components/ui/tableStyles";
import { formatCount, formatPercent } from "@/lib/format";

import type { DistributionTableProps } from "./interface";

export function DistributionTable({
  caption,
  labelHeader,
  rows,
  emptyMessage,
}: DistributionTableProps) {
  return (
    <div className={TABLE_FRAME}>
      <div className={TABLE_SCROLL}>
        <table className={TABLE_ELEMENT}>
          <caption className={TABLE_CAPTION}>{caption}</caption>
          <thead>
            <tr>
              <th scope="col" className={TABLE_HEAD_CELL}>
                {labelHeader}
              </th>
              <th scope="col" className={`${TABLE_HEAD_CELL} ${CELL_NUMERIC}`}>
                Count
              </th>
              <th scope="col" className={TABLE_HEAD_CELL}>
                Share
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={3} className="text-center text-muted">
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row" className="font-normal">
                    {row.label}
                  </th>
                  <td className={CELL_NUMERIC}>{formatCount(row.count)}</td>
                  <td>
                    {formatPercent(row.share)}
                    <div
                      aria-hidden="true"
                      className="mt-1 h-1 rounded-full bg-accent"
                      style={{ width: `${row.bar}%` }}
                    />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
