// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { LiveIndicator } from "@/components/alerts/LiveIndicator";
import type { StreamStatus } from "@/hooks/useAlertStream";

describe("LiveIndicator", () => {
  it("names the state in text for each status", () => {
    const cases: Array<{ status: StreamStatus; label: string }> = [
      { status: "connecting", label: "Connecting…" },
      { status: "live", label: "Live" },
      { status: "polling", label: "Polling every 30s" },
    ];

    for (const { status, label } of cases) {
      const { unmount } = render(<LiveIndicator status={status} updates={0} />);

      const indicator = screen.getByRole("status");
      expect(indicator).toHaveAttribute("aria-live", "polite");
      expect(indicator).toHaveTextContent(label);
      // No updates yet: the "· N update(s)" suffix must not appear.
      expect(indicator.textContent).not.toContain("update");
      unmount();
    }

    const { unmount } = render(<LiveIndicator status="live" updates={3} />);
    const indicator = screen.getByRole("status");
    expect(indicator).toHaveTextContent("Live");
    expect(indicator).toHaveTextContent("3 update(s)");
    unmount();
  });
});
