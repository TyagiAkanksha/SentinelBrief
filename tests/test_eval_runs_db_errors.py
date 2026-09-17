"""Pins `evals.run`'s `--database-url` error handling (m7 task-04 fix-1, review finding I1).

`evals/run.py`'s own module docstring promises every failure path "prints exactly one `error:
<code>: <message>` line to stderr, leaves stdout empty, and never raises a traceback"; the review
found the `--database-url` write path (m7 task-04, ruling R35) let a DB failure escape unhandled,
AND that it discarded the per-run JSON — the run's proof of already-incurred LLM spend — by
writing the DB row BEFORE the JSON. This file pins the fix: (a) a connect failure exits 1 with a
clean `database_error` message and no traceback, and (b) the per-run JSON for that prompt version
is already on disk when it happens (the DB write is attempted only AFTER the JSON write).

Local `_alert`/`_golden_case`/`_write_golden`/`_fake_model_env` helpers, mirroring
`tests/test_evals_run.py`'s own (deliberately NOT imported — test files never import from each
other, per that module's own docstring).

Uses an unroutable loopback port (`127.0.0.1:1`) so the connection is refused immediately without
any real database — this test must NOT require `TEST_DATABASE_URL` and must never skip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.schemas.alert import SessionAlert
from evals.golden import GoldenCase, GoldenLabel
from evals.run import main
from tests.fakes import FakeLLMClient

FIXTURES_DIR = Path("fixtures/alerts")

_VALID_VERDICT: dict[str, object] = {
    "severity": 2,
    "category": "brute_force",
    "confidence": 0.8,
    "reasoning": "ten failed logins with default credentials, no successful login observed.",
    "recommended_action": "monitor for continued brute-force activity.",
    "escalate": False,
}
VALID_VERDICT_JSON = json.dumps(_VALID_VERDICT)


@pytest.fixture(autouse=True)
def _fake_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake, zero-priced model and no real API key (mirrors `tests/test_evals_run.py`'s own
    autouse fixture, so a developer's shell can never leak a real `LLM_API_KEY` into this test)."""
    for name in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_JSON_MODE",
        "CHEAP_MODEL",
        "STRONG_MODEL",
        "MODEL_PRICES_JSON",
        "TRIAGE_PROMPT_VERSION",
        "ENVIRONMENT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}',
    )


def _alert(name: str) -> SessionAlert:
    payload = json.loads((FIXTURES_DIR / name).read_text())
    return SessionAlert.model_validate(payload)


def _golden_case(name: str) -> GoldenCase:
    """One structurally-valid golden case built from a fixture alert; the label content is
    irrelevant here (this test never scores accuracy, only the DB-failure exit path)."""
    return GoldenCase(
        alert=_alert(name),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="synthetic label for the --database-url error-handling test, not a scored "
        "claim.",
    )


def _write_golden(path: Path, cases: list[GoldenCase]) -> Path:
    path.write_text("\n".join(c.model_dump_json() for c in cases) + "\n")
    return path


def test_database_url_failure_exits_1_without_discarding_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A DB connect failure on `--database-url` (m7 task-04 fix-1, finding I1) exits 1 with a
    clean `database_error` message and no traceback — and the per-run JSON for that prompt
    version is already on disk (written before the DB attempt), so the LLM spend that already
    happened is never discarded.
    """
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    output_dir.mkdir()

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
            "--database-url",
            "postgresql://nobody:nobody@127.0.0.1:1/none",
        ],
        llm=FakeLLMClient([VALID_VERDICT_JSON]),
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err.startswith("error: database_error:")
    assert "Traceback" not in captured.err

    written = list(output_dir.glob("*-triage-v1.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["prompt_version"] == "triage-v1"
    assert len(payload["cases"]) == 1
