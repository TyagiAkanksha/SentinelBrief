import { Badge } from "@/components/ui/Badge";
import type { Severity } from "@/components/ui/Badge";
import { formatPercent } from "@/lib/format";

import type { VerdictPanelProps } from "./interface";

export function VerdictPanel({ verdict }: VerdictPanelProps) {
  return (
    <section
      aria-labelledby="verdict-heading"
      className="rounded-(--radius) border border-border bg-surface p-5"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 id="verdict-heading" className="font-display text-lg font-semibold text-text">
          Verdict
        </h2>
        <Badge severity={verdict.severity as Severity} />
        <Badge category={verdict.category} />
        <span className="text-sm text-muted">Confidence {formatPercent(verdict.confidence)}</span>
        <span className="text-sm text-muted">
          {verdict.escalate ? "Escalate: yes" : "Escalate: no"}
        </span>
      </div>

      <h3 className="mt-5 text-xs font-medium tracking-wide text-faint uppercase">Reasoning</h3>
      <p className="mt-1.5 leading-relaxed whitespace-pre-wrap text-text">{verdict.reasoning}</p>

      <h3 className="mt-5 text-xs font-medium tracking-wide text-faint uppercase">
        Recommended action
      </h3>
      <p className="mt-1.5 leading-relaxed whitespace-pre-wrap text-text">
        {verdict.recommended_action}
      </p>
    </section>
  );
}
