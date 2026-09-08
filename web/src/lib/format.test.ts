import { describe, expect, it } from "vitest";

import {
  formatAge,
  formatDatetimeLocalUtc,
  formatLatency,
  formatPercent,
  formatTokens,
  formatUsd,
  formatUtc,
} from "@/lib/format";

describe("formatUtc", () => {
  it("formatUtc renders UTC with seconds and a Z suffix", () => {
    expect(formatUtc("2026-09-06T01:00:00.000Z")).toBe("2026-09-06 01:00:00Z");
    expect(formatUtc("nope")).toBe("—");
  });
});

describe("formatAge", () => {
  it("formatAge buckets seconds, minutes, hours and days", () => {
    const now = new Date("2026-09-06T01:00:00.000Z");

    expect(formatAge(new Date(now.getTime() - 30_000).toISOString(), now)).toBe("<1m ago");
    expect(formatAge(new Date(now.getTime() - 3 * 60_000).toISOString(), now)).toBe("3m ago");
    expect(formatAge(new Date(now.getTime() - 2 * 60 * 60_000).toISOString(), now)).toBe("2h ago");
    expect(formatAge(new Date(now.getTime() - 5 * 24 * 60 * 60_000).toISOString(), now)).toBe(
      "5d ago",
    );
    expect(formatAge(new Date(now.getTime() + 60_000).toISOString(), now)).toBe("—");
  });
});

describe("formatUsd", () => {
  it("formatUsd formats the decimal string with six places", () => {
    expect(formatUsd("0.000228")).toBe("$0.000228");
    expect(formatUsd(null)).toBe("—");
    expect(formatUsd("x")).toBe("—");
  });
});

describe("formatPercent", () => {
  it("formatPercent rounds to a whole percent", () => {
    expect(formatPercent(0.9)).toBe("90%");
    expect(formatPercent(0.955)).toBe("96%");
  });
});

describe("formatLatency", () => {
  it("formatLatency uses thousands separators and a ms suffix", () => {
    expect(formatLatency(1234)).toBe("1,234 ms");
    expect(formatLatency(null)).toBe("—");
  });
});

describe("formatTokens", () => {
  it("formatTokens uses thousands separators", () => {
    expect(formatTokens(1234)).toBe("1,234");
    expect(formatTokens(null)).toBe("—");
  });
});

describe("formatDatetimeLocalUtc", () => {
  it("formatDatetimeLocalUtc normalizes any ISO value to a UTC datetime-local string", () => {
    expect(formatDatetimeLocalUtc("2026-09-01T00:00:00Z")).toBe("2026-09-01T00:00");
    expect(formatDatetimeLocalUtc("2026-09-01T05:30:00+05:30")).toBe("2026-09-01T00:00");
    expect(formatDatetimeLocalUtc("2026-09-01T00:00")).toBe("2026-09-01T00:00");
    expect(formatDatetimeLocalUtc("nope")).toBe("");
  });
});
