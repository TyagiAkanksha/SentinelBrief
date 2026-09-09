import type { VerdictCategory } from "@/types/api";

export type Severity = 1 | 2 | 3 | 4 | 5;

export type BadgeProps = { severity: Severity } | { category: VerdictCategory };
