// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Badge } from "@/components/ui/Badge";
import type { Severity } from "@/components/ui/Badge";

describe("Badge", () => {
  it("renders S1..S5 as text for every severity", () => {
    const severities: Severity[] = [1, 2, 3, 4, 5];

    for (const severity of severities) {
      const { unmount } = render(<Badge severity={severity} />);

      expect(screen.getByText(`S${severity}`)).toBeInTheDocument();
      unmount();
    }
  });

  it("exposes an accessible severity label", () => {
    render(<Badge severity={4} />);

    expect(screen.getByLabelText("Severity 4")).toBeInTheDocument();
  });

  it("renders the category text verbatim", () => {
    render(<Badge category="brute_force" />);

    expect(screen.getByText("brute_force")).toBeInTheDocument();
  });
});
