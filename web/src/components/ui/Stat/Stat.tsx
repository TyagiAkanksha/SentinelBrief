import type { StatProps } from "./interface";

export function Stat({ label, value, hint }: StatProps) {
  return (
    <div className="min-h-24 rounded-(--radius) border border-border bg-surface p-4">
      <span className="block text-xs font-medium tracking-wide text-muted uppercase">{label}</span>
      <span className="mt-1 block text-2xl font-semibold tabular-nums text-text">{value}</span>
      {hint !== undefined ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
    </div>
  );
}
