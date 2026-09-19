import type { StatusBannerProps } from "./interface";

export function StatusBanner({ status }: StatusBannerProps) {
  return (
    <p
      role="status"
      className="rounded-(--radius) border border-border bg-surface px-4 py-3 text-sm text-muted"
    >
      {status === "pending"
        ? "Triage pending — no verdict yet."
        : "Triage failed — no verdict was produced."}
    </p>
  );
}
