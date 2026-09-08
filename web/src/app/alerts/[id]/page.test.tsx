// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import type { AlertDetail } from "@/types/api";

const { NotFoundSentinel } = vi.hoisted(() => {
  class NotFoundSentinel extends Error {}
  return { NotFoundSentinel };
});

vi.mock("@/lib/api/server", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/server")>("@/lib/api/server");
  return {
    ...actual,
    getJson: vi.fn(),
  };
});

vi.mock("next/navigation", () => ({
  notFound: vi.fn(() => {
    throw new NotFoundSentinel("NEXT_NOT_FOUND");
  }),
}));

import AlertDetailPage from "@/app/alerts/[id]/page";
import { ApiError, getJson } from "@/lib/api/server";

const mockGetJson = vi.mocked(getJson);

function makeAlert(overrides: Partial<AlertDetail> = {}): AlertDetail {
  return {
    id: "44444444-4444-4444-8444-444444444444",
    source: "cowrie",
    src_ip: "203.0.113.9",
    sensor: "sensor-9",
    event_time: "2026-09-06T00:55:00.000Z",
    received_at: "2026-09-06T00:57:00.000Z",
    status: "triaged",
    raw: {},
    tool_calls: [],
    verdict: {
      id: "55555555-5555-4555-8555-555555555555",
      category: "scanning",
      confidence: 0.5,
      cost_usd: "0.000228",
      created_at: "2026-09-06T00:59:00.000Z",
      escalate: false,
      escalated_model: false,
      input_tokens: 100,
      latency_ms: 50,
      model_final: "gpt-5-nano",
      model_primary: "gpt-5-nano",
      output_tokens: 50,
      prompt_version: "triage-v1",
      reasoning: "reasoning text",
      recommended_action: "action text",
      severity: 2,
    },
    ...overrides,
  };
}

beforeEach(() => {
  mockGetJson.mockReset();
});

describe("AlertDetailPage", () => {
  it("calls notFound when the API answers 404", async () => {
    mockGetJson.mockRejectedValueOnce(new ApiError(404, null));

    await expect(
      AlertDetailPage({
        params: Promise.resolve({ id: "11111111-1111-4111-8111-111111111111" }),
      }),
    ).rejects.toBeInstanceOf(NotFoundSentinel);
  });

  it("calls notFound when the API answers 422 for a non-UUID id", async () => {
    mockGetJson.mockRejectedValueOnce(new ApiError(422, null));

    await expect(
      AlertDetailPage({ params: Promise.resolve({ id: "not-a-uuid" }) }),
    ).rejects.toBeInstanceOf(NotFoundSentinel);
  });

  it("renders the unreachable error state for a non-API error", async () => {
    mockGetJson.mockRejectedValueOnce(new Error("ECONNREFUSED"));

    const element = await AlertDetailPage({
      params: Promise.resolve({ id: "11111111-1111-4111-8111-111111111111" }),
    });

    render(element);

    expect(screen.getByRole("alert")).toHaveTextContent("API unreachable");
  });

  it("fetches exactly one detail with the id URL-encoded", async () => {
    mockGetJson.mockResolvedValueOnce(makeAlert());

    await AlertDetailPage({ params: Promise.resolve({ id: "a b" }) });

    expect(mockGetJson).toHaveBeenCalledTimes(1);
    expect(mockGetJson).toHaveBeenCalledWith("/api/v1/alerts/a%20b");
  });
});
