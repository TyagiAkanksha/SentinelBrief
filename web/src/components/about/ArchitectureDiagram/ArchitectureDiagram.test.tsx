// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { ArchitectureDiagram } from "@/components/about/ArchitectureDiagram";

describe("ArchitectureDiagram", () => {
  it("renders a figure whose caption describes the flow", () => {
    const { container } = render(<ArchitectureDiagram />);

    const figure = container.querySelector("figure");
    expect(figure).not.toBeNull();

    const caption = container.querySelector("figcaption");
    expect(caption).not.toBeNull();
    expect(caption?.textContent).toMatch(/honeypot/i);
    expect(caption?.textContent).toMatch(/worker/i);
    expect(caption?.textContent).toMatch(/dashboard/i);
  });

  it("hides the ASCII art from assistive technology", () => {
    const { container } = render(<ArchitectureDiagram />);

    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.getAttribute("aria-hidden")).toBe("true");
    expect(pre?.textContent).toContain("Cowrie");
    expect(pre?.textContent).toContain("PostgreSQL");
    expect(pre?.textContent).toContain("ARQ");
  });

  it("wraps the diagram in a horizontally scrollable container", () => {
    const { container } = render(<ArchitectureDiagram />);

    const pre = container.querySelector("pre");
    const parent = pre?.parentElement;
    expect(parent).not.toBeNull();
    expect(parent?.className).toContain("overflow-x-auto");
  });
});
