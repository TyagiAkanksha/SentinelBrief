// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import path from "node:path";
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

describe("primary nav", () => {
  it("links to About from the primary nav", () => {
    // Rendering the full `<html>` `RootLayout` document inside jsdom produces nesting warnings
    // (dispatch note, precedent: web/src/app/stats/page.test.tsx); asserting on the source text
    // is the accepted alternative here, resolved with `path.join(process.cwd(), ...)` rather than
    // `new URL(..., import.meta.url)` per ruling R20 (jsdom's global `URL` does not resolve a
    // relative path against a `file:` base).
    const layoutPath = path.join(process.cwd(), "src", "app", "layout.tsx");
    const source = readFileSync(layoutPath, "utf-8");

    expect(source).toMatch(/<Link\s+href="\/about">\s*About\s*<\/Link>/);
    expect(source.indexOf('href="/alerts"')).toBeLessThan(source.indexOf('href="/stats"'));
    expect(source.indexOf('href="/stats"')).toBeLessThan(source.indexOf('href="/about"'));
  });
});
