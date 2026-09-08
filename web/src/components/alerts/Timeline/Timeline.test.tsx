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
  it("renders the M4 placeholder when there are no tool calls", () => {
    render(<Timeline toolCalls={[]} />);

    const list = screen.getByRole("list", { name: "Tool trace" });
    const items = within(list).getAllByRole("listitem");

    expect(items).toHaveLength(1);
    expect(items[0]!).toHaveTextContent("Tool trace arrives at M4");
  });

  it("renders one item per tool call in the given order", () => {
    const toolCalls: ToolCallOut[] = [
      makeToolCall({ seq: 0, tool_name: "lookup_ip_reputation", latency_ms: 12 }),
      makeToolCall({ seq: 1, tool_name: "get_session_commands", latency_ms: 340 }),
    ];

    render(<Timeline toolCalls={toolCalls} />);

    const list = screen.getByRole("list", { name: "Tool trace" });
    const items = within(list).getAllByRole("listitem");

    expect(items).toHaveLength(2);
    expect(items[0]!).toHaveTextContent("0. lookup_ip_reputation · 12 ms");
    expect(items[1]!).toHaveTextContent("1. get_session_commands · 340 ms");
    expect(screen.queryByText("Tool trace arrives at M4")).toBeNull();
  });
});
