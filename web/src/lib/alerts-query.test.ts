import { describe, expect, it } from "vitest";

import { DEFAULT_PAGE_SIZE, pageHref, parseListQuery, toQueryString } from "@/lib/alerts-query";
import type { ListQuery, SearchParams } from "@/lib/alerts-query";

describe("parseListQuery", () => {
  it("defaults to page 1 and page_size 25", () => {
    const query = parseListQuery({});

    expect(query.page).toBe(1);
    expect(query.page_size).toBe(DEFAULT_PAGE_SIZE);
    expect(query.page_size).toBe(25);
    expect(query.severity_gte).toBeUndefined();
    expect(query.category).toBeUndefined();
    expect(query.since).toBeUndefined();
    expect(query.escalate).toBeUndefined();
  });

  it("coerces an invalid page to 1 and clamps page_size to 1..100", () => {
    expect(parseListQuery({ page: "abc" }).page).toBe(1);
    expect(parseListQuery({ page: "0" }).page).toBe(1);
    expect(parseListQuery({ page_size: "500" }).page_size).toBe(100);
    expect(parseListQuery({ page_size: "0" }).page_size).toBe(1);
  });

  it("passes severity_gte, category, since and escalate through", () => {
    const sp: SearchParams = {
      severity_gte: "4",
      category: "brute_force",
      since: "2026-09-01T00:00:00Z",
      escalate: "true",
    };

    const query = parseListQuery(sp);

    expect(query.severity_gte).toBe(4);
    expect(query.category).toBe("brute_force");
    expect(query.since).toBe("2026-09-01T00:00:00Z");
    expect(query.escalate).toBe(true);

    const withArrays = parseListQuery({
      severity_gte: ["4", "5"],
      category: ["brute_force", "scanning"],
    });

    expect(withArrays.severity_gte).toBe(4);
    expect(withArrays.category).toBe("brute_force");
  });

  it("drops severity_gte outside 1..5 and escalate values other than true/false", () => {
    const query = parseListQuery({ severity_gte: "9", escalate: "maybe" });

    expect(query.severity_gte).toBeUndefined();
    expect(query.escalate).toBeUndefined();
  });
});

describe("toQueryString", () => {
  it("toQueryString sorts keys and omits undefined", () => {
    const query: ListQuery = { page: 2, page_size: 25, severity_gte: 4 };

    expect(toQueryString(query)).toBe("page=2&page_size=25&severity_gte=4");
  });
});

describe("pageHref", () => {
  it("pageHref keeps every filter and replaces page", () => {
    const query: ListQuery = { page: 1, page_size: 25, category: "scanning" };

    expect(pageHref(query, 3)).toBe("/alerts?category=scanning&page=3&page_size=25");
  });
});
