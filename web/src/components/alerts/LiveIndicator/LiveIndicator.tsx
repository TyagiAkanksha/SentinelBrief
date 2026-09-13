import type { StreamStatus } from "@/hooks/useAlertStream";

import type { LiveIndicatorProps } from "./interface";

const LABEL_BY_STATUS: Record<StreamStatus, string> = {
  connecting: "Connecting…",
  live: "Live",
  polling: "Polling every 30s",
};

// Colour is never the only signal (docs/FRONTEND-CONVENTIONS.md §9): the text always names the
// state; these token classes are a secondary cue.
const COLOR_CLASS_BY_STATUS: Record<StreamStatus, string> = {
  connecting: "text-muted",
  live: "text-sev-2",
  polling: "text-sev-3",
};

export function LiveIndicator({ status, updates }: LiveIndicatorProps) {
  const label = LABEL_BY_STATUS[status];
  const suffix = updates > 0 ? ` · ${updates} update(s)` : "";

  return (
    <p role="status" aria-live="polite" className={`text-sm ${COLOR_CLASS_BY_STATUS[status]}`}>
      {label}
      {suffix}
    </p>
  );
}
