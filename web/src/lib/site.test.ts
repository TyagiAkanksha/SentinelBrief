import { describe, expect, it } from "vitest";

import { PRD_URL, REPO_URL, RESULTS_URL } from "@/lib/site";

describe("site links", () => {
  it("exposes absolute https project links under the repo URL", () => {
    expect(REPO_URL.startsWith("https://")).toBe(true);
    expect(RESULTS_URL.startsWith("https://")).toBe(true);
    expect(PRD_URL.startsWith("https://")).toBe(true);

    expect(RESULTS_URL.startsWith(REPO_URL)).toBe(true);
    expect(PRD_URL.startsWith(REPO_URL)).toBe(true);

    expect(RESULTS_URL.endsWith("/docs/results.md")).toBe(true);

    for (const url of [REPO_URL, RESULTS_URL, PRD_URL]) {
      expect(url).toBe(url.trim());
    }
  });
});
