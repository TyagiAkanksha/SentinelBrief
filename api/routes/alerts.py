"""`POST /api/v1/alerts` — HMAC-signed ingest with fingerprint dedup (PRD §6.1, §8) — m2 task-03.

FastAPI decodes the JSON request body *before* solving `Depends` parameters (see
`fastapi.routing`'s request handler), so a bare `Depends(require_signature)` cannot turn an
unsigned, malformed-JSON body into a `401` — the body-decode failure would already have become a
`422 RequestValidationError` first. `SignedRoute` below is a custom `APIRoute.get_route_handler()`
(FastAPI's documented pattern for exactly this kind of before-everything-else check) that calls
`require_signature` (`api/deps.py`) directly, ahead of that decode; `request.body()` caches the
bytes, so FastAPI's own body parse downstream still sees them. `require_signature` is the one
function that verifies the signature — `SignedRoute` calls it rather than duplicating the HMAC
compare, so there is exactly one place that raises `SignatureError`.

`router` uses `route_class=SignedRoute`, so *every* route ever added to it is signature-gated; it
therefore holds only this one ingest `POST`
(`tests/test_ingest.py::test_signed_router_holds_only_the_ingest_post` pins that it stays that
way). M3's public `GET` read routes belong on their own, unsigned `APIRouter()` — never on this
one.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from api.deps import SessionDep, TriageFn, get_settings, get_triage, require_signature
from core.schemas.alert import SessionAlert
from core.schemas.ingest import IngestResponse
from core.services.alerts import insert_alert


class SignedRoute(APIRoute):
    """An `APIRoute` that verifies `X-Signature` over the raw body before FastAPI parses it."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Any]]:
        """Wrap the normal route handler with a raw-body signature check that runs first.

        Returns:
            A handler that runs `require_signature` (raising `SignatureError` on a bad
            signature) before ever reaching FastAPI's own dependency solving / body parsing,
            and otherwise delegates to the original handler unchanged.
        """
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request) -> Any:
            await require_signature(request, get_settings(request))
            return await original_handler(request)

        return custom_handler


router = APIRouter(route_class=SignedRoute)


@router.post(
    "/alerts",
    operation_id="ingest_alert",
    response_model=IngestResponse,
    status_code=202,
    responses={
        200: {
            "model": IngestResponse,
            "description": "Duplicate session: existing alert returned, triage not re-run.",
        },
        401: {"description": "Missing or invalid X-Signature."},
        422: {"description": "Invalid session payload."},
    },
)
async def ingest_alert(
    payload: SessionAlert,
    session: SessionDep,
    triage: TriageFn = Depends(get_triage),
) -> JSONResponse:
    """Insert `payload`, deduplicating on its fingerprint; triage only newly created alerts.

    Args:
        payload: The parsed session alert body.
        session: The request-scoped session (`SessionDep`); its commit/rollback runs before the
            response is sent.
        triage: The wired triage callable, invoked only for newly created alerts. It persists
            the status it returns (verdict + status in one transaction, PRD §6.2) — this route
            never writes `alerts.status` itself.

    Returns:
        `202` with the new alert's id/status when this request created it; `200` with the
        existing alert's id/current status on a fingerprint duplicate.
    """
    result = await insert_alert(session, payload)
    if result.created:
        # M2 inline triage; removed at M5 (the worker/ARQ job owns this call there). Committing
        # here — ahead of `SessionDep`'s own post-response commit — makes the new row visible
        # to `triage`, which runs in the same request and needs the id to already be durable.
        await session.commit()
        status = await triage(session, result.alert_id)
    else:
        status = result.status

    body = IngestResponse(id=result.alert_id, status=status, created=result.created)
    return JSONResponse(
        status_code=202 if result.created else 200, content=body.model_dump(mode="json")
    )
