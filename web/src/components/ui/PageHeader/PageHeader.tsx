import type { PageHeaderProps } from "./interface";

/**
 * Shared page masthead: a display-font H1, an optional muted subtitle, and an optional right
 * aligned `actions` slot (the /alerts live indicator lives here). Dumb: props in, no logic.
 */
export function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-x-6 gap-y-2 border-b border-border pb-4">
      <div>
        <h1 className="font-display text-2xl font-semibold tracking-tight text-text">{title}</h1>
        {subtitle !== undefined ? <p className="mt-1 text-sm text-muted">{subtitle}</p> : null}
      </div>
      {actions !== undefined ? (
        <div className="flex shrink-0 items-center gap-3">{actions}</div>
      ) : null}
    </div>
  );
}
