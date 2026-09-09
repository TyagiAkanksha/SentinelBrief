import { Badge } from "@/components/ui/Badge";
import type { Severity } from "@/components/ui/Badge";
import { formatPercent } from "@/lib/format";

import type { VerdictPanelProps } from "./interface";

export function VerdictPanel({ verdict }: VerdictPanelProps) {
  return (
    <section aria-labelledby="verdict-heading">
      <h2 id="verdict-heading">Verdict</h2>
      <Badge severity={verdict.severity as Severity} />
      <Badge category={verdict.category} />
      <span>Confidence {formatPercent(verdict.confidence)}</span>
      <span>{verdict.escalate ? "Escalate: yes" : "Escalate: no"}</span>
      <h3>Reasoning</h3>
      <p className="whitespace-pre-wrap">{verdict.reasoning}</p>
      <h3>Recommended action</h3>
      <p className="whitespace-pre-wrap">{verdict.recommended_action}</p>
    </section>
  );
}
