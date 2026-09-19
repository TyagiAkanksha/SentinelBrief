"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

/**
 * Header button that flips between light and dark. Both icons are always rendered; CSS picks one
 * via the `[data-theme]` `dark:` variant, so there is no mounted-state gate and no hydration
 * flicker. `resolvedTheme` is only read to decide the direction of the next toggle.
 */
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();

  return (
    <button
      type="button"
      aria-label="Toggle color theme"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
      className="inline-flex size-8 items-center justify-center rounded-(--radius) text-muted transition-colors hover:bg-surface-2 hover:text-accent"
    >
      <Sun className="hidden size-4 dark:block" aria-hidden />
      <Moon className="size-4 dark:hidden" aria-hidden />
    </button>
  );
}
