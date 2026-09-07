"""Pins `Settings.cors_origin_list` and the CORS preflight behavior `create_app` wires
`CORSMiddleware` with (CONVENTIONS.md §5; PRD §8) — m2 task-02 fix 2.

A configured origin must be echoed back exactly; an unconfigured origin must get no
`Access-Control-Allow-Origin` header at all. The origin is never echoed as a literal `*` when
credentials are allowed — the CORS spec forbids that combination, and it would defeat the point
of restricting origins in the first place.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from api.factory import create_app
from core.config import Settings


def test_cors_origin_list_splits_and_strips() -> None:
    settings = Settings(cors_origins=" http://a.example , http://b.example,,")

    assert settings.cors_origin_list == ["http://a.example", "http://b.example"]


async def test_cors_preflight_allows_configured_origin_only(settings: Settings) -> None:
    app = create_app(settings=settings)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        allowed = await client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        blocked = await client.options(
            "/healthz",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    # Read the actual response, not create_app's kwargs: if credentials are ever allowed, the
    # echoed origin must be the exact request origin, never the "*" wildcard.
    if allowed.headers.get("access-control-allow-credentials") == "true":
        assert allowed.headers["access-control-allow-origin"] != "*"

    assert "access-control-allow-origin" not in blocked.headers
