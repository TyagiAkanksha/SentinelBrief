import type { DistributionRow } from "@/lib/stats";

export type DistributionTableProps = {
  caption: string;
  labelHeader: string;
  rows: DistributionRow[];
  emptyMessage: string;
};
