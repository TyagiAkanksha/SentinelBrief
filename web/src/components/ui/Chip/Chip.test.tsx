// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Chip } from "@/components/ui/Chip";

describe("Chip", () => {
  it("renders its children as a rounded tag", () => {
    render(<Chip>FastAPI</Chip>);

    const chip = screen.getByText("FastAPI");
    expect(chip).toBeInTheDocument();
    expect(chip.className).toContain("rounded-full");
  });
});
