import { Button, ButtonLink } from "@/components/ui/Button";
import { FIELD_CONTROL, FIELD_LABEL } from "@/components/ui/formStyles";
import { formatDatetimeLocalUtc } from "@/lib/format";

import { VERDICT_CATEGORIES } from "./interface";
import type { FilterBarProps } from "./interface";

const SEVERITIES = [1, 2, 3, 4, 5] as const;

export function FilterBar({ query }: FilterBarProps) {
  const categoryIsKnown =
    query.category === undefined ||
    (VERDICT_CATEGORIES as readonly string[]).includes(query.category);

  return (
    <form
      method="get"
      action="/alerts"
      aria-label="Filters"
      className="flex flex-wrap items-end gap-x-4 gap-y-3 rounded-(--radius) border border-border bg-surface p-3"
    >
      <label className="flex flex-col gap-1">
        <span className={FIELD_LABEL}>Minimum severity</span>
        <select
          name="severity_gte"
          defaultValue={query.severity_gte?.toString() ?? ""}
          className={FIELD_CONTROL}
        >
          <option value="">any</option>
          {SEVERITIES.map((severity) => (
            <option key={severity} value={severity}>
              {`S${severity}`}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className={FIELD_LABEL}>Category</span>
        <select name="category" defaultValue={query.category ?? ""} className={FIELD_CONTROL}>
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
      <label className="flex flex-col gap-1">
        <span className={FIELD_LABEL}>Escalated</span>
        <select
          name="escalate"
          defaultValue={query.escalate === undefined ? "" : String(query.escalate)}
          className={FIELD_CONTROL}
        >
          <option value="">any</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className={FIELD_LABEL}>Since (UTC)</span>
        <input
          type="datetime-local"
          name="since"
          defaultValue={query.since ? formatDatetimeLocalUtc(query.since) : ""}
          className={FIELD_CONTROL}
        />
      </label>
      <input type="hidden" name="page_size" value={query.page_size} />
      <div className="flex items-end gap-2 sm:ml-auto">
        <Button type="submit" variant="primary">
          Apply
        </Button>
        <ButtonLink href="/alerts" variant="ghost">
          Clear
        </ButtonLink>
      </div>
    </form>
  );
}
