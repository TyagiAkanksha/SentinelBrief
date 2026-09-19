// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { mockUsePathname } = vi.hoisted(() => ({ mockUsePathname: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: mockUsePathname,
}));

import { PrimaryNav } from "@/components/ui/PrimaryNav";

describe("PrimaryNav", () => {
  it("renders the three primary links, in PRD order, under one labelled nav", () => {
    mockUsePathname.mockReturnValue("/alerts");

    render(<PrimaryNav />);

    const nav = screen.getByRole("navigation", { name: "Primary" });
    const links = within(nav).getAllByRole("link");

    expect(links.map((link) => link.textContent)).toEqual(["Alerts", "Stats", "About"]);
    expect(links.map((link) => link.getAttribute("href"))).toEqual(["/alerts", "/stats", "/about"]);
  });

  it("marks only the current page with aria-current", () => {
    mockUsePathname.mockReturnValue("/stats");

    render(<PrimaryNav />);

    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Stats" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Alerts" })).not.toHaveAttribute("aria-current");
    expect(within(nav).getByRole("link", { name: "About" })).not.toHaveAttribute("aria-current");
  });

  it("carries the dashboard-wide link treatment on every link", () => {
    mockUsePathname.mockReturnValue("/alerts");

    render(<PrimaryNav />);

    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const link of within(nav).getAllByRole("link")) {
      expect(link.className).toContain("text-accent");
      expect(link.className).toContain("underline");
    }
  });
});
