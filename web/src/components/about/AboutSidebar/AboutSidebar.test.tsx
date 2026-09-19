// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { AboutSidebar } from "@/components/about/AboutSidebar";
import { PRD_URL, REPO_URL, RESULTS_URL } from "@/lib/site";

describe("AboutSidebar", () => {
  it("links to the results, repo, spec and the live queue", () => {
    render(<AboutSidebar />);

    const results = screen.getByRole("link", { name: "Evaluation results" });
    expect(results).toHaveAttribute("href", RESULTS_URL);
    expect(results).toHaveAttribute("target", "_blank");
    expect(results.getAttribute("rel")).toContain("noreferrer");

    expect(screen.getByRole("link", { name: "Source code" })).toHaveAttribute("href", REPO_URL);
    expect(screen.getByRole("link", { name: "Product spec" })).toHaveAttribute("href", PRD_URL);

    const queue = screen.getByRole("link", { name: "Live alert queue" });
    expect(queue).toHaveAttribute("href", "/alerts");
    expect(queue).not.toHaveAttribute("target");
  });

  it("lists the tech stack as chips", () => {
    render(<AboutSidebar />);

    for (const tech of ["FastAPI", "Postgres", "Redis", "ARQ", "Next.js", "Caddy", "OpenAI"]) {
      expect(screen.getByText(tech)).toBeInTheDocument();
    }
  });
});
