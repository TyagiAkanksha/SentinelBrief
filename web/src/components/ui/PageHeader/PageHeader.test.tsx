// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { PageHeader } from "@/components/ui/PageHeader";

describe("PageHeader", () => {
  it("renders the title as the level-one heading", () => {
    render(<PageHeader title="Alert queue" />);

    expect(screen.getByRole("heading", { level: 1, name: "Alert queue" })).toBeInTheDocument();
  });

  it("renders the optional subtitle only when provided", () => {
    const { rerender } = render(<PageHeader title="Stats" subtitle="Since launch" />);

    expect(screen.getByText("Since launch")).toBeInTheDocument();

    rerender(<PageHeader title="Stats" />);

    expect(screen.queryByText("Since launch")).not.toBeInTheDocument();
  });

  it("renders the actions slot when provided", () => {
    render(<PageHeader title="Alert queue" actions={<button type="button">Live</button>} />);

    expect(screen.getByRole("button", { name: "Live" })).toBeInTheDocument();
  });
});
