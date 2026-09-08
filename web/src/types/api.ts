import type { components, operations } from "@/types/generated/schema";

export type AlertSummary = components["schemas"]["AlertSummary"];
export type AlertDetail = components["schemas"]["AlertDetail"];
export type VerdictOut = components["schemas"]["VerdictOut"];
export type VerdictSummary = components["schemas"]["VerdictSummary"];
export type ToolCallOut = components["schemas"]["ToolCallOut"];
export type StatsOut = components["schemas"]["StatsOut"];
export type ErrorEnvelope = components["schemas"]["ErrorEnvelope"];
export type PaginatedAlerts = components["schemas"]["PaginatedResponse_AlertSummary_"];

export type VerdictCategory = VerdictOut["category"];
export type AlertStatus = AlertSummary["status"];

export type ListAlertsQuery = NonNullable<operations["list_alerts"]["parameters"]["query"]>;
