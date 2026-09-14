import { POLL_INTERVAL_MS } from "@/lib/stream";
import type { StreamStatus } from "@/lib/stream";

import type { LiveIndicatorProps } from "./interface";

// A function, not a module-level Record: the "polling" branch derives its label from
// POLL_INTERVAL_MS rather than hardcoding "30s" (review M7).
function labelForStatus(status: StreamStatus): string {
  switch (status) {
    case "connecting":
      return "Connecting…";
    case "live":
      return "Live";
    case "polling":
      return `Polling every ${POLL_INTERVAL_MS / 1000}s`;
  }
}

// Colour is never the only signal (docs/FRONTEND-CONVENTIONS.md §9): the text always names the
// state; these token classes are a secondary cue.
const COLOR_CLASS_BY_STATUS: Record<StreamStatus, string> = {
  connecting: "text-muted",
  live: "text-sev-2",
  polling: "text-sev-3",
};

export function LiveIndicator({ status, updates }: LiveIndicatorProps) {
  const label = labelForStatus(status);
  const suffix = updates > 0 ? ` · ${updates} update(s)` : "";

  return (
    <p role="status" aria-live="polite" className={`text-sm ${COLOR_CLASS_BY_STATUS[status]}`}>
      {label}
      {suffix}
    </p>
  );
}
