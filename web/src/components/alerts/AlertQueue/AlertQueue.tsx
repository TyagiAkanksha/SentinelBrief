import { AlertRow } from "@/components/alerts/AlertRow";
import { DataTable } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Pagination } from "@/components/ui/Pagination";
import { ApiError } from "@/lib/api/server";
import type { AlertSummary } from "@/types/api";

import { ALERT_COLUMNS } from "./interface";
import type { AlertQueueProps } from "./interface";

export function AlertQueue({ page, error, now, hrefForPage }: AlertQueueProps) {
  if (error !== null) {
    return error instanceof ApiError ? (
      <ErrorState
        title={`API error ${error.status}`}
        detail={error.envelope?.error.message ?? error.message}
      />
    ) : (
      <ErrorState title="API unreachable" detail={error.message} />
    );
  }

  if (page === null || page.items.length === 0) {
    return <EmptyState message="No alerts yet — run scripts/seed_dev.py" />;
  }

  return (
    <>
      <DataTable
        caption="Alert queue, sorted by severity then recency"
        columns={ALERT_COLUMNS}
        rows={page.items}
        rowKey={(a: AlertSummary) => a.id}
        renderRow={(a: AlertSummary) => <AlertRow key={a.id} alert={a} now={now} />}
        emptyMessage="No alerts"
      />
      <Pagination
        page={page.page}
        pageSize={page.page_size}
        total={page.total}
        hrefForPage={hrefForPage}
      />
    </>
  );
}
