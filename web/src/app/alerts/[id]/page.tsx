import type { JSX } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AlertHeader } from "@/components/alerts/AlertHeader";
import { RawJson } from "@/components/alerts/RawJson";
import { RoutingInfo } from "@/components/alerts/RoutingInfo";
import { StatusBanner } from "@/components/alerts/StatusBanner";
import { Timeline } from "@/components/alerts/Timeline";
import { VerdictPanel } from "@/components/alerts/VerdictPanel";
import { ErrorState } from "@/components/ui/ErrorState";
import { ApiError, getJson } from "@/lib/api/server";
import type { AlertDetail } from "@/types/api";

export const dynamic = "force-dynamic";

export default async function AlertDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<JSX.Element> {
  const { id } = await params;

  let alert: AlertDetail;
  try {
    alert = await getJson<AlertDetail>(`/api/v1/alerts/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) {
      notFound();
    }
    const err = e instanceof Error ? e : new Error(String(e));
    return (
      <ErrorState
        title={e instanceof ApiError ? `API error ${e.status}` : "API unreachable"}
        detail={err.message}
      />
    );
  }

  return (
    <article className="space-y-6">
      <Link
        href="/alerts"
        className="inline-flex items-center gap-1 text-sm text-accent hover:text-accent-strong"
      >
        ← Alert queue
      </Link>
      <AlertHeader alert={alert} now={new Date()} />
      {alert.verdict ? (
        <>
          <VerdictPanel verdict={alert.verdict} />
          <RoutingInfo verdict={alert.verdict} />
        </>
      ) : (
        <StatusBanner status={alert.status === "failed" ? "failed" : "pending"} />
      )}
      <Timeline toolCalls={alert.tool_calls} />
      <RawJson raw={alert.raw} />
    </article>
  );
}
