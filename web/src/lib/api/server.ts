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
    let envelope: ErrorEnvelope | null = null;
    try {
      envelope = (await res.json()) as ErrorEnvelope;
    } catch {
      envelope = null;
    }
    throw new ApiError(res.status, envelope);
  }

  return (await res.json()) as T;
}
