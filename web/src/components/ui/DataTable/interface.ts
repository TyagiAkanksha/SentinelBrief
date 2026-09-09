import type { ReactNode } from "react";

export type Column = { key: string; header: string; className?: string };

export type DataTableProps<Row> = {
  caption: string;
  columns: readonly Column[];
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  renderRow: (row: Row) => ReactNode;
  emptyMessage: string;
};
