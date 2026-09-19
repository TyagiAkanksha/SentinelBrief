import Link from "next/link";

import { Badge } from "@/components/ui/Badge";
import type { Severity } from "@/components/ui/Badge";
import { CountryFlag } from "@/components/ui/CountryFlag";
import { formatAge, formatUtc } from "@/lib/format";

import type { AlertRowProps } from "./interface";

export function AlertRow({ alert, now }: AlertRowProps) {
  return (
    <tr className="align-top">
      <td>
        {alert.verdict ? (
          <Badge severity={alert.verdict.severity as Severity} />
        ) : (
          <span className="text-muted">{alert.status}</span>
        )}
      </td>
      <td>{alert.verdict ? <Badge category={alert.verdict.category} /> : "—"}</td>
      <td className="whitespace-nowrap">
        <span className="inline-flex items-center gap-1.5">
          <CountryFlag code={alert.country} />
          <Link
            href={`/alerts/${alert.id}`}
            className="font-mono text-accent underline decoration-transparent underline-offset-2 transition-colors hover:decoration-current"
          >
            {alert.src_ip}
          </Link>
        </span>
      </td>
      <td className="text-muted">{alert.sensor}</td>
      <td className="text-muted">
        <span className="line-clamp-2">
          {alert.verdict
            ? alert.verdict.reasoning_excerpt
            : alert.status === "pending"
              ? "awaiting triage"
              : "triage failed — no verdict"}
        </span>
      </td>
      <td className="whitespace-nowrap text-right text-muted tabular-nums">
        <time dateTime={alert.received_at} title={formatUtc(alert.received_at)}>
          {formatAge(alert.received_at, now)}
        </time>
      </td>
    </tr>
  );
}
