"""Pins the OpenAPI hygiene the codegen (task-03) and CI drift check need: title/version,
`\\f`-truncated descriptions, a `HealthResponse` model, `ErrorEnvelope` on every error response,
and the M3 read routes' stable `operationId`s (PRD §8; M2 final review, plan defects 3, 5) —
m3 task-02.

Every assertion reads `create_app().openapi()` directly — the same DB-less spec
`scripts/export_openapi.py` dumps — never the committed `api/openapi.json` file (that pin lives in
`tests/test_openapi_baseline.py`). Every dict lookup here is defensive (`.get(..., {})`) on
purpose: at RED, most of these keys don't exist yet, and a missing-hygiene bug must show up as a
clean assertion failure, never an opaque `KeyError`.
"""

from __future__ import annotations

import importlib.metadata
from typing import Any

from api.factory import create_app

_ERROR_ENVELOPE_REF = "#/components/schemas/ErrorEnvelope"


def _spec() -> dict[str, Any]:
    return create_app().openapi()


def _operations(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        operation
        for path_item in spec["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict)
    ]


def _schema_ref(response: dict[str, Any]) -> str:
    return response.get("content", {}).get("application/json", {}).get("schema", {}).get("$ref", "")


def test_info_title_and_version() -> None:
    spec = _spec()

    assert spec["info"]["title"] == "SentinelBrief API"
    assert spec["info"]["version"] == importlib.metadata.version("sentinelbrief")


def test_no_operation_description_contains_docstring_sections() -> None:
    spec = _spec()

    for operation in _operations(spec):
        description = operation.get("description", "")
        for section in ("Args:", "Returns:", "Raises:"):
            assert section not in description, (
                f"{operation.get('operationId')}'s description leaks {section!r}: {description!r}"
            )


def test_healthz_declares_health_response_for_200_and_503() -> None:
    spec = _spec()

    responses = spec["paths"]["/healthz"]["get"]["responses"]
    for status in ("200", "503"):
        response = responses.get(status, {})
        ref = _schema_ref(response)
        assert ref.endswith("/HealthResponse"), f"{status} does not ref HealthResponse: {response}"


def test_every_error_response_references_error_envelope() -> None:
    spec = _spec()

    checked = 0
    for path, path_item in spec["paths"].items():
        if path == "/healthz":
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for status, response in operation.get("responses", {}).items():
                if not status.isdigit() or int(status) < 400:
                    continue
                checked += 1
                ref = _schema_ref(response)
                assert ref == _ERROR_ENVELOPE_REF, (
                    f"{operation.get('operationId')} {status} does not ref ErrorEnvelope: "
                    f"{response}"
                )

    assert checked > 0, "expected at least one >= 400 response across the non-healthz operations"


def test_paginated_alert_summary_schema_name_is_stable() -> None:
    spec = _spec()

    assert "PaginatedResponse_AlertSummary_" in spec["components"]["schemas"]

    get_alerts = spec["paths"].get("/api/v1/alerts", {}).get("get", {})
    ok_200 = get_alerts.get("responses", {}).get("200", {})
    ref = _schema_ref(ok_200)
    assert ref.endswith("/PaginatedResponse_AlertSummary_")


def test_read_operation_ids_present() -> None:
    spec = _spec()

    operation_ids = {operation.get("operationId") for operation in _operations(spec)}

    assert {"list_alerts", "get_alert", "get_stats"} <= operation_ids
