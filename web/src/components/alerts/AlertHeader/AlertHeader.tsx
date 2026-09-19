import type { ReactNode } from "react";

import { CountryFlag } from "@/components/ui/CountryFlag";
import { formatAge, formatUtc } from "@/lib/format";

import type { AlertHeaderProps } from "./interface";

/** One labelled meta item in the header's definition list. */
function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs font-medium tracking-wide text-faint uppercase">{label}</dt>
      <dd className="font-medium text-text">{children}</dd>
    </div>
  );
}

export function AlertHeader({ alert, now }: AlertHeaderProps) {
  return (
    <header className="border-b border-border pb-5">
      <h1 className="flex items-center gap-2 font-mono text-2xl font-semibold tracking-tight text-text">
        <CountryFlag code={alert.country} />
        {alert.src_ip}
      </h1>
      <dl className="mt-4 flex flex-wrap gap-x-10 gap-y-4 text-sm">
        <Meta label="Sensor">{alert.sensor}</Meta>
        <Meta label="Source">{alert.source}</Meta>
        <Meta label="Status">{alert.status}</Meta>
        <Meta label="Event time">
          <time dateTime={alert.event_time} title={formatUtc(alert.event_time)}>
            {formatUtc(alert.event_time)}
          </time>
        </Meta>
        <Meta label="Received">
          <time dateTime={alert.received_at} title={formatUtc(alert.received_at)}>
            {formatUtc(alert.received_at)} ({formatAge(alert.received_at, now)})
          </time>
        </Meta>
      </dl>
    </header>
  );
}
