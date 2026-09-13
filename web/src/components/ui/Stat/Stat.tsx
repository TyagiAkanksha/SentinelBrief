import type { StatProps } from "./interface";

export function Stat({ label, value, hint }: StatProps) {
  return (
    <div className="rounded border border-border bg-surface p-3">
      <span className="block text-xs text-muted">{label}</span>
      <span className="block text-2xl font-semibold tabular-nums">{value}</span>
      {hint !== undefined ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}
