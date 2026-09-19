import type { ReactNode } from "react";

export type ButtonVariant = "primary" | "ghost";

export type ButtonProps = {
  children: ReactNode;
  variant?: ButtonVariant;
  type?: "button" | "submit" | "reset";
  className?: string;
};

export type ButtonLinkProps = {
  href: string;
  children: ReactNode;
  variant?: ButtonVariant;
  external?: boolean;
  className?: string;
};

// Shared control shape; the variant only supplies colour. The token focus ring pairs with the
// global :focus-visible outline from globals.css for a portfolio-grade highlight in both themes.
export const BUTTON_BASE =
  "inline-flex items-center justify-center gap-2 rounded-(--radius) px-3 py-1.5 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring";

export const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent text-bg hover:bg-accent-strong",
  ghost: "border border-border bg-surface text-text hover:bg-surface-2",
};
