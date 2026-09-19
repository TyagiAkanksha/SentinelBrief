import Link from "next/link";

import type { PaginationProps } from "./interface";

export function Pagination({ page, pageSize, total, hrefForPage }: PaginationProps) {
  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <nav
      aria-label="Pagination"
      className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-muted"
    >
      <span>
        Page {page} of {pages} · {total} alerts
      </span>
      {page > 1 && (
        <Link href={hrefForPage(page - 1)} className="text-accent hover:text-accent-strong">
          Previous
        </Link>
      )}
      {page < pages && (
        <Link href={hrefForPage(page + 1)} className="text-accent hover:text-accent-strong">
          Next
        </Link>
      )}
    </nav>
  );
}
