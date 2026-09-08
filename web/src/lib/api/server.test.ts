import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, DEFAULT_DEV_API_URL, apiUrl, getJson } from "@/lib/api/server";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("apiUrl", () => {
  it("apiUrl returns API_URL with trailing slashes stripped", () => {
    vi.stubEnv("API_URL", "http://api:8000/");

    expect(apiUrl()).toBe("http://api:8000");
  });

  it("apiUrl throws when API_URL is unset under NODE_ENV=production", () => {
    vi.stubEnv("API_URL", undefined);
    vi.stubEnv("NODE_ENV", "production");

    expect(() => apiUrl()).toThrow("API_URL is not set");
  });

  it("apiUrl falls back to http://localhost:8000 outside production", () => {
    vi.stubEnv("API_URL", undefined);
    vi.stubEnv("NODE_ENV", "test");

    expect(apiUrl()).toBe(DEFAULT_DEV_API_URL);
  });
});

describe("getJson", () => {
  it("getJson returns the parsed body and always sends cache: no-store", async () => {
    vi.stubEnv("API_URL", "http://api:8000");
    const body = { alerts: [] };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getJson<typeof body>("/api/v1/alerts", { cache: "force-cache" });

    expect(result).toEqual(body);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      `${apiUrl()}/api/v1/alerts`,
      expect.objectContaining({
        cache: "no-store",
        headers: expect.objectContaining({ accept: "application/json" }),
      }),
    );
  });

  it("getJson throws ApiError carrying the ErrorEnvelope on a non-2xx JSON body", async () => {
    vi.stubEnv("API_URL", "http://api:8000");
    const envelope = { error: { code: "not_found", message: "x" } };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(envelope), { status: 404 })),
    );

    let caught: unknown;
    try {
      await getJson("/api/v1/alerts/missing");
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(ApiError);
    const apiError = caught as ApiError;
    expect(apiError.status).toBe(404);
    expect(apiError.envelope).toEqual(envelope);
    expect(apiError.message).toBe("x");
  });

  it("getJson throws ApiError with envelope null when the error body is not JSON", async () => {
    vi.stubEnv("API_URL", "http://api:8000");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("bad gateway", { status: 502 })),
    );

    let caught: unknown;
    try {
      await getJson("/api/v1/alerts");
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(ApiError);
    const apiError = caught as ApiError;
    expect(apiError.status).toBe(502);
    expect(apiError.envelope).toBeNull();
    expect(apiError.message).toBe("API error 502");
  });

  it("getJson throws ApiError with envelope null when the error body is JSON but not an envelope", async () => {
    vi.stubEnv("API_URL", "http://api:8000");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ detail: "Not Found" }, { status: 404 })),
    );

    let caught: unknown;
    try {
      await getJson("/x");
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(ApiError);
    const apiError = caught as ApiError;
    expect(apiError.status).toBe(404);
    expect(apiError.envelope).toBeNull();
    expect(apiError.message).toBe("API error 404");
  });

  it("getJson rejects an envelope whose error lacks string code/message", async () => {
    vi.stubEnv("API_URL", "http://api:8000");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: { code: 42 } }, { status: 400 })),
    );

    let caught: unknown;
    try {
      await getJson("/x");
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(ApiError);
    const apiError = caught as ApiError;
    expect(apiError.status).toBe(400);
    expect(apiError.envelope).toBeNull();
    expect(apiError.message).toBe("API error 400");
  });
});
