import { CodeBlock } from "@/components/ui/CodeBlock";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatLatency } from "@/lib/format";

import type { TimelineProps } from "./interface";

export function Timeline({ toolCalls }: TimelineProps) {
  return (
    <section aria-labelledby="trace-heading">
      <h2 id="trace-heading" className="font-display text-lg font-semibold text-text">
        Tool trace
      </h2>
      <ol aria-label="Tool trace" className="mt-4 space-y-3">
        {toolCalls.length === 0 ? (
          <li>
            <EmptyState message="No tool calls were made." />
          </li>
        ) : (
          toolCalls.map((call) => {
            const unavailable = call.result["unavailable"] === true;
            const reason = String(call.result["reason"] ?? "unknown");
            return (
              <li key={call.seq} className="rounded-(--radius) border border-border bg-surface p-4">
                <p className="flex flex-wrap items-center gap-x-2 font-mono text-sm">
                  <span className="font-medium text-text">
                    {call.seq}. {call.tool_name}
                  </span>
                  <span className="text-muted">· {formatLatency(call.latency_ms)}</span>
                  {unavailable ? (
                    <span className="text-sev-3">· unavailable ({reason})</span>
                  ) : null}
                </p>
                <div className="mt-3">
                  <span className="mb-1.5 block text-xs font-medium tracking-wide text-faint uppercase">
                    Arguments
                  </span>
                  <CodeBlock text={JSON.stringify(call.arguments, null, 2)} />
                </div>
                <div className="mt-3">
                  <span className="mb-1.5 block text-xs font-medium tracking-wide text-faint uppercase">
                    Result
                  </span>
                  <CodeBlock text={JSON.stringify(call.result, null, 2)} />
                </div>
              </li>
            );
          })
        )}
      </ol>
    </section>
  );
}
