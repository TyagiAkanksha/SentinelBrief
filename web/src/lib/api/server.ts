import type { ErrorEnvelope } from "@/types/api";

export type { ErrorEnvelope } from "@/types/api";

export class ApiError extends Error {
  readonly status: number;
  readonly envelope: ErrorEnvelope | null;

  constructor(status: number, envelope: ErrorEnvelope | null) {
    super(envelope?.error.message ?? `API error ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.envelope = envelope;
  }
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const error: unknown = (value as { error: unknown }).error;
  if (typeof error !== "object" || error === null) {
    return false;
  }
  const { code, message } = error as { code: unknown; message: unknown };
  return typeof code === "string" && typeof message === "string";
}

export const DEFAULT_DEV_API_URL = "http://localhost:8000";

export function apiUrl(): string {
  const raw = process.env.API_URL;
  if (!raw) {
    if (process.env.NODE_ENV === "production") {
      throw new Error("API_URL is not set");
    }
    return DEFAULT_DEV_API_URL;
  }
  return raw.replace(/\/+$/, "");
}

export async function getJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${apiUrl()}${path}`, {
    ...init,
    cache: "no-store",
    headers: { accept: "application/json" },
  });

  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null);
    throw new ApiError(res.status, isErrorEnvelope(body) ? body : null);
  }

  return (await res.json()) as T;
}
