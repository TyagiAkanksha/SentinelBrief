"""`HealthResponse`: the `GET /healthz` body shape (PRD §8) — m3 task-02.

Modeled so `api/routes/health.py` can declare it as the OpenAPI `response_model`/`responses`
schema instead of leaving both the 200 and 503 bodies untyped in the baseline.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """The `/healthz` liveness body: overall status plus the database's own state."""

    status: Literal["ok", "degraded"]
    db: Literal["ok", "error", "unconfigured"]
