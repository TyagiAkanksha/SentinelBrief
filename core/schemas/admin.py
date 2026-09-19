"""`RetriageResponse`: the `POST /api/v1/admin/retriage/{alert_id}` response body (PRD §8, m8b
task-04).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from core.models import AlertStatus


class RetriageResponse(BaseModel):
    """The `POST /api/v1/admin/retriage/{alert_id}` response body: the alert's id, its new
    status (always `"pending"`), and `retriaged=True`."""

    id: uuid.UUID
    status: AlertStatus
    retriaged: bool
