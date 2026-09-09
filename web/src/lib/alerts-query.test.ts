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

  it("treats non-safe-integer page and page_size as invalid", () => {
    // "1e23" is not parsed as scientific notation: `parseInt` reads only the leading "1" and
    // stops at "e", so a prefix-based guard lets it through unnoticed. `Number("1e23")` is
    // 1e23 — nowhere near a safe integer — so a `Number.isSafeInteger` guard is what actually
    // catches it (controller ruling t4-M1 fix wave).
    expect(parseListQuery({ page: "1e23" }).page).toBe(1);
    expect(parseListQuery({ page_size: "1e23" }).page_size).toBe(25);

    // A page number one above `Number.MAX_SAFE_INTEGER` rounds to a distinct double under
    // `Number()`, but is still not a safe integer — same invalid-like-default treatment.
    expect(parseListQuery({ page: "9007199254740993" }).page).toBe(1);
    expect(parseListQuery({ page_size: "9007199254740993" }).page_size).toBe(25);
  });
});

describe("toQueryString", () => {
  it("toQueryString sorts keys and omits undefined", () => {
    const query: ListQuery = { page: 2, page_size: 25, severity_gte: 4 };

    expect(toQueryString(query)).toBe("page=2&page_size=25&severity_gte=4");
  });

  it("toQueryString orders all six keys by code point", () => {
    // localeCompare and code-point order happen to agree on this exact key set (t4-M9 review
    // note), so this pin is likely GREEN already — it still pins the exact wire format a
    // locale-dependent comparator could silently reorder on a future key.
    const query: ListQuery = {
      page: 2,
      page_size: 25,
      severity_gte: 4,
      category: "brute_force",
      since: "2026-09-01T00:00:00Z",
      escalate: true,
    };

    expect(toQueryString(query)).toBe(
      "category=brute_force&escalate=true&page=2&page_size=25&severity_gte=4&since=2026-09-01T00%3A00%3A00Z",
    );
  });
});

describe("pageHref", () => {
  it("pageHref keeps every filter and replaces page", () => {
    const query: ListQuery = { page: 1, page_size: 25, category: "scanning" };

    expect(pageHref(query, 3)).toBe("/alerts?category=scanning&page=3&page_size=25");
  });
});
