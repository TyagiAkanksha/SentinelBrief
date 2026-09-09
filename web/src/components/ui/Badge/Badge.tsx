import type { BadgeProps, Severity } from "./interface";

const SEVERITY_CLASSES: Record<Severity, string> = {
  1: "border-sev-1 bg-sev-1/20 text-sev-1",
  2: "border-sev-2 bg-sev-2/20 text-sev-2",
  3: "border-sev-3 bg-sev-3/20 text-sev-3",
  4: "border-sev-4 bg-sev-4/20 text-sev-4",
  5: "border-sev-5 bg-sev-5/20 text-sev-5",
};

const CATEGORY_CLASSES = "border-border bg-surface text-muted";

export function Badge(props: BadgeProps) {
  if ("severity" in props) {
    const { severity } = props;
    const label = `Severity ${severity}`;
    return (
      <span
        className={`inline-block rounded border px-1.5 font-mono text-xs ${SEVERITY_CLASSES[severity]}`}
        aria-label={label}
        title={label}
      >
        {`S${severity}`}
      </span>
    );
  }

  return (
    <span className={`inline-block rounded border px-1.5 font-mono text-xs ${CATEGORY_CLASSES}`}>
      {props.category}
    </span>
  );
}
