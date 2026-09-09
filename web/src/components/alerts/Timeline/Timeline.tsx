import { formatLatency } from "@/lib/format";

import type { TimelineProps } from "./interface";

export function Timeline({ toolCalls }: TimelineProps) {
  return (
    <ol aria-label="Tool trace">
      {toolCalls.length === 0 ? (
        <li>Tool trace arrives at M4</li>
      ) : (
        toolCalls.map((call) => (
          <li key={call.seq}>
            {call.seq}. {call.tool_name} · {formatLatency(call.latency_ms)}
          </li>
        ))
      )}
    </ol>
  );
}
