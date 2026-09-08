import type { StatusBannerProps } from "./interface";

export function StatusBanner({ status }: StatusBannerProps) {
  return (
    <p role="status">
      {status === "pending"
        ? "Triage pending — no verdict yet."
        : "Triage failed — no verdict was produced."}
    </p>
  );
}
