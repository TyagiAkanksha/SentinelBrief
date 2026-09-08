export type SearchParams = Record<string, string | string[] | undefined>;

export type ListQuery = {
  page: number;
  page_size: number;
  severity_gte?: number;
  category?: string;
  since?: string;
  escalate?: boolean;
};

export const DEFAULT_PAGE_SIZE = 25;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export function parseListQuery(sp: SearchParams): ListQuery {
  const rawPage = first(sp.page);
  const parsedPage = rawPage === undefined ? NaN : parseInt(rawPage, 10);
  const page = Number.isNaN(parsedPage) || parsedPage < 1 ? 1 : parsedPage;

  const rawPageSize = first(sp.page_size);
  const parsedPageSize = rawPageSize === undefined ? NaN : parseInt(rawPageSize, 10);
  const pageSize = Number.isNaN(parsedPageSize)
    ? DEFAULT_PAGE_SIZE
    : Math.min(100, Math.max(1, parsedPageSize));

  const query: ListQuery = { page, page_size: pageSize };

  const rawSeverityGte = first(sp.severity_gte);
  if (rawSeverityGte !== undefined) {
    const parsed = parseInt(rawSeverityGte, 10);
    if (!Number.isNaN(parsed) && parsed >= 1 && parsed <= 5) {
      query.severity_gte = parsed;
    }
  }

  const rawCategory = first(sp.category);
  if (rawCategory !== undefined && rawCategory !== "") {
    query.category = rawCategory;
  }

  const rawSince = first(sp.since);
  if (rawSince !== undefined && rawSince !== "") {
    query.since = rawSince;
  }

  const rawEscalate = first(sp.escalate);
  if (rawEscalate === "true") {
    query.escalate = true;
  } else if (rawEscalate === "false") {
    query.escalate = false;
  }

  return query;
}

export function toQueryString(q: ListQuery): string {
  const params = new URLSearchParams();
  const entries = Object.entries(q).sort(([a], [b]) => a.localeCompare(b));
  for (const [key, value] of entries) {
    if (value !== undefined) {
      params.set(key, String(value));
    }
  }
  return params.toString();
}

export function pageHref(q: ListQuery, page: number): string {
  return `/alerts?${toQueryString({ ...q, page })}`;
}
