"""`IngestResponse`: the `POST /api/v1/alerts` response body (PRD §6.1, §8)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from core.models import AlertStatus


class IngestResponse(BaseModel):
    """The `POST /api/v1/alerts` response body: the alert's id, current status, and whether this
    request is the one that created it (as opposed to a fingerprint duplicate)."""

    id: uuid.UUID
    status: AlertStatus
    created: bool
