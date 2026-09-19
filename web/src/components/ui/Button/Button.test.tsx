// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Button, ButtonLink } from "@/components/ui/Button";

describe("Button", () => {
  it("renders a native button carrying the requested type", () => {
    render(<Button type="submit">Apply</Button>);

    expect(screen.getByRole("button", { name: "Apply" })).toHaveAttribute("type", "submit");
  });

  it("defaults to type=button", () => {
    render(<Button>Go</Button>);

    expect(screen.getByRole("button", { name: "Go" })).toHaveAttribute("type", "button");
  });

  it("uses the primary variant by default and the ghost variant on request", () => {
    const { rerender } = render(<Button>Primary</Button>);
    expect(screen.getByRole("button", { name: "Primary" }).className).toContain("bg-accent");

    rerender(<Button variant="ghost">Ghost</Button>);
    expect(screen.getByRole("button", { name: "Ghost" }).className).toContain("bg-surface");
  });
});

describe("ButtonLink", () => {
  it("renders an internal link with no target by default", () => {
    render(<ButtonLink href="/alerts">Live queue</ButtonLink>);

    const link = screen.getByRole("link", { name: "Live queue" });
    expect(link).toHaveAttribute("href", "/alerts");
    expect(link).not.toHaveAttribute("target");
  });

  it("opens external links safely in a new tab", () => {
    render(
      <ButtonLink href="https://example.com/spec" external>
        Product spec
      </ButtonLink>,
    );

    const link = screen.getByRole("link", { name: "Product spec" });
    expect(link).toHaveAttribute("href", "https://example.com/spec");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noreferrer");
  });
});
