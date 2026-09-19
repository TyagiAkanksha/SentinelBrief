import type { JSX } from "react";

import { BudgetBanner } from "@/components/alerts/BudgetBanner";
import { CostTable } from "@/components/stats/CostTable";
import { DistributionTable } from "@/components/stats/DistributionTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Stat } from "@/components/ui/Stat";
import { ApiError, getJson } from "@/lib/api/server";
import {
  formatCount,
  formatLatency,
  formatPercent,
  formatTokens,
  formatUsd,
  formatUtc,
} from "@/lib/format";
import { categoryRows, escalationRate, severityRows, triagedCount, volumeRows } from "@/lib/stats";
import type { StatsOut } from "@/types/api";

export const dynamic = "force-dynamic";

export const metadata = { title: "Stats — SentinelBrief" };

export default async function StatsPage(): Promise<JSX.Element> {
  let stats: StatsOut | null = null;
  let error: Error | null = null;
  try {
    stats = await getJson<StatsOut>("/api/v1/stats");
  } catch (e) {
    error = e instanceof Error ? e : new Error(String(e));
  }

  if (error !== null) {
    return error instanceof ApiError ? (
      <ErrorState
        title={`API error ${error.status}`}
        detail={error.envelope?.error.message ?? error.message}
      />
    ) : (
      <ErrorState title="API unreachable" detail={error.message} />
    );
  }

  if (stats === null || stats.total_alerts === 0) {
    return <EmptyState message="No alerts yet — run scripts/seed_dev.py" />;
  }

  const triaged = triagedCount(stats);

  // Daily token-budget circuit breaker (PRD §10.3 / M8b): 0 means no cap. The hint surfaces the
  // breaker so the stats grid showcases it alongside the eight-card layout.
  const budgetUncapped = stats.daily_token_budget === 0;
  const budgetValue = budgetUncapped
    ? "Unlimited"
    : `${formatTokens(stats.tokens_today)} / ${formatTokens(stats.daily_token_budget)}`;
  const budgetHint = budgetUncapped
    ? "No daily cap configured"
    : stats.budget_exhausted
      ? "Exhausted — triage paused"
      : "Tokens used today";

  return (
    <section className="space-y-6">
      <PageHeader title="Stats" subtitle="Live triage metrics — updated as new sessions arrive" />
      <BudgetBanner budgetExhausted={stats.budget_exhausted ?? false} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Total alerts" value={formatCount(stats.total_alerts)} />
        <Stat label="Triaged" value={formatCount(triaged)} />
        <Stat
          label="Escalation rate"
          value={formatPercent(escalationRate(stats))}
          hint={`${stats.escalated_count} of ${triaged} triaged`}
        />
        <Stat
          label="Mean cost per alert"
          value={formatUsd(stats.cost_mean_usd)}
          hint={`Total ${formatUsd(stats.cost_total_usd)}`}
        />
        <Stat label="Latency p50" value={formatLatency(stats.latency_p50_ms)} />
        <Stat label="Latency p95" value={formatLatency(stats.latency_p95_ms)} />
        <Stat
          label="Last alert"
          value={stats.last_alert_at === null ? "—" : formatUtc(stats.last_alert_at)}
        />
        <Stat label="Daily budget" value={budgetValue} hint={budgetHint} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <DistributionTable
          caption="Alerts per day"
          labelHeader="Day"
          rows={volumeRows(stats)}
          emptyMessage="No volume data yet"
        />
        <DistributionTable
          caption="Severity distribution"
          labelHeader="Severity"
          rows={severityRows(stats)}
          emptyMessage="No severity data yet"
        />
        <DistributionTable
          caption="Category distribution"
          labelHeader="Category"
          rows={categoryRows(stats)}
          emptyMessage="No category data yet"
        />
        <CostTable rows={stats.cost_by_day} emptyMessage="No cost data yet" />
      </div>
    </section>
  );
}
