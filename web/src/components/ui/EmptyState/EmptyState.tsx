import type { EmptyStateProps } from "./interface";

export function EmptyState({ message }: EmptyStateProps) {
  return <p role="status">{message}</p>;
}
