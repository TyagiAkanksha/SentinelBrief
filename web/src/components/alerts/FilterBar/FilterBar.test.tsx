// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { FilterBar } from "@/components/alerts/FilterBar";
import type { ListQuery } from "@/lib/alerts-query";

const VERDICT_CATEGORIES_IN_ORDER = [
  "scanning",
  "brute_force",
  "successful_intrusion",
  "malware_delivery",
  "persistence_attempt",
  "reconnaissance",
  "other",
] as const;

describe("FilterBar", () => {
  it("renders the four labelled controls pre-filled from query with seven category options", () => {
    const query: ListQuery = {
      page: 1,
      page_size: 25,
      severity_gte: 4,
      category: "brute_force",
      escalate: true,
      since: "2026-09-01T00:00",
    };

    render(<FilterBar query={query} />);

    expect(screen.getByLabelText("Minimum severity")).toHaveValue("4");

    const category = screen.getByLabelText("Category");
    expect(category).toHaveValue("brute_force");
    const optionTexts = Array.from(category.querySelectorAll("option")).map(
      (option) => option.textContent,
    );
    expect(optionTexts).toEqual(["any", ...VERDICT_CATEGORIES_IN_ORDER]);

    expect(screen.getByLabelText("Escalated")).toHaveValue("true");
    expect(screen.getByLabelText("Since (UTC)")).toHaveValue("2026-09-01T00:00");
  });

  it("pre-fills since from a Z-suffixed URL value as a UTC datetime-local string", () => {
    const query: ListQuery = { page: 1, page_size: 25, since: "2026-09-01T00:00:00Z" };

    render(<FilterBar query={query} />);

    expect(screen.getByLabelText("Since (UTC)")).toHaveValue("2026-09-01T00:00");
  });

  it("selects any for every absent filter", () => {
    const query: ListQuery = { page: 1, page_size: 25 };

    render(<FilterBar query={query} />);

    expect(screen.getByLabelText("Minimum severity")).toHaveValue("");
    expect(screen.getByLabelText("Category")).toHaveValue("");
    expect(screen.getByLabelText("Escalated")).toHaveValue("");
    expect(screen.getByLabelText("Since (UTC)")).toHaveValue("");
  });

  it("submits with method=get to /alerts, carries page_size but never page, and offers a Clear link", () => {
    const query: ListQuery = { page: 1, page_size: 25 };

    const { container } = render(<FilterBar query={query} />);

    const form = screen.getByRole("form", { name: "Filters" });
    expect(form).toHaveAttribute("method", "get");
    expect(form).toHaveAttribute("action", "/alerts");
    expect(container.querySelector('input[name="page"]')).toBeNull();

    const pageSize = container.querySelector('input[name="page_size"]');
    expect(pageSize).not.toBeNull();
    expect(pageSize).toHaveAttribute("value", "25");
    expect(pageSize).toHaveAttribute("type", "hidden");

    expect(screen.getByRole("button", { name: "Apply" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Clear" })).toHaveAttribute("href", "/alerts");
  });

  it("escapes a <script> fragment in query.category", () => {
    const query: ListQuery = { page: 1, page_size: 25, category: "<script>alert(1)</script>" };

    const { container } = render(<FilterBar query={query} />);

    const category = screen.getByLabelText("Category");
    expect(category).toHaveValue("<script>alert(1)</script>");
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
  });
});
