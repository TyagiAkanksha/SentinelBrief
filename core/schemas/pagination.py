"""`PaginatedResponse[T]`: the one generic envelope every M3+ list route wraps its items in
(PRD §8) — m3 task-01.

A PEP 695 generic Pydantic model: `PaginatedResponse[AlertSummary]` registers as
`PaginatedResponse_AlertSummary_` in the OpenAPI schema (FastAPI 0.141 / pydantic 2.13), which
task-02 pins and task-03's codegen consumes.
"""

from __future__ import annotations

from pydantic import BaseModel


class PaginatedResponse[T](BaseModel):
    """A page of `T` items plus the total row count matching the filters (PRD §8).

    `total` is the count of rows matching the request's filters, not `len(items)` — a caller
    computes total pages from `total` and `page_size`, independent of the current page's length.
    """

    items: list[T]
    total: int
    page: int
    page_size: int
