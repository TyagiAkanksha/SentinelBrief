import type { Metadata } from "next";
import type { ReactNode } from "react";

// Aliased to `Link` because that is what these nav entries are; `NavLink` adds the dashboard-wide
// link treatment and `aria-current="page"` on top of `next/link` (review t03 M-links-wide).
import { NavLink as Link } from "@/components/ui/NavLink";

import "./globals.css";

export const metadata: Metadata = {
  title: "SentinelBrief",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header>
          <nav aria-label="Primary" className="flex gap-4">
            <Link href="/alerts">Alerts</Link>
            <Link href="/stats">Stats</Link>
            <Link href="/about">About</Link>
          </nav>
        </header>
        <main className="px-4 py-3">{children}</main>
      </body>
    </html>
  );
}
