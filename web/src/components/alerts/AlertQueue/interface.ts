import type { Column } from "@/components/ui/DataTable";
import type { PaginatedAlerts } from "@/types/api";

export const ALERT_COLUMNS: readonly Column[] = [
  { key: "severity", header: "Severity" },
  { key: "category", header: "Category" },
  { key: "src_ip", header: "Source IP" },
  { key: "sensor", header: "Sensor" },
  { key: "reasoning", header: "Reasoning" },
  { key: "received", header: "Received" },
];

export type AlertQueueProps = {
  page: PaginatedAlerts | null;
  error: Error | null;
  now: Date;
  hrefForPage: (page: number) => string;
};
