"use client";

import { useRouter } from "next/navigation";
import { useCallback } from "react";

import { LiveIndicator } from "@/components/alerts/LiveIndicator";
import { useAlertStream } from "@/hooks/useAlertStream";
import { streamUrl } from "@/lib/api/browser";

/**
 * The queue page's one client island: opens the SSE stream and calls `router.refresh()` on
 * every event/poll tick so the Server Component re-reads the database (m5 task-04 review M4 —
 * the SSE payload is a hint, never the rendered data). Renders `LiveIndicator` for the current
 * stream status.
 */
export function AlertStreamRefresher() {
  const router = useRouter();
  const onUpdate = useCallback(() => {
    router.refresh();
  }, [router]);
  const state = useAlertStream({ url: streamUrl(), onUpdate });

  return <LiveIndicator status={state.status} updates={state.updates} />;
}
