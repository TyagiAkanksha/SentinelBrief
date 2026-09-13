import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "SentinelBrief",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header>
          <nav aria-label="Primary">
            <Link href="/alerts">Alerts</Link>
            <Link href="/stats">Stats</Link>
            <Link href="/about">About</Link>
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
