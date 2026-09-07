"""`POST /api/v1/alerts` — HMAC-signed ingest with fingerprint dedup (PRD §6.1, §8) — m2 task-03.

Signature verification runs twice, on purpose. FastAPI decodes the JSON request body *before*
solving `Depends` parameters (see `fastapi.routing`'s request handler), so a bare
`Depends(require_signature)` cannot turn an unsigned, malformed-JSON body into a `401` — the
body-decode failure would already have become a `422 RequestValidationError` first. `SignedRoute`
below reads the raw body and checks the signature ahead of that decode, inside a custom
`APIRoute.get_route_handler()` (FastAPI's documented pattern for exactly this kind of
before-everything-else check); `request.body()` caches the bytes, so FastAPI's own body parse
downstream still sees them. `Depends(require_signature)` on the route itself is a second,
idempotent check of the identical bytes and secret — kept so the shared, Interfaces-declared
`require_signature` function stays part of the route's declared dependencies rather than logic
that only lives inside the custom route class.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from api.deps import SessionDep, TriageFn, get_settings, get_triage, require_signature
from core.errors import SignatureError
from core.schemas.alert import SessionAlert
from core.schemas.ingest import IngestResponse
from core.services.alerts import insert_alert, set_alert_status
from core.signing import SIGNATURE_HEADER, verify_signature


class SignedRoute(APIRoute):
    """An `APIRoute` that verifies `X-Signature` over the raw body before FastAPI parses it."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Any]]:
        """Wrap the normal route handler with a raw-body signature check that runs first.

        Returns:
            A handler that raises `SignatureError` on a bad signature before ever reaching
            FastAPI's own dependency solving / body parsing, and otherwise delegates to the
            original handler unchanged.
        """
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request) -> Any:
            settings = get_settings(request)
            body = await request.body()
            header = request.headers.get(SIGNATURE_HEADER)
            if not verify_signature(settings.ingest_hmac_secret.get_secret_value(), body, header):
                raise SignatureError("missing or invalid signature")
            return await original_handler(request)

        return custom_handler


router = APIRouter(route_class=SignedRoute)


@router.post("/alerts", operation_id="ingest_alert", response_model=IngestResponse)
async def ingest_alert(
    payload: SessionAlert,
    session: SessionDep,
    triage: TriageFn = Depends(get_triage),
    _sig: None = Depends(require_signature),
) -> JSONResponse:
    """Insert `payload`, deduplicating on its fingerprint; triage only newly created alerts.

    Args:
        payload: The parsed session alert body.
        session: The request-scoped session (`SessionDep`); its commit/rollback runs before the
            response is sent.
        triage: The wired triage callable, invoked only for newly created alerts.
        _sig: Unused; its presence re-runs `require_signature` as a second, idempotent check
            (see the module docstring for why `SignedRoute` is what actually guarantees
            401-before-422).

    Returns:
        `202` with the new alert's id/status when this request created it; `200` with the
        existing alert's id/current status on a fingerprint duplicate.
    """
    result = await insert_alert(session, payload)
    if result.created:
        # M2 inline triage; removed at M5 (the worker/ARQ job owns this call there). Committing
        # here — ahead of `SessionDep`'s own post-response commit — makes the new row visible
        # to `triage`, which runs in the same request and needs the id to already be durable.
        # `triage` itself only returns the outcome (the real worker pipeline persists its own
        # verdict; this M2 fake does not), so the route writes the resulting status back onto
        # the row itself, via `SessionDep`'s own commit, so a later duplicate POST sees it.
        await session.commit()
        status = await triage(session, result.alert_id)
        await set_alert_status(session, result.alert_id, status)
    else:
        status = result.status

    body = IngestResponse(id=result.alert_id, status=status, created=result.created)
    return JSONResponse(
        status_code=202 if result.created else 200, content=body.model_dump(mode="json")
    )
