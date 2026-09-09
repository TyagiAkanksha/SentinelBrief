import Link from "next/link";

import type { PaginationProps } from "./interface";

export function Pagination({ page, pageSize, total, hrefForPage }: PaginationProps) {
  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <nav aria-label="Pagination">
      <span>
        Page {page} of {pages} · {total} alerts
      </span>
      {page > 1 && <Link href={hrefForPage(page - 1)}>Previous</Link>}
      {page < pages && <Link href={hrefForPage(page + 1)}>Next</Link>}
    </nav>
  );
}
