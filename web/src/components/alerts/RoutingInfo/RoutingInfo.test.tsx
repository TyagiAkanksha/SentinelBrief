// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { RoutingInfo } from "@/components/alerts/RoutingInfo";
import type { VerdictOut } from "@/types/api";

const EM_DASH = "—";

function makeVerdict(overrides: Partial<VerdictOut> = {}): VerdictOut {
  return {
    id: "33333333-3333-4333-8333-333333333333",
    category: "scanning",
    confidence: 0.5,
    cost_usd: "0.000228",
    created_at: "2026-09-06T00:59:00.000Z",
    escalate: false,
    escalated_model: false,
    input_tokens: 1000,
    latency_ms: 1234,
    model_final: "gpt-5-nano",
    model_primary: "gpt-5-nano",
    output_tokens: 1000,
    prompt_version: "triage-v1",
    reasoning: "reasoning text",
    recommended_action: "action text",
    severity: 2,
    ...overrides,
  };
}

describe("RoutingInfo", () => {
  it("renders the nine routing fields in a definition list", () => {
    const verdict = makeVerdict({
      model_primary: "gpt-5-nano",
      model_final: "gpt-5-nano",
      prompt_version: "triage-v1",
    });

    const { container } = render(<RoutingInfo verdict={verdict} />);

    expect(container.querySelectorAll("dt")).toHaveLength(9);
    expect(screen.getAllByText("gpt-5-nano").length).toBeGreaterThan(0);
    expect(screen.getByText("triage-v1")).toBeInTheDocument();
  });

  it("formats cost from the decimal string and latency with a unit", () => {
    const verdict = makeVerdict({
      cost_usd: "0.000228",
      latency_ms: 1234,
      input_tokens: 1000,
      output_tokens: 1000,
    });

    render(<RoutingInfo verdict={verdict} />);

    expect(screen.getByText("$0.000228")).toBeInTheDocument();
    expect(screen.getByText("1,234 ms")).toBeInTheDocument();
    expect(screen.getAllByText("1,000").length).toBeGreaterThan(0);
  });

  it("renders escalated_model as yes/no and null tokens, cost and latency as —", () => {
    const verdict = makeVerdict({
      escalated_model: true,
      input_tokens: null,
      output_tokens: 42,
      cost_usd: null,
      latency_ms: null,
    });

    render(<RoutingInfo verdict={verdict} />);

    expect(screen.getByText("yes")).toBeInTheDocument();
    expect(screen.getAllByText(EM_DASH)).toHaveLength(3);
  });

  it("renders the nine routing fields as label/value pairs in the brief's order", () => {
    const verdict = makeVerdict({
      model_primary: "gpt-5-nano",
      model_final: "gpt-5-mini",
      escalated_model: true,
      prompt_version: "triage-v1",
      input_tokens: 512,
      output_tokens: 128,
      cost_usd: "0.000228",
      latency_ms: 900,
      created_at: "2026-09-06T00:59:00.000Z",
    });

    const { container } = render(<RoutingInfo verdict={verdict} />);

    const dts = Array.from(container.querySelectorAll("dt"));
    const dds = Array.from(container.querySelectorAll("dd"));
    const pairs = dts.map((dt, i) => [dt.textContent, dds[i]?.textContent]);

    expect(pairs).toEqual([
      ["Primary model", "gpt-5-nano"],
      ["Final model", "gpt-5-mini"],
      ["Escalated to strong model", "yes"],
      ["Prompt version", "triage-v1"],
      ["Input tokens", "512"],
      ["Output tokens", "128"],
      ["Cost", "$0.000228"],
      ["Latency", "900 ms"],
      ["Verdict at", "2026-09-06 00:59:00Z"],
    ]);
  });
});
