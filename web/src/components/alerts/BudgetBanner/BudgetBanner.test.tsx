// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { BudgetBanner } from "@/components/alerts/BudgetBanner";

// Pins the render contract (m8b task-05 brief: "a dumb banner component ... renders a visible
// warning when budgetExhausted is true and nothing when false"). `@/components/alerts/BudgetBanner`
// does not exist yet, so this fails RED today with a module-resolution error.
describe("BudgetBanner", () => {
  it("shows a visible warning when the daily token budget is exhausted", () => {
    render(<BudgetBanner budgetExhausted={true} />);

    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent(/budget/i);
  });

  it("renders nothing when the budget is not exhausted", () => {
    const { container } = render(<BudgetBanner budgetExhausted={false} />);

    expect(container.firstChild).toBeNull();
  });
});
