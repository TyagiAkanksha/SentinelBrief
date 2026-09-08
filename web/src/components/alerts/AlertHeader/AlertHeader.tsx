import { formatAge, formatUtc } from "@/lib/format";

import type { AlertHeaderProps } from "./interface";

export function AlertHeader({ alert, now }: AlertHeaderProps) {
  return (
    <header>
      <h1 className="font-mono">{alert.src_ip}</h1>
      <dl>
        <dt>Sensor</dt>
        <dd>{alert.sensor}</dd>
        <dt>Source</dt>
        <dd>{alert.source}</dd>
        <dt>Status</dt>
        <dd>{alert.status}</dd>
        <dt>Event time</dt>
        <dd>
          <time dateTime={alert.event_time} title={formatUtc(alert.event_time)}>
            {formatUtc(alert.event_time)}
          </time>
        </dd>
        <dt>Received</dt>
        <dd>
          <time dateTime={alert.received_at} title={formatUtc(alert.received_at)}>
            {formatUtc(alert.received_at)} ({formatAge(alert.received_at, now)})
          </time>
        </dd>
      </dl>
    </header>
  );
}
