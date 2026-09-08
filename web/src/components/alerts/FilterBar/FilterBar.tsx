import Link from "next/link";

import { formatDatetimeLocalUtc } from "@/lib/format";

import { VERDICT_CATEGORIES } from "./interface";
import type { FilterBarProps } from "./interface";

const SEVERITIES = [1, 2, 3, 4, 5] as const;

export function FilterBar({ query }: FilterBarProps) {
  const categoryIsKnown =
    query.category === undefined ||
    (VERDICT_CATEGORIES as readonly string[]).includes(query.category);

  return (
    <form method="get" action="/alerts" aria-label="Filters">
      <label>
        Minimum severity
        <select name="severity_gte" defaultValue={query.severity_gte?.toString() ?? ""}>
          <option value="">any</option>
          {SEVERITIES.map((severity) => (
            <option key={severity} value={severity}>
              {`S${severity}`}
            </option>
          ))}
        </select>
      </label>
      <label>
        Category
        <select name="category" defaultValue={query.category ?? ""}>
          <option value="">any</option>
          {VERDICT_CATEGORIES.map((category) => (
            <option key={category} value={category}>
              {category}
            </option>
          ))}
          {!categoryIsKnown && query.category !== undefined && (
            <option value={query.category}>{query.category}</option>
          )}
        </select>
      </label>
      <label>
        Escalated
        <select
          name="escalate"
          defaultValue={query.escalate === undefined ? "" : String(query.escalate)}
        >
          <option value="">any</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>
      <label>
        Since (UTC)
        <input
          type="datetime-local"
          name="since"
          defaultValue={query.since ? formatDatetimeLocalUtc(query.since) : ""}
        />
      </label>
      <input type="hidden" name="page_size" value={query.page_size} />
      <button type="submit">Apply</button>
      <Link href="/alerts">Clear</Link>
    </form>
  );
}
