import Link from "next/link";

import { Badge } from "@/components/ui/Badge";
import type { Severity } from "@/components/ui/Badge";
import { formatAge, formatUtc } from "@/lib/format";

import type { AlertRowProps } from "./interface";

export function AlertRow({ alert, now }: AlertRowProps) {
  return (
    <tr>
      <td>
        {alert.verdict ? (
          <Badge severity={alert.verdict.severity as Severity} />
        ) : (
          <span className="text-muted">{alert.status}</span>
        )}
      </td>
      <td>{alert.verdict ? <Badge category={alert.verdict.category} /> : "—"}</td>
      <td>
        <Link href={`/alerts/${alert.id}`} className="font-mono text-accent">
          {alert.src_ip}
        </Link>
      </td>
      <td>{alert.sensor}</td>
      <td>
        {alert.verdict
          ? alert.verdict.reasoning_excerpt
          : alert.status === "pending"
            ? "awaiting triage"
            : "triage failed — no verdict"}
      </td>
      <td>
        <time dateTime={alert.received_at} title={formatUtc(alert.received_at)}>
          {formatAge(alert.received_at, now)}
        </time>
      </td>
    </tr>
  );
}
