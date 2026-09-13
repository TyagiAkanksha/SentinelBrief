// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { mockUsePathname } = vi.hoisted(() => ({ mockUsePathname: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: mockUsePathname,
}));

import { NavLink } from "@/components/ui/NavLink";

describe("NavLink", () => {
  it("marks the link for the current page with aria-current", () => {
    mockUsePathname.mockReturnValue("/stats");

    render(<NavLink href="/stats">Stats</NavLink>);

    const link = screen.getByRole("link", { name: "Stats" });
    expect(link).toHaveAttribute("href", "/stats");
    expect(link).toHaveAttribute("aria-current", "page");
  });

  it("leaves aria-current off every other link, including a sub-path of its own href", () => {
    mockUsePathname.mockReturnValue("/alerts/9f0c1b2a");

    render(
      <>
        <NavLink href="/alerts">Alerts</NavLink>
        <NavLink href="/about">About</NavLink>
      </>,
    );

    expect(screen.getByRole("link", { name: "Alerts" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "About" })).not.toHaveAttribute("aria-current");
  });

  it("carries the dashboard-wide link treatment", () => {
    mockUsePathname.mockReturnValue("/alerts");

    render(<NavLink href="/about">About</NavLink>);

    const link = screen.getByRole("link", { name: "About" });
    expect(link.className).toContain("text-accent");
    expect(link.className).toContain("underline");
  });
});
