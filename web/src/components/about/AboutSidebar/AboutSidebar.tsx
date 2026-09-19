import { ButtonLink } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { PRD_URL, REPO_URL, RESULTS_URL } from "@/lib/site";

// The recruiter-facing surfaces (results + code) lead as primary buttons; the spec and the live
// queue follow as ghost buttons.
const TECH_STACK = ["FastAPI", "Postgres", "Redis", "ARQ", "Next.js", "Caddy", "OpenAI"] as const;

export function AboutSidebar() {
  return (
    <aside className="space-y-6">
      <div className="space-y-3 rounded-(--radius) border border-border bg-surface p-4">
        <h2 className="text-xs font-medium tracking-wide text-muted uppercase">Project links</h2>
        <div className="flex flex-col items-start gap-2">
          <ButtonLink href={RESULTS_URL} external variant="primary">
            Evaluation results
          </ButtonLink>
          <ButtonLink href={REPO_URL} external variant="primary">
            Source code
          </ButtonLink>
          <ButtonLink href={PRD_URL} external variant="ghost">
            Product spec
          </ButtonLink>
          <ButtonLink href="/alerts" variant="ghost">
            Live alert queue
          </ButtonLink>
        </div>
      </div>
      <div className="space-y-3 rounded-(--radius) border border-border bg-surface p-4">
        <h2 className="text-xs font-medium tracking-wide text-muted uppercase">
          At a glance — tech stack
        </h2>
        <ul className="flex flex-wrap gap-2">
          {TECH_STACK.map((tech) => (
            <li key={tech}>
              <Chip>{tech}</Chip>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
