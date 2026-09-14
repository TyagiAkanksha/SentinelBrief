import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_DEV_PUBLIC_API_URL, publicApiUrl, streamUrl } from "@/lib/api/browser";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("publicApiUrl", () => {
  it("publicApiUrl strips trailing slashes", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.example/");

    expect(publicApiUrl()).toBe("https://api.example");
  });

  it("publicApiUrl throws when NEXT_PUBLIC_API_URL is unset under NODE_ENV=production", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", undefined);
    vi.stubEnv("NODE_ENV", "production");

    expect(() => publicApiUrl()).toThrow("NEXT_PUBLIC_API_URL is not set");
  });

  it("publicApiUrl falls back to localhost and streamUrl appends the stream path", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", undefined);
    vi.stubEnv("NODE_ENV", "test");

    expect(publicApiUrl()).toBe(DEFAULT_DEV_PUBLIC_API_URL);
    expect(streamUrl()).toBe(`${DEFAULT_DEV_PUBLIC_API_URL}/api/v1/stream`);
    expect(streamUrl().endsWith("/api/v1/stream")).toBe(true);
  });
});
