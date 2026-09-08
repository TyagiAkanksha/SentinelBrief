// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { EmptyState } from "@/components/ui/EmptyState";

describe("EmptyState", () => {
  it("renders the message with role=status", () => {
    render(<EmptyState message="No alerts yet — run scripts/seed_dev.py" />);

    expect(screen.getByRole("status")).toHaveTextContent("No alerts yet — run scripts/seed_dev.py");
  });
});
