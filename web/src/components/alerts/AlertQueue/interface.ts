import type { Column } from "@/components/ui/DataTable";
import type { PaginatedAlerts } from "@/types/api";

// Widths land on the DataTable <colgroup> so body cells size to the headers; Reasoning carries no
// width and takes the flexible remainder. Received is numeric, so it right-aligns.
export const ALERT_COLUMNS: readonly Column[] = [
  { key: "severity", header: "Severity", className: "w-20" },
  { key: "category", header: "Category", className: "w-40" },
  { key: "src_ip", header: "Source IP", className: "w-44" },
  { key: "sensor", header: "Sensor", className: "w-32" },
  { key: "reasoning", header: "Reasoning" },
  { key: "received", header: "Received", className: "w-28", align: "right" },
];

export type AlertQueueProps = {
  page: PaginatedAlerts | null;
  error: Error | null;
  now: Date;
  hrefForPage: (page: number) => string;
};
