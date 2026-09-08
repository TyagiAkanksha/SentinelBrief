import { formatLatency, formatTokens, formatUsd, formatUtc } from "@/lib/format";

import type { RoutingInfoProps } from "./interface";

export function RoutingInfo({ verdict }: RoutingInfoProps) {
  return (
    <section aria-labelledby="routing-heading">
      <h2 id="routing-heading">Routing</h2>
      <dl className="tabular-nums">
        <dt>Primary model</dt>
        <dd>{verdict.model_primary}</dd>
        <dt>Final model</dt>
        <dd>{verdict.model_final}</dd>
        <dt>Escalated to strong model</dt>
        <dd>{verdict.escalated_model ? "yes" : "no"}</dd>
        <dt>Prompt version</dt>
        <dd>{verdict.prompt_version}</dd>
        <dt>Input tokens</dt>
        <dd>{formatTokens(verdict.input_tokens)}</dd>
        <dt>Output tokens</dt>
        <dd>{formatTokens(verdict.output_tokens)}</dd>
        <dt>Cost</dt>
        <dd>{formatUsd(verdict.cost_usd)}</dd>
        <dt>Latency</dt>
        <dd>{formatLatency(verdict.latency_ms)}</dd>
        <dt>Verdict at</dt>
        <dd>{formatUtc(verdict.created_at)}</dd>
      </dl>
    </section>
  );
}
