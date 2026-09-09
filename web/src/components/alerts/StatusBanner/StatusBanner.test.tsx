// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { StatusBanner } from "@/components/alerts/StatusBanner";

describe("StatusBanner", () => {
  it("explains a pending alert", () => {
    render(<StatusBanner status="pending" />);

    expect(screen.getByRole("status")).toHaveTextContent("Triage pending — no verdict yet.");
  });

  it("explains a failed alert", () => {
    render(<StatusBanner status="failed" />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Triage failed — no verdict was produced.",
    );
  });
});
