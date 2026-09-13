import type { JSX } from "react";
import Link from "next/link";

import { ArchitectureDiagram } from "@/components/about/ArchitectureDiagram";
import { PRD_URL, REPO_URL, RESULTS_URL } from "@/lib/site";

export const metadata = { title: "About — SentinelBrief" };

export default function AboutPage(): JSX.Element {
  return (
    <section>
      <h1 className="text-lg font-semibold">About SentinelBrief</h1>
      <div className="max-w-prose space-y-3">
        <p>
          SentinelBrief triages honeypot alerts with a large language model. A Cowrie SSH honeypot
          on an isolated host records every session an attacker opens against it, a shipper posts
          each finished session here, and a worker asks a model to judge it: how severe, what kind
          of activity, how confident, and what a person should do about it. Every verdict carries
          written reasoning and the evidence it rests on. The human always decides. Nothing here
          blocks an address, isolates a host, or answers an attacker.
        </p>
        <p>
          One alert is one honeypot session. The ingest endpoint verifies a signature, stores the
          raw session and returns in well under a second; all model work happens in a background
          worker, so no page view and no public request ever spends a token. Before deciding, the
          model may call enrichment tools: the session&apos;s commands, the target host&apos;s role,
          the source address&apos;s geography and reputation, and that address&apos;s history
          against this honeypot. Every tool call is recorded, so the trace shown on an alert&apos;s
          page is the real one. A second, stronger model re-judges the cases the first rates severe
          or is unsure about.
        </p>
        <p>
          This is a portfolio project, built to be measured rather than demoed. Accuracy, cost and
          latency are scored against a labelled set of sessions whenever the prompt or the model
          changes, and the numbers are published — including the runs that came out worse than the
          ones before them. The dashboard runs on a single host behind Caddy; the honeypot lives in
          its own network with no shared credentials, on the assumption that it will be fully
          compromised. That is its job.
        </p>
      </div>
      <ArchitectureDiagram />
      <nav aria-label="Project links">
        <ul>
          <li>
            <a
              href={RESULTS_URL}
              target="_blank"
              rel="noreferrer"
              className="text-accent underline"
            >
              Evaluation results
            </a>
          </li>
          <li>
            <a href={REPO_URL} target="_blank" rel="noreferrer" className="text-accent underline">
              Source code
            </a>
          </li>
          <li>
            <a href={PRD_URL} target="_blank" rel="noreferrer" className="text-accent underline">
              Product spec
            </a>
          </li>
          <li>
            <Link href="/alerts" className="text-accent underline">
              Live alert queue
            </Link>
          </li>
        </ul>
      </nav>
    </section>
  );
}
