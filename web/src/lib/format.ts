const EM_DASH = "—";

export const COUNTRY_CODE_RE = /^[A-Z]{2}$/;

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
  if (value === null || value.trim() === "") {
    return EM_DASH;
  }
  const parsed = Number(value);
  if (Number.isNaN(parsed)) {
    return EM_DASH;
  }
  return `$${parsed.toFixed(6)}`;
}

export function formatPercent(confidence: number): string {
  if (!Number.isFinite(confidence)) {
    return EM_DASH;
  }
  return `${Math.round(confidence * 100)}%`;
}

export function formatLatency(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) {
    return EM_DASH;
  }
  return `${ms.toLocaleString("en-US")} ms`;
}

export function formatCount(n: number | null): string {
  if (n === null || !Number.isFinite(n)) {
    return EM_DASH;
  }
  return n.toLocaleString("en-US");
}

export function formatTokens(n: number | null): string {
  return formatCount(n);
}

// Maps an ISO 3166-1 alpha-2 code to its regional-indicator-symbol flag emoji; "" for anything
// else (missing, wrong length, or not uppercase A-Z) — the caller renders nothing in that case.
export function countryFlag(code: string | null | undefined): string {
  if (code === null || code === undefined || !COUNTRY_CODE_RE.test(code)) {
    return "";
  }
  return String.fromCodePoint(0x1f1e6 + code.charCodeAt(0) - 65, 0x1f1e6 + code.charCodeAt(1) - 65);
}

// HTML's <input type="datetime-local"> rejects any value carrying a zone suffix (the browser
// blanks it), and the API reads a naive `since` as UTC (task-01 ruling Q7). This normalizes any
// ISO input — zoned or naive — to the UTC wall clock in datetime-local's own format, so a naive
// value round-trips unchanged and a zoned value is converted rather than dropped.
export function formatDatetimeLocalUtc(iso: string): string {
  const hasZoneDesignator = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso);
  // A time part is marked by "T" (ISO) or a plain space (e.g. "2026-09-01 00:00:00"); either
  // spelling needs the UTC-as-naive handling below, not just the ISO-standard "T" form.
  const hasTimePart = /[T ]/.test(iso);
  const normalized = hasZoneDesignator || !hasTimePart ? iso : `${iso}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}
