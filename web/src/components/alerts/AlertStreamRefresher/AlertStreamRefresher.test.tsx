// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AlertStreamState, UseAlertStreamOptions } from "@/hooks/useAlertStream";

const { mockRefresh, mockUseRouter } = vi.hoisted(() => {
  const mockRefresh = vi.fn();
  const mockUseRouter = vi.fn(() => ({ refresh: mockRefresh }));
  return { mockRefresh, mockUseRouter };
});

vi.mock("next/navigation", () => ({
  useRouter: mockUseRouter,
}));

const { mockUseAlertStream } = vi.hoisted(() => {
  return { mockUseAlertStream: vi.fn() };
});

vi.mock("@/hooks/useAlertStream", () => ({
  useAlertStream: mockUseAlertStream,
}));

import { AlertStreamRefresher } from "@/components/alerts/AlertStreamRefresher";

describe("AlertStreamRefresher", () => {
  it("refreshes the route when the hook reports an update", () => {
    mockUseAlertStream.mockReturnValue({ status: "live", updates: 2 } satisfies AlertStreamState);

    render(<AlertStreamRefresher />);

    expect(mockUseAlertStream).toHaveBeenCalledTimes(1);
    const options = mockUseAlertStream.mock.calls[0]?.[0] as UseAlertStreamOptions;
    expect(options.url.endsWith("/api/v1/stream")).toBe(true);
    expect(screen.getByRole("status")).toHaveTextContent("Live");

    options.onUpdate();

    expect(mockRefresh).toHaveBeenCalledTimes(1);
  });

  it("renders the live indicator above the filter bar", () => {
    // `path.join(process.cwd(), ...)` rather than `new URL(relative, import.meta.url)`: under
    // `// @vitest-environment jsdom`, the global `URL` is jsdom's own implementation, which does
    // not resolve a relative path against a `file:` base the way Node's `node:url` does — this
    // was confirmed empirically while authoring this file (see the test-author report).
    // `pnpm -C web test` always runs with cwd `web/`, so this is anchored the same way
    // `vitest.config.ts` anchors its own `@` alias.
    const pagePath = path.join(process.cwd(), "src", "app", "alerts", "page.tsx");
    const source = readFileSync(pagePath, "utf-8");

    const refresherIndex = source.indexOf("<AlertStreamRefresher");
    const filterBarIndex = source.indexOf("<FilterBar");

    expect(refresherIndex).toBeGreaterThan(-1);
    expect(filterBarIndex).toBeGreaterThan(-1);
    expect(refresherIndex).toBeLessThan(filterBarIndex);
  });
});
