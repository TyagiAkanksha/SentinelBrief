// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { mockSetTheme, mockUseTheme } = vi.hoisted(() => ({
  mockSetTheme: vi.fn(),
  mockUseTheme: vi.fn(),
}));

vi.mock("next-themes", () => ({
  useTheme: mockUseTheme,
}));

import { ThemeToggle } from "@/components/ui/ThemeToggle";

afterEach(() => {
  mockSetTheme.mockClear();
  mockUseTheme.mockReset();
});

describe("ThemeToggle", () => {
  it("exposes an accessible label", () => {
    mockUseTheme.mockReturnValue({ resolvedTheme: "dark", setTheme: mockSetTheme });

    render(<ThemeToggle />);

    expect(screen.getByRole("button", { name: "Toggle color theme" })).toBeInTheDocument();
  });

  it("switches to light when the resolved theme is dark", async () => {
    mockUseTheme.mockReturnValue({ resolvedTheme: "dark", setTheme: mockSetTheme });

    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button", { name: "Toggle color theme" }));

    expect(mockSetTheme).toHaveBeenCalledExactlyOnceWith("light");
  });

  it("switches to dark when the resolved theme is light", async () => {
    mockUseTheme.mockReturnValue({ resolvedTheme: "light", setTheme: mockSetTheme });

    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button", { name: "Toggle color theme" }));

    expect(mockSetTheme).toHaveBeenCalledExactlyOnceWith("dark");
  });
});
