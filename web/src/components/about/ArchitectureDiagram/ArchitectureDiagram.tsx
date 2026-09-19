// Verbatim from the task brief: the ASCII art is the spec, never re-drawn or reflowed here.
const DIAGRAM = `  Honeypot host (isolated VPC)          App host (one box, behind Caddy)
 +---------------------------+        +-----------------------------------+
 |  Cowrie SSH honeypot      |        |   FastAPI  --enqueue-->  Redis     |
 |         |                 | HMAC   |      |                     |      |
 |         v                 | HTTPS  |      | raw session         | job  |
 |  log shipper  ------------|------->|      v                     v      |
 +---------------------------+  POST  |  PostgreSQL <--verdict--  ARQ     |
                                      |      |                    worker  |
   assume it is compromised           |      | read-only        (tools +  |
   -- that is its job                 |      v                   the LLM) |
                                      |  Next.js dashboard  --> public    |
                                      +-----------------------------------+`;

export function ArchitectureDiagram() {
  return (
    <figure className="my-6">
      <div className="overflow-x-auto rounded-(--radius) border border-border bg-surface p-4">
        {/* aria-hidden: the art is decorative — the figcaption carries the meaning in prose. */}
        <pre aria-hidden="true" className="font-mono text-xs leading-tight text-muted">
          {DIAGRAM}
        </pre>
      </div>
      <figcaption className="mt-2 max-w-prose text-sm text-muted">
        An isolated honeypot host posts each finished SSH session to the app host, where the API
        stores it and queues it; a worker calls the model and its tools, writes the verdict, and the
        dashboard reads the database.
      </figcaption>
    </figure>
  );
}
