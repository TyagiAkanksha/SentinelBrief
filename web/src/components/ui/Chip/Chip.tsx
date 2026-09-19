import type { ChipProps } from "./interface";

/** A small token-styled tag (the About tech-stack list). Dumb: renders its children, no logic. */
export function Chip({ children }: ChipProps) {
  return (
    <span className="inline-flex items-center rounded-full border border-border bg-surface-2 px-2 py-0.5 text-xs text-muted">
      {children}
    </span>
  );
}
