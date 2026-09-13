"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import type { NavLinkProps } from "./interface";

/**
 * One primary-nav link: the dashboard-wide link treatment plus `aria-current="page"` when it is
 * the page being shown. `usePathname` is a client hook, which is why this is the layout's only
 * client island; it compares exactly, so `/alerts/<id>` does not mark "Alerts" as current.
 */
export function NavLink({ href, children }: NavLinkProps) {
  const pathname = usePathname();

  return (
    <Link
      href={href}
      className="text-accent underline"
      aria-current={pathname === href ? "page" : undefined}
    >
      {children}
    </Link>
  );
}
