// The ONLY browser-side origin reader (docs/FRONTEND-CONVENTIONS.md §6): `NEXT_PUBLIC_API_URL`
// is inlined into the bundle at `next build` time, so it must be referenced as this exact
// literal expression for Next to statically replace it. Only `useAlertStream` (via `streamUrl`)
// runs in the browser; every other data fetch is server-side through `@/lib/api/server`.

export const DEFAULT_DEV_PUBLIC_API_URL = "http://localhost:8000";

export function publicApiUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_URL;
  if (!raw) {
    if (process.env.NODE_ENV === "production") {
      throw new Error("NEXT_PUBLIC_API_URL is not set");
    }
    return DEFAULT_DEV_PUBLIC_API_URL;
  }
  return raw.replace(/\/+$/, "");
}

export function streamUrl(): string {
  return `${publicApiUrl()}/api/v1/stream`;
}
