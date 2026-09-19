// A hand-built, theme-aware architecture diagram: token-styled node boxes joined by inline-SVG
// arrows (no diagramming dependency). The whole graphic is one labelled image to assistive tech
// (`role="img"` + aria-label); the arrows are decorative, and the figcaption repeats the flow in
// prose for sighted readers.

const FLOW_LABEL =
  "Architecture flow: an isolated Cowrie SSH honeypot posts each finished session to the FastAPI " +
  "service; the API stores the raw session in PostgreSQL and enqueues a job in Redis; an ARQ " +
  "worker runs the enrichment tools and the model, writes the verdict, and the read-only Next.js " +
  "dashboard reads it back.";

function Node({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="w-40 shrink-0 rounded-(--radius) border border-border bg-bg px-3 py-2 text-center">
      <div className="text-sm font-medium text-text">{title}</div>
      <div className="mt-0.5 text-[11px] text-muted">{sub}</div>
    </div>
  );
}

function Arrow() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-5 shrink-0 text-faint"
    >
      <path d="M4 12h16m0 0-5-5m5 5-5 5" />
    </svg>
  );
}

export function ArchitectureDiagram() {
  return (
    <figure className="my-6">
      <div className="overflow-x-auto rounded-(--radius) border border-border bg-surface p-4">
        <div role="img" aria-label={FLOW_LABEL} className="flex min-w-max items-center gap-3">
          <Node title="Cowrie SSH honeypot" sub="isolated host" />
          <Arrow />
          <Node title="API" sub="FastAPI" />
          <Arrow />
          <div className="flex shrink-0 flex-col gap-2">
            <Node title="Redis" sub="job queue" />
            <Node title="PostgreSQL" sub="sessions + verdicts" />
            <Node title="ARQ worker" sub="tools + LLM" />
          </div>
          <Arrow />
          <Node title="Next.js dashboard" sub="read-only" />
        </div>
      </div>
      <figcaption className="mt-2 max-w-prose text-sm text-muted">
        An isolated honeypot host posts each finished SSH session to the app host, where the API
        stores it and queues it; a worker calls the model and its tools, writes the verdict, and the
        dashboard reads the database.
      </figcaption>
    </figure>
  );
}
