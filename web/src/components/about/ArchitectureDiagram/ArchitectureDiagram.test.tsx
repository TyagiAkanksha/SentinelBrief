// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

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

  it("renders the labelled architecture nodes", () => {
    render(<ArchitectureDiagram />);

    expect(screen.getByText("Cowrie SSH honeypot")).toBeInTheDocument();
    expect(screen.getByText("API")).toBeInTheDocument();
    expect(screen.getByText("Redis")).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("ARQ worker")).toBeInTheDocument();
    expect(screen.getByText("Next.js dashboard")).toBeInTheDocument();
  });

  it("exposes the whole diagram to assistive tech as one labelled image", () => {
    render(<ArchitectureDiagram />);

    const diagram = screen.getByRole("img");
    const label = diagram.getAttribute("aria-label") ?? "";
    expect(label).toMatch(/honeypot/i);
    expect(label).toMatch(/worker/i);
    expect(label).toMatch(/dashboard/i);
  });

  it("wraps the diagram in a horizontally scrollable container", () => {
    const { container } = render(<ArchitectureDiagram />);

    const diagram = container.querySelector('[role="img"]');
    expect(diagram?.parentElement?.className).toContain("overflow-x-auto");
  });
});
