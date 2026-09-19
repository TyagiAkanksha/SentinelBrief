// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import AboutPage, { metadata } from "@/app/about/page";
import { PRD_URL, REPO_URL, RESULTS_URL } from "@/lib/site";

describe("AboutPage", () => {
  it("sets the document title via metadata.title (t03 M-meta)", () => {
    expect(metadata.title).toBe("About — SentinelBrief");
  });

  it("renders exactly the three approved paragraphs in order", () => {
    const { container } = render(<AboutPage />);

    const paragraphs = Array.from(container.querySelectorAll("p"));
    const openings = [
      "SentinelBrief triages honeypot alerts",
      "One alert is one honeypot session.",
      "This is a portfolio project",
    ];

    const indices = openings.map((opening) =>
      paragraphs.findIndex((paragraph) => paragraph.textContent?.trim().startsWith(opening)),
    );

    expect(indices.every((index) => index !== -1)).toBe(true);
    expect(indices[0]).toBeLessThan(indices[1] as number);
    expect(indices[1]).toBeLessThan(indices[2] as number);
  });

  it("states the claims the project is held to", () => {
    render(<AboutPage />);

    expect(screen.getByText(/The human always decides\./)).toBeInTheDocument();
    expect(
      screen.getByText(/no page view and no public request ever spends a token/),
    ).toBeInTheDocument();
    expect(screen.getByText(/including the runs that came out worse/)).toBeInTheDocument();
  });

  it("links to the results table, the repo, the spec and the live queue", () => {
    render(<AboutPage />);

    const results = screen.getByRole("link", { name: "Evaluation results" });
    expect(results).toHaveAttribute("href", RESULTS_URL);
    expect(results).toHaveAttribute("target", "_blank");
    expect(results.getAttribute("rel")).toContain("noreferrer");

    const repo = screen.getByRole("link", { name: "Source code" });
    expect(repo).toHaveAttribute("href", REPO_URL);
    expect(repo).toHaveAttribute("target", "_blank");
    expect(repo.getAttribute("rel")).toContain("noreferrer");

    const spec = screen.getByRole("link", { name: "Product spec" });
    expect(spec).toHaveAttribute("href", PRD_URL);
    expect(spec).toHaveAttribute("target", "_blank");
    expect(spec.getAttribute("rel")).toContain("noreferrer");

    const queue = screen.getByRole("link", { name: "Live alert queue" });
    expect(queue).toHaveAttribute("href", "/alerts");
  });

  it("has a single level-one heading", () => {
    render(<AboutPage />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("renders the sidebar tech-stack panel alongside the prose", () => {
    render(<AboutPage />);

    expect(screen.getByText("At a glance — tech stack")).toBeInTheDocument();
    expect(screen.getByText("Caddy")).toBeInTheDocument();
    expect(screen.getByText("OpenAI")).toBeInTheDocument();
  });
});

describe("AboutPage network access", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders without any network access", () => {
    const fetchStub = vi.fn(() => {
      throw new Error("no fetch on /about");
    });
    vi.stubGlobal("fetch", fetchStub);

    render(<AboutPage />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(fetchStub).not.toHaveBeenCalled();
  });
});

// The former "primary nav" source-text check here (t02 M4 / M8a review Q3) is retired: the
// primary nav is now the render-tested `PrimaryNav` component
// (`web/src/components/ui/PrimaryNav/PrimaryNav.test.tsx`), used by `layout.tsx`.
