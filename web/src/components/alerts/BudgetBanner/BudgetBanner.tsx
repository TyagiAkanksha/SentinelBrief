import type { BudgetBannerProps } from "./interface";

// A dumb component: props in, nothing out (docs/FRONTEND-CONVENTIONS.md §3). Renders a visible
// warning when the daily token-budget circuit breaker (PRD §10.3, from M8) has paused triage;
// renders nothing otherwise (m8b task-05 brief).
export function BudgetBanner({ budgetExhausted }: BudgetBannerProps) {
  if (!budgetExhausted) {
    return null;
  }

  return (
    <p
      role="alert"
      className="rounded-(--radius) border border-sev-4/40 bg-sev-4/10 px-3 py-2 text-sm font-medium text-sev-4"
    >
      Daily token budget exhausted — triage is paused until the counter resets.
    </p>
  );
}
