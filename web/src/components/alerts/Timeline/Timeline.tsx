import { CodeBlock } from "@/components/ui/CodeBlock";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatLatency } from "@/lib/format";

import type { TimelineProps } from "./interface";

export function Timeline({ toolCalls }: TimelineProps) {
  return (
    <ol aria-label="Tool trace">
      {toolCalls.length === 0 ? (
        <li>
          <EmptyState message="No tool calls were made." />
        </li>
      ) : (
        toolCalls.map((call) => {
          const unavailable = call.result["unavailable"] === true;
          const reason = String(call.result["reason"] ?? "unknown");
          return (
            <li key={call.seq}>
              <p>
                {call.seq}. {call.tool_name} · {formatLatency(call.latency_ms)}
                {unavailable ? ` · unavailable (${reason})` : null}
              </p>
              <div>
                <span>Arguments</span>
                <CodeBlock text={JSON.stringify(call.arguments, null, 2)} />
              </div>
              <div>
                <span>Result</span>
                <CodeBlock text={JSON.stringify(call.result, null, 2)} />
              </div>
            </li>
          );
        })
      )}
    </ol>
  );
}
