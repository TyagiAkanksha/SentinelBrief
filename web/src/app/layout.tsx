import type { Metadata } from "next";
import type { ReactNode } from "react";
import NextLink from "next/link";
import { Quicksand } from "next/font/google";
import { ShieldCheck } from "lucide-react";

// Aliased to `Link` because that is what these nav entries are; `NavLink` adds the dashboard-wide
// link treatment and `aria-current="page"` on top of `next/link` (review t03 M-links-wide).
import { NavLink as Link } from "@/components/ui/NavLink";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { ThemeProvider } from "@/components/theme-provider";
import { PRD_URL, REPO_URL } from "@/lib/site";

import "./globals.css";

const quicksand = Quicksand({
  variable: "--font-quicksand",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "SentinelBrief",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // suppressHydrationWarning: next-themes mutates <html data-theme> pre-paint.
    <html lang="en" suppressHydrationWarning className={quicksand.variable}>
      <body>
        <ThemeProvider>
          <div className="flex min-h-screen flex-col">
            <header className="sticky top-0 z-40 border-b border-border bg-bg/85 backdrop-blur-sm">
              <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
                <NextLink
                  href="/"
                  className="inline-flex items-center gap-2 font-display text-lg font-semibold tracking-tight text-text transition-colors hover:text-accent"
                >
                  <ShieldCheck className="size-5 text-accent" aria-hidden />
                  SentinelBrief
                </NextLink>
                <div className="flex items-center gap-5 sm:gap-6">
                  <nav aria-label="Primary">
                    <ul className="flex items-center gap-4 text-sm sm:gap-6">
                      <li>
                        <Link href="/alerts">Alerts</Link>
                      </li>
                      <li>
                        <Link href="/stats">Stats</Link>
                      </li>
                      <li>
                        <Link href="/about">About</Link>
                      </li>
                    </ul>
                  </nav>
                  <ThemeToggle />
                </div>
              </div>
            </header>
            <main className="mx-auto w-full max-w-5xl grow px-4 py-8 sm:px-6">{children}</main>
            <footer className="border-t border-border">
              <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-x-6 gap-y-1 px-4 py-4 text-xs text-muted sm:px-6">
                <span>SentinelBrief — LLM honeypot triage</span>
                <span className="flex items-center gap-4">
                  <a href={REPO_URL} target="_blank" rel="noreferrer">
                    Source
                  </a>
                  <a href={PRD_URL} target="_blank" rel="noreferrer">
                    Spec
                  </a>
                </span>
              </div>
            </footer>
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}
