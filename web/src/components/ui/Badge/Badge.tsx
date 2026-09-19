import type { BadgeProps, Severity } from "./interface";

const PILL = "inline-block rounded-full border px-2 py-0.5 font-mono text-xs font-medium";

const SEVERITY_CLASSES: Record<Severity, string> = {
  1: "border-sev-1/40 bg-sev-1/15 text-sev-1",
  2: "border-sev-2/40 bg-sev-2/15 text-sev-2",
  3: "border-sev-3/40 bg-sev-3/15 text-sev-3",
  4: "border-sev-4/40 bg-sev-4/15 text-sev-4",
  5: "border-sev-5/40 bg-sev-5/15 text-sev-5",
};

const CATEGORY_CLASSES = "border-border bg-surface-2 text-muted";

export function Badge(props: BadgeProps) {
  if ("severity" in props) {
    const { severity } = props;
    const label = `Severity ${severity}`;
    return (
      <span className={`${PILL} ${SEVERITY_CLASSES[severity]}`} aria-label={label} title={label}>
        {`S${severity}`}
      </span>
    );
  }

  return <span className={`${PILL} ${CATEGORY_CLASSES}`}>{props.category}</span>;
}
