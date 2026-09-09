// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { Timeline } from "@/components/alerts/Timeline";
import type { ToolCallOut } from "@/types/api";

function makeToolCall(overrides: Partial<ToolCallOut> = {}): ToolCallOut {
  return {
    seq: 0,
    tool_name: "lookup_ip_reputation",
    arguments: {},
    result: {},
    latency_ms: 12,
    ...overrides,
  };
}

describe("Timeline", () => {
  it("renders the empty state when there are no tool calls", () => {
    render(<Timeline toolCalls={[]} />);

    const list = screen.getByRole("list", { name: "Tool trace" });
    const items = within(list).getAllByRole("listitem");

    expect(items).toHaveLength(1);
    expect(items[0]!).toHaveTextContent("No tool calls were made.");
    expect(screen.queryByText("Tool trace arrives at M4")).toBeNull();
    // m4 fix-wave (review finding task-07 N3 / MUT-15 survived): the empty state must render
    // through the shared `EmptyState` primitive (`role="status"`), not just any element with the
    // right text — a bare `<p>` with the same message would satisfy the assertions above but
    // drops the accessible live-region role.
    expect(within(items[0]!).getByRole("status")).toHaveTextContent("No tool calls were made.");
  });

  it("renders tool name, arguments, result and latency for each call in order", () => {
    const toolCalls: ToolCallOut[] = [
      makeToolCall({
        seq: 0,
        tool_name: "lookup_ip_reputation",
        // maxAgeInDays lives only in arguments, never in result — a substring match against
        // the item's whole text content can therefore only come from the Arguments block, not
        // (as `"ip": "203.0.113.7"` alone could) from either block.
        arguments: { ip: "203.0.113.7", maxAgeInDays: 90 },
        result: { ip: "203.0.113.7", abuse_score: 87 },
        latency_ms: 12,
      }),
      makeToolCall({
        seq: 1,
        tool_name: "get_session_commands",
        arguments: { session_id: "abc123" },
        result: { commands: ["whoami"] },
        latency_ms: 340,
      }),
    ];

    render(<Timeline toolCalls={toolCalls} />);

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);

    expect(items[0]!).toHaveTextContent("0. lookup_ip_reputation");
    expect(items[0]!).toHaveTextContent("12 ms");
    expect(items[0]!).toHaveTextContent('"abuse_score": 87');
    expect(items[0]!).toHaveTextContent('"ip": "203.0.113.7"');
    expect(items[0]!).toHaveTextContent('"maxAgeInDays": 90');

    expect(items[1]!).toHaveTextContent("1. get_session_commands");
    expect(items[1]!).toHaveTextContent("340 ms");
  });

  it("renders a <script> fragment inside a command as text, never an element", () => {
    const toolCalls: ToolCallOut[] = [
      makeToolCall({
        seq: 0,
        tool_name: "get_session_commands",
        arguments: { session_id: "<img src=x onerror=alert(1)>" },
        result: { commands: ["<script>alert(1)</script>"] },
      }),
    ];

    const { container } = render(<Timeline toolCalls={toolCalls} />);

    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
    expect(container.querySelector("script, img")).toBeNull();
  });

  it("marks an unavailable result visibly with its reason", () => {
    const toolCalls: ToolCallOut[] = [
      makeToolCall({
        seq: 0,
        tool_name: "lookup_ip_reputation",
        result: { unavailable: true, reason: "no_api_key" },
      }),
      makeToolCall({
        seq: 1,
        tool_name: "get_ip_geo_asn",
        result: { unavailable: true },
      }),
      makeToolCall({
        seq: 2,
        tool_name: "get_asset_info",
        result: { asset: "web-01" },
      }),
    ];

    render(<Timeline toolCalls={toolCalls} />);

    const items = screen.getAllByRole("listitem");
    expect(items[0]!).toHaveTextContent("unavailable (no_api_key)");
    expect(items[1]!).toHaveTextContent("unavailable (unknown)");
    expect(items[2]!).not.toHaveTextContent("unavailable (");
  });

  it("renders an em dash for a null latency", () => {
    const toolCalls: ToolCallOut[] = [makeToolCall({ latency_ms: null })];

    render(<Timeline toolCalls={toolCalls} />);

    expect(screen.getByRole("listitem")).toHaveTextContent("—");
  });

  it("keeps the ordered list with the Tool trace name and one li per call", () => {
    const toolCalls: ToolCallOut[] = [
      makeToolCall({ seq: 0 }),
      makeToolCall({ seq: 1 }),
      makeToolCall({ seq: 2 }),
    ];

    render(<Timeline toolCalls={toolCalls} />);

    const list = screen.getByRole("list", { name: "Tool trace" });
    expect(list.tagName).toBe("OL");
    expect(screen.getAllByRole("listitem")).toHaveLength(toolCalls.length);
  });
});
