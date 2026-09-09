import type { JSX } from "react";

import { AlertQueue } from "@/components/alerts/AlertQueue";
import { FilterBar } from "@/components/alerts/FilterBar";
import { pageHref, parseListQuery, toQueryString } from "@/lib/alerts-query";
import type { SearchParams } from "@/lib/alerts-query";
import { getJson } from "@/lib/api/server";
import type { PaginatedAlerts } from "@/types/api";

export const dynamic = "force-dynamic";

export default async function AlertsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}): Promise<JSX.Element> {
  const query = parseListQuery(await searchParams);
  const now = new Date();

  let page: PaginatedAlerts | null = null;
  let error: Error | null = null;
  try {
    page = await getJson<PaginatedAlerts>(`/api/v1/alerts?${toQueryString(query)}`);
  } catch (e) {
    error = e instanceof Error ? e : new Error(String(e));
  }

  return (
    <section>
      <h1 className="text-lg font-semibold">Alert queue</h1>
      <FilterBar query={query} />
      <AlertQueue page={page} error={error} now={now} hrefForPage={(p) => pageHref(query, p)} />
    </section>
  );
}
