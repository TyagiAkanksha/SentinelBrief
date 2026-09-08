const EM_DASH = "—";

export function formatUtc(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return EM_DASH;
  }
  return `${date.toISOString().slice(0, 10)} ${date.toISOString().slice(11, 19)}Z`;
}

export function formatAge(iso: string, now: Date): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return EM_DASH;
  }

  const diffMs = now.getTime() - date.getTime();
  if (diffMs < 0) {
    return EM_DASH;
  }

  const diffSeconds = Math.floor(diffMs / 1000);
  if (diffSeconds < 60) {
    return "<1m ago";
  }

  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) {
    return `${diffMinutes}m ago`;
  }

  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) {
    return `${diffHours}h ago`;
  }

  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

export function formatUsd(value: string | null): string {
  if (value === null) {
    return EM_DASH;
  }
  const parsed = Number(value);
  if (Number.isNaN(parsed)) {
    return EM_DASH;
  }
  return `$${parsed.toFixed(6)}`;
}

export function formatPercent(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

export function formatLatency(ms: number | null): string {
  if (ms === null) {
    return EM_DASH;
  }
  return `${ms.toLocaleString("en-US")} ms`;
}

export function formatTokens(n: number | null): string {
  if (n === null) {
    return EM_DASH;
  }
  return n.toLocaleString("en-US");
}
