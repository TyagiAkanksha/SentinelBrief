import type { ListQuery } from "@/lib/alerts-query";
import type { VerdictCategory } from "@/types/api";

export type FilterBarProps = { query: ListQuery };

export const VERDICT_CATEGORIES = [
  "scanning",
  "brute_force",
  "successful_intrusion",
  "malware_delivery",
  "persistence_attempt",
  "reconnaissance",
  "other",
] as const satisfies readonly VerdictCategory[];
