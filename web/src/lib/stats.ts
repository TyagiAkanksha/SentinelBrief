import type { StatsOut } from "@/types/api";

export type DistributionRow = {
  key: string; // the raw bucket key ("4", "brute_force", "2026-09-12")
  label: string; // what the table prints ("Severity 4", "Brute force", "2026-09-12")
  count: number;
  share: number; // count / total, 0..1 (0 when total is 0) — printed with formatPercent
  bar: number; // Math.round((count / max) * 100), 0..100 (0 when max is 0) — the bar's width %
};

type RowInput = { key: string; label: string; count: number };

// share uses the sum of the series' own counts; bar uses the series' largest count. Both are 0
// (never NaN) when their denominator is 0.
function buildRows(entries: readonly RowInput[]): DistributionRow[] {
  const total = entries.reduce((sum, entry) => sum + entry.count, 0);
  const max = entries.reduce((m, entry) => Math.max(m, entry.count), 0);
  return entries.map((entry) => ({
    key: entry.key,
    label: entry.label,
    count: entry.count,
    share: total > 0 ? entry.count / total : 0,
    bar: max > 0 ? Math.round((entry.count / max) * 100) : 0,
  }));
}

// "brute_force" -> "Brute force"; unknown keys pass through with _ -> space and an initial
// capital, so a category the frontend has never seen still renders legibly.
export function humanizeCategory(key: string): string {
  const words = key.split("_").join(" ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function triagedCount(stats: StatsOut): number {
  return stats.by_status.triaged ?? 0;
}

export function escalationRate(stats: StatsOut): number {
  const triaged = triagedCount(stats);
  return triaged === 0 ? 0 : stats.escalated_count / triaged;
}

// Keys "1".."5" always, ascending, zero-filling any gap so the severity table never shrinks.
export function severityRows(stats: StatsOut): DistributionRow[] {
  const entries: RowInput[] = Array.from({ length: 5 }, (_, i) => {
    const key = String(i + 1);
    return { key, label: `Severity ${key}`, count: stats.by_severity[key] ?? 0 };
  });
  return buildRows(entries);
}

// Every key of `by_category`, ordered count desc then key asc; zero-count categories are dropped.
export function categoryRows(stats: StatsOut): DistributionRow[] {
  const entries: RowInput[] = Object.entries(stats.by_category)
    .filter(([, count]) => count > 0)
    .map(([key, count]) => ({ key, label: humanizeCategory(key), count }))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
  return buildRows(entries);
}

// The API's own day order, unchanged; each row's label is its day.
export function volumeRows(stats: StatsOut): DistributionRow[] {
  const entries: RowInput[] = stats.volume_by_day.map((row) => ({
    key: row.day,
    label: row.day,
    count: row.count,
  }));
  return buildRows(entries);
}
