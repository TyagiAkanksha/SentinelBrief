import type { ReactNode } from "react";

import { formatLatency, formatTokens, formatUsd, formatUtc } from "@/lib/format";

import type { RoutingInfoProps } from "./interface";

/** One label/value pair on a shared baseline rule (keeps the grid columns legible). */
function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border/60 pb-2">
      <dt className="text-muted">{label}</dt>
      <dd className="font-medium text-text">{children}</dd>
    </div>
  );
}

export function RoutingInfo({ verdict }: RoutingInfoProps) {
  return (
    <section
      aria-labelledby="routing-heading"
      className="rounded-(--radius) border border-border bg-surface p-5"
    >
      <h2 id="routing-heading" className="font-display text-lg font-semibold text-text">
        Routing
      </h2>
      <dl className="mt-4 grid grid-cols-1 gap-x-10 gap-y-3 text-sm tabular-nums sm:grid-cols-2 lg:grid-cols-3">
        <Row label="Primary model">{verdict.model_primary}</Row>
        <Row label="Final model">{verdict.model_final}</Row>
        <Row label="Escalated to strong model">{verdict.escalated_model ? "yes" : "no"}</Row>
        <Row label="Prompt version">{verdict.prompt_version}</Row>
        <Row label="Input tokens">{formatTokens(verdict.input_tokens)}</Row>
        <Row label="Output tokens">{formatTokens(verdict.output_tokens)}</Row>
        <Row label="Cost">{formatUsd(verdict.cost_usd)}</Row>
        <Row label="Latency">{formatLatency(verdict.latency_ms)}</Row>
        <Row label="Verdict at">{formatUtc(verdict.created_at)}</Row>
      </dl>
    </section>
  );
}
