// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Stat } from "@/components/ui/Stat";

describe("Stat", () => {
  it("renders the label, the value and the optional hint", () => {
    const { rerender } = render(<Stat label="Total alerts" value="128" hint="Since launch" />);

    expect(screen.getByText("Total alerts")).toBeInTheDocument();
    expect(screen.getByText("128")).toBeInTheDocument();
    expect(screen.getByText("Since launch")).toBeInTheDocument();

    rerender(<Stat label="Total alerts" value="128" />);

    expect(screen.queryByText("Since launch")).not.toBeInTheDocument();
  });
});
