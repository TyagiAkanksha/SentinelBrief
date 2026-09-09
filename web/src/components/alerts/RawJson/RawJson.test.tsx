// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { RawJson } from "@/components/alerts/RawJson";

describe("RawJson", () => {
  it("renders a collapsed details element with the summary text", () => {
    const raw = { src_ip: "203.0.113.7", events: [] };

    const { container } = render(<RawJson raw={raw} />);

    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect(details?.open).toBe(false);
    expect(screen.getByText("Raw session JSON")).toBeInTheDocument();
    expect(container.querySelector("pre")?.textContent).toContain('"src_ip"');
  });

  it("escapes a <script> fragment inside a raw username as text", () => {
    const raw = { events: [{ username: "<script>alert(1)</script>" }] };

    const { container } = render(<RawJson raw={raw} />);

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("pre")?.textContent).toContain("<script>alert(1)</script>");
  });
});
