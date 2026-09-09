// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { VerdictPanel } from "@/components/alerts/VerdictPanel";
import type { VerdictOut } from "@/types/api";

function makeVerdict(overrides: Partial<VerdictOut> = {}): VerdictOut {
  return {
    id: "22222222-2222-4222-8222-222222222222",
    category: "successful_intrusion",
    confidence: 0.9,
    cost_usd: "0.000228",
    created_at: "2026-09-06T00:59:00.000Z",
    escalate: true,
    escalated_model: false,
    input_tokens: 512,
    latency_ms: 900,
    model_final: "gpt-5-nano",
    model_primary: "gpt-5-nano",
    output_tokens: 128,
    prompt_version: "triage-v1",
    reasoning: "the attacker enumerated credentials against the honeypot",
    recommended_action: "block the source ip and review sensor logs",
    severity: 4,
    ...overrides,
  };
}

describe("VerdictPanel", () => {
  it("renders the full reasoning and recommended action as text", () => {
    const reasoning = "R".repeat(400);
    const recommendedAction = "Block the source IP and review sensor logs for related sessions.";
    const verdict = makeVerdict({ reasoning, recommended_action: recommendedAction });

    render(<VerdictPanel verdict={verdict} />);

    expect(reasoning).toHaveLength(400);
    expect(screen.getByText(reasoning)).toBeInTheDocument();
    expect(screen.getByText(recommendedAction)).toBeInTheDocument();
  });

  it("escapes a <script> fragment in reasoning", () => {
    const verdict = makeVerdict({ reasoning: "<script>alert(1)</script>" });

    const { container } = render(<VerdictPanel verdict={verdict} />);

    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
  });

  it("escapes a <script> fragment in recommended_action", () => {
    const verdict = makeVerdict({ recommended_action: "<script>alert(1)</script>" });

    const { container } = render(<VerdictPanel verdict={verdict} />);

    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
  });

  it("renders confidence as a whole percent and the escalate flag as text", () => {
    const escalating = makeVerdict({ confidence: 0.9, escalate: true });
    const { unmount } = render(<VerdictPanel verdict={escalating} />);

    expect(screen.getByText("Confidence 90%")).toBeInTheDocument();
    expect(screen.getByText("Escalate: yes")).toBeInTheDocument();
    unmount();

    const notEscalating = makeVerdict({ confidence: 0.9, escalate: false });
    render(<VerdictPanel verdict={notEscalating} />);

    expect(screen.getByText("Escalate: no")).toBeInTheDocument();
  });

  it("renders the severity and category badges", () => {
    const verdict = makeVerdict({ severity: 4, category: "successful_intrusion" });

    render(<VerdictPanel verdict={verdict} />);

    expect(screen.getByLabelText("Severity 4")).toBeInTheDocument();
    expect(screen.getByText("successful_intrusion")).toBeInTheDocument();
  });
});
