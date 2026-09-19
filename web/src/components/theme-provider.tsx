"use client";

import type { ReactNode } from "react";
import { ThemeProvider as NextThemesProvider } from "next-themes";

/**
 * Wraps next-themes so the rest of the tree can stay server components. Dark is the shipped
 * default (PRD §9); `enableSystem` lets a first-time visitor inherit their OS preference, and
 * `attribute="data-theme"` matches the `[data-theme="dark"]` selector in globals.css.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider attribute="data-theme" defaultTheme="dark" enableSystem>
      {children}
    </NextThemesProvider>
  );
}
