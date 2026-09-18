"""Pins `evals.run`: `run_golden`, `main`, the CLI flags, and every failure path (m1 task-03).

PRD §7.2 (CLI shape: repeatable `--prompt`, `--model`, `--concurrency`, `--output-dir`), §7.3
(the run drives the **real** `worker.triage.TriagePipeline`, never a reimplementation); the
task-03 brief's failure-path table (usage / config_error(Settings) / invalid_golden /
config_error(pipeline) / output_error, plus the separate `all_cases_failed` path) and its
"price before spend" note (an unpriced `--model` fails at `TriagePipeline`/`from_settings`
construction, never mid-run).

Every test builds its own tiny golden set from `fixtures/alerts/alert*.json` via `SessionAlert` +
`GoldenLabel` (never `evals/golden/v1.jsonl`, per `.claude/rules/evals.md`: v1 is real dataset
content, not test fixture material) and injects `tests.fakes.FakeLLMClient` so nothing here
touches the network. `evals.run` does not exist yet, so every test in this module is RED at
collection with `ModuleNotFoundError: No module named 'evals.run'`, not merely at first use.

m5 task-03 (PRD §6.4) adds `--strong-model` and its price-before-spend check: two more tests near
the bottom of this module, past the m5 task-01 registry-lifecycle test.

m5 task-03 fix-1 (review I1) adds one more: `--strong-model` equal to `--model` must fail as a
clean `config_error`, not escape `TriagePipeline.__init__`'s bare `ValueError` as a traceback.

m7 task-04 (ruling R35) adds `--database-url`/`--schema` (the `eval_runs` row writer) and ruling
R42's judge-`LLMCallError` regression pin, appended at the end -- additive only, no existing test/
helper above is touched.

m7 task-05 (ruling R44) adds `--gate`/`--baseline`/`--write-baseline`, appended at the end, and
makes the ONE pre-approved edit to a pre-existing (task-04) pin:
`test_main_writes_eval_runs_row_when_database_url_given`'s `model_config` exact-equality
assertion gains the two new keys `run_model_config` (ruling R43) adds -- `strong_model` and
`prompt_version` -- with the values that run produces (`""` and `"triage-v1"`); no other
pre-existing test/helper in this file is touched.

m7 task-06 (ruling R47) adds `--publish`/`--from-artifact`, appended at the end, and makes the
ONE pre-approved edit to a pre-existing (task-03) pin: `test_main_writes_result_json`'s
`set(payload.keys())` assertion gains the new `"golden"` key `--from-artifact`'s v2-only refusal
reads back, plus one new line asserting `payload["golden"] == str(golden_path)`; no other
pre-existing test/helper in this file is touched. The new tests below use LOCAL imports for
`evals.publish` names task-06 adds (`RESULTS_PATH`, `render_header`) -- never a top-level import
-- so a not-yet-existing name fails only the test that needs it, not the whole module's
collection (mirrors this file's own task-04 section, e.g. `core.db`/`core.models.eval_runs`
imported inside `test_main_writes_eval_runs_row_when_database_url_given` rather than at the top).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from core.errors import LLMCallError
from core.schemas.alert import SessionAlert
from evals.golden import GoldenCase, GoldenLabel
from evals.publish import metrics_payload
from evals.run import main, run_golden
from evals.scoring import COLUMNS, PR, CaseResult, RunMetrics
from tests.fakes import FakeLLMClient
from tests.helpers import VALID4, VALID4_STRONG
from worker.triage import TriagePipeline

FIXTURES_DIR = Path("fixtures/alerts")

# A minimally-valid Verdict reply, redefined locally (tests/test_triage_one.py owns the same
# constant; test files never import from each other, per that module's own docstring).
_VALID_VERDICT: dict[str, object] = {
    "severity": 2,
    "category": "brute_force",
    "confidence": 0.8,
    "reasoning": "ten failed logins with default credentials, no successful login observed.",
    "recommended_action": "monitor for continued brute-force activity.",
    "escalate": False,
}
VALID_VERDICT_JSON = json.dumps(_VALID_VERDICT)

# Every env var Settings maps to (CONVENTIONS.md §7 / .env.example); cleared before each test so a
# developer's shell (in particular a real LLM_API_KEY) can never leak into the CLI under test.
_SETTINGS_ENV_VARS = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_JSON_MODE",
    "CHEAP_MODEL",
    "STRONG_MODEL",
    "MODEL_PRICES_JSON",
    "TRIAGE_PROMPT_VERSION",
    "ENVIRONMENT",
)


@pytest.fixture(autouse=True)
def _fake_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake, zero-priced model and no real API key for every test in this module by default."""
    for name in _SETTINGS_ENV_VARS:
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
    irrelevant to every test in this module (none of them score accuracy)."""
    return GoldenCase(
        alert=_alert(name),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="synthetic label for evals.run CLI plumbing tests, not a scored claim.",
    )


def _write_golden(path: Path, cases: list[GoldenCase]) -> Path:
    path.write_text("\n".join(c.model_dump_json() for c in cases) + "\n")
    return path


# --- run_golden -------------------------------------------------------------------------------


def test_run_golden_one_result_per_case_in_order() -> None:
    cases = [_golden_case("alert1.json"), _golden_case("alert2.json"), _golden_case("alert3.json")]
    fake = FakeLLMClient([VALID_VERDICT_JSON, VALID_VERDICT_JSON, VALID_VERDICT_JSON])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")

    results = asyncio.run(run_golden(cases, pipeline=pipeline, concurrency=1))

    assert [r.case_id for r in results] == [c.case_id for c in cases]
    assert all(r.verdict is not None for r in results)
    assert all(r.error is None for r in results)


def test_run_golden_captures_pipeline_failure_as_error() -> None:
    cases = [_golden_case("alert1.json"), _golden_case("alert2.json")]
    # First case fails validation twice (VerdictValidationError); second case succeeds.
    fake = FakeLLMClient(["{}", "{}", VALID_VERDICT_JSON])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")

    results = asyncio.run(run_golden(cases, pipeline=pipeline, concurrency=1))

    assert len(results) == 2
    first, second = results
    assert first.case_id == cases[0].case_id
    assert first.verdict is None
    assert first.error is not None
    assert first.error.startswith("verdict_validation")
    # M0: tokens/cost for a failed case are unknown to the pipeline -> recorded as 0.
    assert first.input_tokens == 0
    assert first.output_tokens == 0
    assert first.cost_usd == Decimal("0")
    assert second.case_id == cases[1].case_id
    assert second.verdict is not None
    assert second.error is None


# --- main: success paths -----------------------------------------------------------------------


def test_main_prints_one_row_per_prompt_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert1.json"), _golden_case("alert2.json")]
    )
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient([VALID_VERDICT_JSON] * 4)  # 2 cases x 2 prompt runs

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    lines = captured.out.rstrip("\n").splitlines()
    assert lines[0] == "| " + " | ".join(COLUMNS) + " |"
    assert lines[1] == "|" + "|".join("---" for _ in COLUMNS) + "|"
    assert len(lines) == 4  # header + separator + 2 body rows


def test_main_passes_prompt_versions_to_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v1_text = Path("worker/prompts/triage-v1.md").read_text()
    (tmp_path / "triage-v1.md").write_text(v1_text)
    (tmp_path / "triage-v2.md").write_text(
        v1_text + "\n\n<!-- test-marker-distinct-v2-sentence -->\n"
    )
    monkeypatch.setattr("worker.prompts.PROMPTS_DIR", tmp_path)

    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient([VALID_VERDICT_JSON, VALID_VERDICT_JSON])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--prompt",
            "triage-v2",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake,
    )

    assert rc == 0
    assert len(fake.calls) == 2
    system_v1 = fake.calls[0].messages[0]["content"]
    system_v2 = fake.calls[1].messages[0]["content"]
    assert system_v1 != system_v2
    assert "test-marker-distinct-v2-sentence" in system_v2
    assert "test-marker-distinct-v2-sentence" not in system_v1


def test_main_writes_result_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts_dir = tmp_path / "_prompts"
    prompts_dir.mkdir()
    v1_text = Path("worker/prompts/triage-v1.md").read_text()
    (prompts_dir / "triage-v1.md").write_text(v1_text)
    (prompts_dir / "triage-v2.md").write_text(v1_text + "\n\n<!-- v2 -->\n")
    monkeypatch.setattr("worker.prompts.PROMPTS_DIR", prompts_dir)

    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert1.json"), _golden_case("alert2.json")]
    )
    fake = FakeLLMClient([VALID_VERDICT_JSON] * 4)  # 2 cases x 2 prompt runs

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--prompt",
            "triage-v2",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path),
        ],
        llm=fake,
    )

    assert rc == 0
    written = sorted(tmp_path.glob("*.json"))  # non-recursive: excludes _prompts/*.md
    assert len(written) == 2
    metrics_fields = {f.name for f in fields(RunMetrics)}
    case_fields = {f.name for f in fields(CaseResult)}
    seen_prompts = set()
    for path in written:
        assert re.fullmatch(r"\d{8}T\d{6}Z-triage-v[12]\.json", path.name)
        payload = json.loads(path.read_text())
        assert set(payload.keys()) == {
            "prompt_version",
            "model",
            "git_sha",
            "started_at",
            "metrics",
            "cases",
            "golden",
            "strong_model",
        }
        # R47: --from-artifact's v2-only refusal reads this field back from the artifact.
        assert payload["golden"] == str(golden_path)
        # R52 (whole-branch fix wave, finding I1): the strong-tier id is read back by
        # `row_from_artifact` so a `--from-artifact` row's `models` cell matches a live row's --
        # always a str, `""` here since this fixture never escalates (no --strong-model, empty
        # STRONG_MODEL).
        assert isinstance(payload["strong_model"], str)
        assert payload["strong_model"] == ""
        assert payload["model"] == "fake-model"
        assert isinstance(payload["git_sha"], str) and payload["git_sha"]
        assert isinstance(payload["started_at"], str) and payload["started_at"]
        assert set(payload["metrics"].keys()) == metrics_fields
        assert len(payload["cases"]) == 2
        first_case = payload["cases"][0]
        assert set(first_case.keys()) == case_fields
        assert "case_id" in first_case
        assert "error" in first_case
        assert isinstance(first_case["verdict"], dict)
        seen_prompts.add(payload["prompt_version"])
    assert seen_prompts == {"triage-v1", "triage-v2"}


# --- main: failure paths (task-03 brief's failure-path table) -----------------------------------


def test_main_exit_1_on_missing_golden_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_path = tmp_path / "does-not-exist.jsonl"
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(
        [
            "--golden",
            str(missing_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path / "results"),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: invalid_golden:")
    assert "Traceback" not in captured.err


def test_main_usage_error_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(["--golden", str(golden_path)], llm=fake)  # no --prompt

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: usage:")


def test_main_exit_1_on_malformed_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MODEL_PRICES_JSON", "not-json")
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path / "results"),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "Traceback" not in captured.err


def test_main_exit_1_on_invalid_golden_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    alert_payload = json.loads((FIXTURES_DIR / "alert1.json").read_text())
    bad_row = {
        "alert": alert_payload,
        "label": {"severity": 9, "category": "scanning", "escalate": True},
        "labeler_note": "deliberately invalid severity for the invalid-golden-row failure path.",
        "tags": [],
    }
    golden_path = tmp_path / "invalid.jsonl"
    golden_path.write_text(json.dumps(bad_row) + "\n")
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path / "results"),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: invalid_golden:")
    assert "row 1" in lines[0]
    assert "Traceback" not in captured.err


def test_main_exit_1_on_unknown_prompt_before_any_case(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert1.json"), _golden_case("alert2.json")]
    )
    fake = FakeLLMClient([])  # no calls should ever reach it

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "nope",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path / "results"),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "Traceback" not in captured.err
    assert fake.calls == []


def test_main_exit_1_when_output_dir_not_writable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A regular file where a directory is needed (not `chmod`, unreliable as root/CI): the
    output directory can never be created/written under it."""
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    blocker = tmp_path / "file"
    blocker.write_text("x")
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(blocker / "out"),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: output_error:")
    assert "Traceback" not in captured.err


def test_main_exit_1_when_all_cases_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert1.json"), _golden_case("alert2.json")]
    )
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient(["{}", "{}", "{}", "{}"])  # both cases fail both attempts

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
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: all_cases_failed:")
    assert "Traceback" not in captured.err
    # Price before spend already happened; the run's JSON result is still written.
    written = list(output_dir.glob("*-triage-v1.json"))
    assert len(written) == 1


# --- fix round 1 (C1, I1): additive only, no existing test/helper changed above. ---------------


def test_main_exit_1_on_unpriced_model_flag_before_any_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """C1: an unpriced `--model` must fail as `config_error` *before any case runs*, via the
    real `llm=None` path (`OpenAICompatibleLLMClient.from_settings`), not escape `main` as a
    raised `ConfigError`/traceback from inside the async run. `LLM_BASE_URL` points at an
    unroutable address: if a call were ever attempted despite the price check, it would surface
    as `llm_call_failed` (or hang/time out), never silently as `config_error` — proving the
    price check really runs before any network attempt, not just before this fake would notice.
    """
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}',
    )
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9")
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--model",
            "unpriced-model",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path),
        ]
        # no llm= kwarg: exercises the real OpenAICompatibleLLMClient.from_settings path.
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "unpriced-model" in lines[0]
    assert "Traceback" not in captured.err
    assert list(tmp_path.glob("*.json")) == []


class _SleepingLLMClient(FakeLLMClient):
    """`FakeLLMClient` that sleeps briefly on its one call and records when it was called.

    Used only by `test_main_started_at_is_taken_before_the_run` to prove ordering: if
    `started_at` is captured before `run_golden` starts (as it must be), it is strictly earlier
    than the wall-clock time recorded inside this fake's (artificially slow) call.

    Overrides both `LLMClient` methods (m4 task-06): `evals.run.main` now always builds its
    `TriagePipeline` with tools attached (PRD §7.2 — external tools replay, the LLM is the only
    live component), so every call this test's single golden case makes goes through
    `complete_with_tools`, not `complete_structured`. Mirrors the same sleep-then-record shape so
    the ordering proof and the `called_at` self-check hold either way.
    """

    def __init__(self, responses: list[str | Exception]) -> None:
        super().__init__(responses)
        self.called_at: datetime | None = None

    async def complete_structured(self, *, messages: Any, response_model: Any, model: str) -> Any:
        await asyncio.sleep(0.05)
        self.called_at = datetime.now(UTC)
        return await super().complete_structured(
            messages=messages, response_model=response_model, model=model
        )

    async def complete_with_tools(
        self, *, messages: Any, tools: Any, response_model: Any, model: str
    ) -> Any:
        await asyncio.sleep(0.05)
        self.called_at = datetime.now(UTC)
        return await super().complete_with_tools(
            messages=messages, tools=tools, response_model=response_model, model=model
        )


def test_main_started_at_is_taken_before_the_run(tmp_path: Path) -> None:
    """I1: the result JSON's `started_at` must be stamped before `run_golden` runs, not after —
    otherwise it is not actually the run's start time.
    """
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    fake = _SleepingLLMClient([VALID_VERDICT_JSON])

    t0 = datetime.now(UTC)
    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path),
        ],
        llm=fake,
    )
    t1 = datetime.now(UTC)

    assert rc == 0
    written = list(tmp_path.glob("*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    started_at = datetime.fromisoformat(payload["started_at"])
    assert t0 <= started_at <= t1
    assert fake.called_at is not None
    assert started_at < fake.called_at


# --- m1 final-review fix wave (I2): additive only, no existing test/helper changed above. -------


@pytest.mark.parametrize("concurrency", ["-1", "0"])
def test_main_usage_error_on_nonpositive_concurrency(
    concurrency: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """I2: `--concurrency <= 0` must be a `usage` exit (argparse `type=` validation), never a
    `ValueError` traceback (`asyncio.Semaphore(-1)`) or a hang (`asyncio.Semaphore(0)` never
    releases). `llm=fake` has no queued replies, so if the validator ever let a nonpositive value
    through and the run actually reached the pipeline, this test would fail fast
    (`FakeLLMClient` raising `AssertionError` on an empty queue) rather than hang.
    """
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    fake = FakeLLMClient([])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            concurrency,
            "--output-dir",
            str(tmp_path / "results"),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: usage:")
    assert "Traceback" not in captured.err
    assert fake.calls == []


# --- m5 task-01 (N-M5): one registry per run, over an injected http= client main always closes ---


def test_main_closes_the_injected_http_client_on_success_and_failure(tmp_path: Path) -> None:
    """`evals.run.main` builds `build_registry(...)` exactly once per run — above the
    `--prompt` loop, never once per prompt version — over an injected `http=` client it always
    closes in a `finally`: on the success path (`rc == 0`, two prompt runs over the same
    registry) and on the `all_cases_failed` path (`rc == 1`), so a batch of prompt-version runs
    never leaks a connection pool. (`build_registry(` appearing exactly once in `evals/run.py`,
    above `for prompt_version in args.prompt`, is a review item with `file:line` — not something
    this black-box test can assert on its own.)
    """
    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert1.json"), _golden_case("alert2.json")]
    )
    output_dir = tmp_path / "results"
    output_dir.mkdir()

    ok_client = httpx.AsyncClient()
    fake_ok = FakeLLMClient([VALID_VERDICT_JSON] * 4)  # 2 cases x 2 prompt runs

    rc_ok = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--prompt",
            "triage-v2",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake_ok,
        http=ok_client,
    )

    assert rc_ok == 0
    assert ok_client.is_closed

    failing_client = httpx.AsyncClient()
    fake_failing = FakeLLMClient(["{}", "{}", "{}", "{}"])  # both cases fail every attempt

    rc_failed = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake_failing,
        http=failing_client,
    )

    assert rc_failed == 1
    assert failing_client.is_closed


# --- m5 task-03: --strong-model, PRD §6.4 ------------------------------------------------------


def test_strong_model_flag_flows_to_the_pipeline_and_the_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """3 golden cases, `--concurrency 1` so the fake's queue order is deterministic (rule 9): case
    0 runs to completion (cheap `VALID4`, then the strong pass `VALID4_STRONG` — 2 calls) before
    case 1 starts, then case 2 (1 non-escalating call each) — 1 of 3 cases escalates ->
    `escalation_rate == 1/3`, rendered `"0.33"` like every other ratio column.
    """
    golden_path = _write_golden(
        tmp_path / "golden.jsonl",
        [_golden_case("alert1.json"), _golden_case("alert2.json"), _golden_case("alert3.json")],
    )
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient([VALID4, VALID4_STRONG, VALID_VERDICT_JSON, VALID_VERDICT_JSON])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--strong-model",
            "strong-model",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert fake.calls[1].model == "strong-model"

    lines = captured.out.rstrip("\n").splitlines()
    body_cells = [cell.strip() for cell in lines[2].strip("|").split("|")]
    assert body_cells[COLUMNS.index("escalation_rate")] == "0.33"


def test_unpriced_strong_model_exit_1_before_any_case(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mirrors `test_main_exit_1_on_unpriced_model_flag_before_any_case` (C1) for `--strong-model`:
    the real-client path (`llm=None`) must fail as `config_error` naming the unpriced id, before
    any case runs — `CHEAP_MODEL` stays priced (the autouse fixture), only `--strong-model ghost`
    is absent from `MODEL_PRICES_JSON`.
    """
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--strong-model",
            "ghost",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ]
        # no llm= kwarg: exercises the real OpenAICompatibleLLMClient.from_settings path.
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "ghost" in lines[0]
    assert "Traceback" not in captured.err
    assert list(output_dir.glob("*.json")) == []


# --- m5 task-03 fix-1 (review I1): --strong-model equal to --model must not escape as a traceback


def test_strong_model_equal_to_model_exit_1_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--model",
            "fake-model",
            "--strong-model",
            "fake-model",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=FakeLLMClient([VALID_VERDICT_JSON]),
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert captured.err.startswith("error: config_error:")
    assert "Traceback" not in captured.err


# --- m7 task-01: `is_v2_golden` + `require_human=True` enforcement for v2 files (PRD §13) --------


def test_run_requires_human_labels_for_v2_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pins `evals.run.is_v2_golden` (m7 task-01 Ruling R1: "basename starts with v2", shared
    with task-02) and `main`'s use of it: a golden path `is_v2_golden` flags must be loaded via
    `evals.golden.load_golden(path, require_human=True)`, so a v2 file carrying even one row that
    is not `labeled_by: "human"` fails the whole run as `config_error` (Interfaces → test table
    row "run.py") rather than silently scoring a machine-authored label as ground truth (PRD §13,
    `.claude/rules/evals.md`). `evals.run` does not define `is_v2_golden` yet, so this test is RED
    at collection with `ImportError: cannot import name 'is_v2_golden' from 'evals.run'`.
    """
    from evals.run import is_v2_golden

    assert is_v2_golden(Path("evals/golden/v2.jsonl")) is True
    assert is_v2_golden(Path("/some/dir/v2-candidates.jsonl")) is True
    assert is_v2_golden(Path("evals/golden/v20-not-really-v2.jsonl")) is True  # starts with "v2"
    assert is_v2_golden(Path("evals/golden/v1.jsonl")) is False

    case = _golden_case("alert1.json")  # labeled_by defaults to None: not human-labeled
    golden_path = tmp_path / "v2-unlabeled.jsonl"
    golden_path.write_text(case.model_dump_json() + "\n")
    output_dir = tmp_path / "results"

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
        ],
        llm=FakeLLMClient([]),
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "Traceback" not in captured.err
    assert list(output_dir.glob("*.json")) == []


# --- m7 task-01 fix-1 (review I5/ruling R13): invalid_golden must stay reachable on v2 ------------


def test_run_v2_malformed_row_reports_invalid_golden(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ruling R13: only the loader's "row N is not human-labeled" `ValueError` maps to
    `config_error` for a v2 golden file; any OTHER `ValueError` (a malformed row, a failed
    `GoldenCase` validation, a duplicate `case_id`) must keep the existing `invalid_golden` code.
    Today `evals.run`'s `except ValueError: fail("config_error" if require_human else
    "invalid_golden", ...)` maps EVERY `ValueError` on a v2 path to `config_error`, making
    `invalid_golden` unreachable for v2 files (review finding I5) — this test pins the malformed-
    row half of I5; the pinned `test_run_requires_human_labels_for_v2_files` above already pins
    the non-human-row half (`config_error`).
    """
    bad_row = {
        "alert": {
            "source": "cowrie",
            "session_id": "i5-malformed",
            "src_ip": "203.0.113.6",
            "sensor": "hp-i5",
            "events": [
                {
                    "eventid": "cowrie.command.input",
                    "timestamp": "2026-09-06T00:00:00Z",
                    "session": "i5-malformed",
                    "src_ip": "203.0.113.6",
                    "sensor": "hp-i5",
                    "input": "echo not-a-connect-event",
                }
            ],
        },
        "label": {"severity": 2, "category": "scanning", "escalate": False},
        "labeler_note": "deliberately malformed alert for the v2 invalid_golden regression test.",
        "tags": [],
        "labeled_by": "human",
        "labeled_at": "2026-09-20T00:00:00Z",
    }
    golden_path = tmp_path / "v2-malformed.jsonl"
    golden_path.write_text(json.dumps(bad_row) + "\n")
    output_dir = tmp_path / "results"

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
        ],
        llm=FakeLLMClient([]),
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: invalid_golden:")
    assert "Traceback" not in captured.err
    assert list(output_dir.glob("*.json")) == []


# --- m7 task-03: --judge/--no-judge, per-case payload, judge cost separate from triage cost -------


def test_run_judges_each_case_with_replayed_tool_results_and_separate_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--judge` calls `evals.judge.judge_case` once per case, after that case's own verdict, over
    the SAME tool results the pipeline's own trace recorded (`TriageOutcome.tool_calls`) -- never
    a fresh/live tool call. Proven here by scripting case A's pipeline call to use the real
    `get_session_commands` tool over `alert4.json` (whose commands include `"cat /etc/passwd"`, a
    string that can only reach the judge's own LLM call if THAT case's recorded tool result was
    threaded through, not re-derived or shared across cases): it must appear in a judge call's own
    messages (found by `response_model is JudgeScore`, not by call index, so this test does not
    assume whether judging runs interleaved per case or in a second pass over the whole run).

    Judge cost accumulates separately (`RunMetrics.judge_cost_total_usd`) and must never move
    `cost_mean_usd`/`cost_total_usd` (PRD §7.3 / `.claude/rules/evals.md`: judge spend never
    inflates the triage cost gate) -- proven with exact `Decimal` equality: case A's own triage
    cost is two `FakeLLMClient` calls (the tool turn + the final verdict) and case B's is one, so
    `cost_total_usd` is exactly `0.000300` (never `0.000500`, which is what it would be if either
    case's judge call were folded in) and `cost_mean_usd` is exactly `0.000150`.

    `--no-judge` must call the judge for NO case at all -- `fake_no_judge` below queues no judge
    reply, so `FakeLLMClient` raises `AssertionError: no responses left` if the judge is still
    called despite the flag -- and every case's `judge`/`judge_cost_usd` in the written JSON, and
    `RunMetrics.judge_mean`/`judge_pct_le2`/`injection_pass_rate`, must read back `None`.

    `STRONG_MODEL`/its `MODEL_PRICES_JSON` entry are set only because `--judge-model` defaults to
    `STRONG_MODEL` (Interfaces); the two cases' verdicts stay at severity 2 / confidence 0.8 --
    below both `ESCALATE_SEVERITY_GTE` (default 4) and above `ESCALATE_CONFIDENCE_LT` (default
    0.6) -- so two-tier routing itself never fires and never adds a third LLM call per case.
    """
    from tests.fakes import ScriptedToolCall

    monkeypatch.setenv("STRONG_MODEL", "strong-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}, '
        '"strong-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}',
    )

    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert4.json"), _golden_case("alert1.json")]
    )
    output_dir = tmp_path / "results"
    output_dir.mkdir()

    judge_score_a = json.dumps(
        {
            "score": 4,
            "cites_evidence": True,
            "fabrication": False,
            "conclusion_follows": True,
            "rationale": "cites the observed commands from the tool result.",
        }
    )
    judge_score_b = json.dumps(
        {
            "score": 2,
            "cites_evidence": False,
            "fabrication": False,
            "conclusion_follows": False,
            "rationale": "conclusion is thin, only restates the summary.",
        }
    )
    fake = FakeLLMClient(
        [
            [
                ScriptedToolCall(
                    name="get_session_commands", arguments={"session_id": "4d5e6f708192"}
                )
            ],
            VALID_VERDICT_JSON,  # case A's cheap verdict, after the tool result is fed back
            judge_score_a,  # case A's judge score
            VALID_VERDICT_JSON,  # case B's cheap verdict (no tool call)
            judge_score_b,  # case B's judge score
        ]
    )

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--judge",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake,
    )

    assert rc == 0
    assert len(fake.calls) == 5

    judge_calls = [c for c in fake.calls if c.response_model.__name__ == "JudgeScore"]
    assert len(judge_calls) == 2
    assert any(
        "cat /etc/passwd" in str(message.get("content", ""))
        for call in judge_calls
        for message in call.messages
    )

    written = list(output_dir.glob("*-triage-v1.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    cases = payload["cases"]
    assert len(cases) == 2
    for case_payload in cases:
        assert case_payload["judge"] is not None
        assert case_payload["judge"]["score"] in (2, 4)
        assert Decimal(case_payload["judge_cost_usd"]) > Decimal("0")

    metrics = payload["metrics"]
    assert metrics["judge_mean"] == pytest.approx(3.0)  # (4 + 2) / 2
    assert metrics["judge_pct_le2"] == pytest.approx(0.5)  # only the score-2 case qualifies
    assert metrics["injection_pass_rate"] is None  # neither case is tagged "injection"
    assert Decimal(metrics["cost_mean_usd"]) == Decimal("0.000150")  # triage cost only
    assert Decimal(metrics["cost_total_usd"]) == Decimal("0.000300")  # triage cost only
    assert Decimal(metrics["judge_cost_total_usd"]) == Decimal("0.000200")  # 2 judge calls

    # --- --no-judge: the judge is never called; every judge field reads back None/zero -----------

    output_dir_no_judge = tmp_path / "results-no-judge"
    output_dir_no_judge.mkdir()
    fake_no_judge = FakeLLMClient(
        [
            [
                ScriptedToolCall(
                    name="get_session_commands", arguments={"session_id": "4d5e6f708192"}
                )
            ],
            VALID_VERDICT_JSON,
            VALID_VERDICT_JSON,
        ]
    )

    rc_no_judge = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--no-judge",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir_no_judge),
        ],
        llm=fake_no_judge,
    )

    assert rc_no_judge == 0
    assert len(fake_no_judge.calls) == 3  # the tool turn + one verdict call per case, no judging

    written_no_judge = list(output_dir_no_judge.glob("*-triage-v1.json"))
    assert len(written_no_judge) == 1
    payload_no_judge = json.loads(written_no_judge[0].read_text())
    for case_payload in payload_no_judge["cases"]:
        assert case_payload["judge"] is None
        assert Decimal(case_payload["judge_cost_usd"]) == Decimal("0")

    metrics_no_judge = payload_no_judge["metrics"]
    assert metrics_no_judge["judge_mean"] is None
    assert metrics_no_judge["judge_pct_le2"] is None
    assert metrics_no_judge["injection_pass_rate"] is None
    assert Decimal(metrics_no_judge["judge_cost_total_usd"]) == Decimal("0")


# --- m7 task-03 fix-1 (review findings I1, I2): judge-failure branch and both halves of R33 -----
# Additive only, appended at the end; no existing test/helper above is edited.


def test_judge_failure_records_judge_error_and_does_not_zero_other_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I1: a judge call whose own retry fails twice (`VerdictValidationError`) must not silently
    become a perfect score. `CaseResult.judge` reads back `None`, `judge_error` names the
    exception CLASS (never the message, which could carry attacker-echoed model text, ruling
    R40), the already-spent judge tokens are still counted (`judge_cost_usd > 0` -- the failed
    attempt's own spend, recovered from `VerdictValidationError.__cause__`, is never dropped,
    CONVENTIONS.md §7), the case's OWN triage verdict is untouched, and `RunMetrics.judge_mean` is
    computed over the other, successfully judged case only -- never treating the failed judgment
    as a 0 or silently dropping it from the run.
    """
    monkeypatch.setenv("STRONG_MODEL", "strong-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}, '
        '"strong-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}',
    )

    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert1.json"), _golden_case("alert2.json")]
    )
    output_dir = tmp_path / "results"
    output_dir.mkdir()

    judge_score_b = json.dumps(
        {
            "score": 4,
            "cites_evidence": True,
            "fabrication": False,
            "conclusion_follows": True,
            "rationale": "cites the summary's login counts.",
        }
    )
    fake = FakeLLMClient(
        [
            VALID_VERDICT_JSON,  # case A's triage verdict
            "{}",  # case A's judge attempt 1: invalid
            "{}",  # case A's judge attempt 2 (the one retry): invalid -> VerdictValidationError
            VALID_VERDICT_JSON,  # case B's triage verdict
            judge_score_b,  # case B's judge score
        ]
    )

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--judge",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake,
    )

    assert rc == 0
    assert len(fake.calls) == 5

    written = list(output_dir.glob("*-triage-v1.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    cases = payload["cases"]
    assert len(cases) == 2

    case_a, case_b = cases
    assert case_a["verdict"] is not None  # the triage result is untouched by the judge failure
    assert case_a["judge"] is None
    assert case_a["judge_error"] == "VerdictValidationError"
    assert Decimal(case_a["judge_cost_usd"]) > Decimal("0")

    assert case_b["judge"] is not None
    assert case_b["judge"]["score"] == 4
    assert case_b["judge_error"] is None

    metrics = payload["metrics"]
    assert metrics["judge_mean"] == pytest.approx(4.0)  # case B only -- case A excluded, not 0


def test_judge_default_on_for_v2_golden_requires_a_configured_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2(a)/(b): R33's default half, both directions. A v2-named golden with NO judge model
    configured and no `--judge`/`--no-judge` flag must not call the judge at all (the default
    resolves to `False`); the SAME golden with `STRONG_MODEL` configured (and priced) must call
    the judge (the default resolves to `True`) -- with no flag passed either way, only the model
    configuration differs.
    """
    from evals.judge import JudgeScore

    case = _golden_case("alert1.json").model_copy(update={"labeled_by": "human"})
    golden_path = _write_golden(tmp_path / "v2-default.jsonl", [case])

    # (a) STRONG_MODEL empty (the autouse fixture already clears it): no judge call.
    output_dir_a = tmp_path / "results-a"
    output_dir_a.mkdir()
    fake_a = FakeLLMClient([VALID_VERDICT_JSON])

    rc_a = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir_a),
        ],
        llm=fake_a,
    )

    assert rc_a == 0
    assert not any(c.response_model is JudgeScore for c in fake_a.calls)

    # (b) STRONG_MODEL configured and priced, still no --judge flag: the judge IS called.
    monkeypatch.setenv("STRONG_MODEL", "strong-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}, '
        '"strong-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}',
    )
    output_dir_b = tmp_path / "results-b"
    output_dir_b.mkdir()
    judge_reply = json.dumps(
        {
            "score": 3,
            "cites_evidence": True,
            "fabrication": False,
            "conclusion_follows": True,
            "rationale": "reasonable.",
        }
    )
    fake_b = FakeLLMClient([VALID_VERDICT_JSON, judge_reply])

    rc_b = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir_b),
        ],
        llm=fake_b,
    )

    assert rc_b == 0
    assert any(c.response_model is JudgeScore for c in fake_b.calls)


def test_explicit_judge_with_no_model_configured_is_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """I2(c): R33's `config_error` half. An EXPLICIT `--judge` with no judge model configured
    (`STRONG_MODEL` empty, no `--judge-model`) must exit 1 with a `config_error` BEFORE any LLM
    call -- never a silent no-op, and never a call made with an empty model id.
    """
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    fake = FakeLLMClient([])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--judge",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err.startswith("error: config_error:")
    assert len(fake.calls) == 0
    assert list(output_dir.glob("*.json")) == []


def test_judge_model_flag_and_default_route_to_the_right_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2(d): `--judge-model X` sends the judge call to `X`, not to `settings.strong_model`; with
    no `--judge-model` flag, the judge call goes to `settings.strong_model`. Proven on
    `call.model` itself, not merely on `response_model.__name__` -- a judge silently routed to the
    cheap model would still pass a `response_model`-only check.
    """
    from evals.judge import JudgeScore

    monkeypatch.setenv("STRONG_MODEL", "strong-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}, '
        '"strong-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}',
    )
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    judge_reply = json.dumps(
        {
            "score": 3,
            "cites_evidence": True,
            "fabrication": False,
            "conclusion_follows": True,
            "rationale": "reasonable.",
        }
    )

    # --judge-model overrides the default.
    output_dir_x = tmp_path / "results-x"
    output_dir_x.mkdir()
    fake_x = FakeLLMClient([VALID_VERDICT_JSON, judge_reply])
    rc_x = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--judge",
            "--judge-model",
            "custom-judge-model",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir_x),
        ],
        llm=fake_x,
    )
    assert rc_x == 0
    judge_calls_x = [c for c in fake_x.calls if c.response_model is JudgeScore]
    assert len(judge_calls_x) == 1
    assert judge_calls_x[0].model == "custom-judge-model"

    # No --judge-model: defaults to settings.strong_model.
    output_dir_default = tmp_path / "results-default"
    output_dir_default.mkdir()
    fake_default = FakeLLMClient([VALID_VERDICT_JSON, judge_reply])
    rc_default = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--judge",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir_default),
        ],
        llm=fake_default,
    )
    assert rc_default == 0
    judge_calls_default = [c for c in fake_default.calls if c.response_model is JudgeScore]
    assert len(judge_calls_default) == 1
    assert judge_calls_default[0].model == "strong-model"


def test_judge_prompt_validated_before_any_case_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """I4: the judge prompt version is validated ONCE, up front, before any case runs -- the same
    "price before spend" guarantee the triage prompt already gets at `TriagePipeline.__init__`. A
    bad `JUDGE_PROMPT_VERSION` with an explicit `--judge` must exit 1 as a `config_error` with the
    FakeLLMClient recording ZERO calls, never burning a case's triage spend before the judge
    prompt failure surfaces (which, before this fix, happened lazily inside the first case's own
    judge call, after that case's full triage run had already been paid for).
    """
    monkeypatch.setenv("STRONG_MODEL", "strong-model")
    monkeypatch.setenv("JUDGE_PROMPT_VERSION", "judge-v999")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}, '
        '"strong-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}',
    )
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    fake = FakeLLMClient([])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--judge",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err.startswith("error: config_error:")
    assert len(fake.calls) == 0
    assert list(output_dir.glob("*.json")) == []


# --- m7 task-04 ruling R42 (carried from task-03's re-review): a judge LLMCallError is recorded,
# never fatal --------------------------------------------------------------------------------


def test_judge_llm_call_error_is_recorded_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling R42: a judge call that fails with `core.errors.LLMCallError` (a transport failure,
    never a validation failure) is recorded as `CaseResult.judge=None`/
    `judge_error="LLMCallError"` -- the SAME non-fatal treatment
    `StructuredOutputError`/`VerdictValidationError` already get (ruling R40, m7 task-03 fix-1) --
    never left to escape `run_golden` as an unhandled exception; the case's own (already-succeeded)
    triage verdict is untouched.

    REGRESSION PIN: `evals/run.py::_judge`'s `except (StructuredOutputError,
    VerdictValidationError, LLMCallError)` tuple already names `LLMCallError` today, so this test
    passes on arrival -- it pins that the implementer must not narrow that tuple back down while
    touching this file for task-04's other changes (verified by mutation: removing `LLMCallError`
    from that tuple in an rsync scratch copy must make this test fail).
    """
    monkeypatch.setenv("STRONG_MODEL", "strong-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}, '
        '"strong-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}',
    )

    case = _golden_case("alert1.json").model_copy(update={"labeled_by": "human"})
    golden_path = _write_golden(tmp_path / "v2-judge-llm-error.jsonl", [case])
    output_dir = tmp_path / "results"
    output_dir.mkdir()

    fake = FakeLLMClient([VALID_VERDICT_JSON, LLMCallError("transient judge transport failure")])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--judge",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake,
    )

    assert rc == 0
    assert len(fake.calls) == 2

    written = list(output_dir.glob("*-triage-v1.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    cases = payload["cases"]
    assert len(cases) == 1
    case_payload = cases[0]
    assert case_payload["verdict"] is not None
    assert case_payload["judge"] is None
    assert case_payload["judge_error"] == "LLMCallError"

    metrics = payload["metrics"]
    assert metrics["judge_mean"] is None  # the only case's judgment failed -- never scored 0


# --- m7 task-04 (ruling R35): --database-url writes an eval_runs row after each prompt version ---


def test_main_writes_eval_runs_row_when_database_url_given(
    tmp_schema: tuple[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ruling R35: with `--database-url`/`--schema` given, `evals.run` opens ONE session
    (`core.db.make_engine`/`make_session_factory`, the same pattern the sampler's own
    `--database-url`/`--schema` test uses, `tests/test_sample.py::test_main_writes_file_and_
    exits_zero`) after each prompt version is scored, writes one `eval_runs` row via
    `evals.publish.write_eval_run_row` with `model_config = {"model", "judge_model",
    "replay_strict", "judge"}` (the run's own effective config), commits it, and prints
    `eval_runs: <uuid> (<prompt_version>)` on stdout after the table.

    `evals.run` does not know `--database-url`/`--schema` yet, so this test is RED today: argparse
    rejects them as unrecognized arguments (`core.cli.Parser` -> `UsageError` -> `rc == 1`,
    `error: usage: ...`), not the `rc == 0` this test asserts.
    """
    from core.db import make_engine, make_session_factory
    from core.models.eval_runs import EvalRunRow

    url, schema = tmp_schema
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient([VALID_VERDICT_JSON])

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
            url,
            "--schema",
            schema,
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    out_lines = captured.out.rstrip("\n").splitlines()
    assert re.fullmatch(r"eval_runs: [0-9a-f-]{36} \(triage-v1\)", out_lines[-1])

    async def _read_back() -> list[EvalRunRow]:
        engine = make_engine(url, schema=schema)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                result = await session.execute(select(EvalRunRow))
                return list(result.scalars().all())
        finally:
            await engine.dispose()

    rows = asyncio.run(_read_back())
    assert len(rows) == 1
    assert rows[0].prompt_version == "triage-v1"
    assert rows[0].model_config == {
        "model": "fake-model",
        "judge_model": "",
        "replay_strict": False,
        "judge": False,
        "strong_model": "",
        "prompt_version": "triage-v1",
    }
    assert rows[0].metrics is not None
    assert rows[0].metrics["cost_total_usd"] == "0.000100"


def test_main_without_database_url_touches_no_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling R35: without `--database-url`, no DB code runs at all -- `evals.run` never calls
    `evals.publish.write_eval_run_row`, so the existing DB-less test suite stays green. Proven by
    monkeypatching `evals.run.write_eval_run_row` to raise if it is ever called.

    `evals.run` does not import that name yet, so this test is RED today with `AttributeError`
    (`monkeypatch.setattr` on an attribute the target module doesn't have).
    """

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("write_eval_run_row must not be called without --database-url")

    monkeypatch.setattr("evals.run.write_eval_run_row", _boom)

    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient([VALID_VERDICT_JSON])

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
        ],
        llm=fake,
    )

    assert rc == 0


# --- m7 task-05: --gate / --baseline / --write-baseline ------------------------------------------
# (ruling R44's edit is above, in the task-04 pinned test) ----------------------------------------


_GATE_PLACEHOLDER_PR = PR(precision=0.0, recall=0.0, support=0)


def _gate_baseline_metrics(
    *, severity_exact: float, critical_recall: float, cost_mean_usd: Decimal
) -> RunMetrics:
    """A minimal `RunMetrics` for a hand-written baseline file -- only the three PRD §7.4
    gate-relevant fields are meaningful; everything else is a fixed placeholder
    `evals.gate.evaluate_gate` never reads."""
    return RunMetrics(
        n_cases=2,
        n_failed=0,
        severity_exact=severity_exact,
        severity_within_one=severity_exact,
        category_accuracy=1.0,
        escalate_precision=1.0,
        escalate_recall=1.0,
        critical_recall=critical_recall,
        escalation_rate=0.0,
        cost_mean_usd=cost_mean_usd,
        cost_p95_usd=cost_mean_usd,
        cost_total_usd=cost_mean_usd * 2,
        latency_p50_ms=10,
        latency_p95_ms=10,
        per_severity=dict.fromkeys(range(1, 6), _GATE_PLACEHOLDER_PR),
        confusion=tuple(tuple(0 for _ in range(6)) for _ in range(5)),
        category_confusion={},
        sev_macro_f1=1.0,
    )


def _write_baseline_file(
    path: Path,
    *,
    metrics: RunMetrics,
    model_config: dict[str, Any],
    prompt_version: str = "triage-v1",
) -> None:
    """Hand-writes a baseline JSON in the shape ruling R43 pins (`Baseline` serialized as
    `{"recorded_at", "git_sha", "prompt_version", "model_config", "metrics":
    metrics_payload(...)}`) directly -- never through `evals.gate.write_baseline` (which does not
    exist yet) -- so these tests only exercise `evals.run --gate`'s own wiring."""
    path.write_text(
        json.dumps(
            {
                "recorded_at": "2026-09-01T00:00:00+00:00",
                "git_sha": "baseline0",
                "prompt_version": prompt_version,
                "model_config": model_config,
                "metrics": metrics_payload(metrics),
            }
        )
    )


def test_gate_flag_without_baseline_exits_1_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ruling (Step 6 blocked on the owner's v2 labels): `evals/baseline.json` ships ABSENT until
    a real v2 run writes one -- the default nightly-day state until then. `evals.run --gate`
    against a missing baseline must exit 1 with a `config_error` naming the missing path and
    pointing at `--write-baseline`, never a crash, a silent pass, or an invented baseline.

    `evals.run` has no `--gate`/`--baseline` flags yet, so this fails RED today with a `usage`
    error from argparse's "unrecognized arguments", not the `config_error` asserted here.
    """
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    baseline_path = tmp_path / "no-such-baseline.json"
    fake = FakeLLMClient([VALID_VERDICT_JSON])

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
            "--gate",
            "--baseline",
            str(baseline_path),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert str(baseline_path) in lines[0]
    assert "--write-baseline" in lines[0]


def test_gate_flag_exits_1_and_prints_conditions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`evals.run --gate` evaluates the active prompt's row against a committed baseline
    (`evals.gate.evaluate_gate`) and exits 1 naming every tripped condition when one trips
    (Interfaces: prints "GATE: PASS" or "GATE: FAIL <cond>=<details>" after the table).

    The baseline's `model_config["prompt_version"]` is deliberately `"other-prompt-version"`,
    never equal to this run's own `"triage-v1"` -- the cost condition only applies "while the
    run's model_config equals the baseline's" (PRD §7.4), so it can never trip here regardless of
    cost values, isolating this test to `severity_exact_drop` alone: case A's verdict matches its
    critical (severity 4) label exactly, so `critical_recall` is 1.0 (at/above the 0.90 default,
    never gated against the baseline at all), while case B's verdict (severity 5) mismatches its
    severity-2 label, so the run's `severity_exact` is 0.5 against a baseline of 1.0 -- a 50-point
    drop, far past the default 3-point allowance.

    `evals.run` has no `--gate`/`--baseline` flags yet, so this fails RED today with a `usage`
    error from argparse's "unrecognized arguments".
    """
    case_a = GoldenCase(
        alert=_alert("alert1.json"),
        label=GoldenLabel(severity=4, category="brute_force", escalate=True),
        labeler_note="critical-severity case for the --gate CLI conditions test.",
    )
    case_b = GoldenCase(
        alert=_alert("alert2.json"),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="non-critical case, deliberately mismatched by the scripted verdict below.",
    )
    golden_path = _write_golden(tmp_path / "golden.jsonl", [case_a, case_b])
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    baseline_path = tmp_path / "baseline.json"

    verdict_a = json.dumps(
        {
            "severity": 4,
            "category": "brute_force",
            "confidence": 0.8,
            "reasoning": "matches the golden label exactly for the --gate CLI conditions test.",
            "recommended_action": "escalate and rotate credentials.",
            "escalate": True,
        }
    )
    verdict_b = json.dumps(
        {
            "severity": 5,
            "category": "brute_force",
            "confidence": 0.8,
            "reasoning": "deliberately mismatched severity for the --gate CLI conditions test.",
            "recommended_action": "escalate immediately.",
            "escalate": True,
        }
    )
    fake = FakeLLMClient([verdict_a, verdict_b])

    _write_baseline_file(
        baseline_path,
        metrics=_gate_baseline_metrics(
            severity_exact=1.0, critical_recall=1.0, cost_mean_usd=Decimal("0")
        ),
        model_config={
            "model": "fake-model",
            "judge_model": "",
            "replay_strict": False,
            "judge": False,
            "strong_model": "",
            "prompt_version": "other-prompt-version",  # deliberately != this run's own
        },
        prompt_version="triage-v1",
    )

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
            "--gate",
            "--baseline",
            str(baseline_path),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err == ""
    assert "| " + " | ".join(COLUMNS) + " |" in captured.out  # the table still prints
    assert "GATE: FAIL" in captured.out
    assert "severity_exact_drop" in captured.out


def test_write_baseline_refuses_existing_and_non_v2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`evals.run --write-baseline` refuses in two situations (Interfaces), both `config_error`,
    neither ever writing/modifying the target file: a NON-v2 golden file (a baseline may only be
    recorded from a real v2 run, PRD §7.4/§13), and an EXISTING baseline file without
    `--force-baseline` (a baseline is committed by the controller once, never silently clobbered
    by a routine run).

    `evals.run` has no `--write-baseline`/`--baseline` flags yet, so this fails RED today with a
    `usage` error from argparse's "unrecognized arguments".
    """
    output_dir = tmp_path / "results"
    output_dir.mkdir()

    # --- refusal 1: a v1 (non-v2) golden file --------------------------------------------------
    v1_golden = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    v1_baseline_path = tmp_path / "baseline-v1-attempt.json"
    fake_v1 = FakeLLMClient([VALID_VERDICT_JSON])

    rc_v1 = main(
        [
            "--golden",
            str(v1_golden),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
            "--write-baseline",
            "--baseline",
            str(v1_baseline_path),
        ],
        llm=fake_v1,
    )

    captured_v1 = capsys.readouterr()
    assert rc_v1 == 1
    assert captured_v1.err.startswith("error: config_error:")
    assert not v1_baseline_path.exists()

    # --- refusal 2: an existing baseline file, no --force-baseline -----------------------------
    v2_case = GoldenCase(
        alert=_alert("alert1.json"),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="human-labeled v2 case for the --write-baseline refusal test.",
        labeled_by="human",
        labeled_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    v2_golden = _write_golden(tmp_path / "v2.jsonl", [v2_case])
    existing_baseline_path = tmp_path / "baseline-exists.json"
    existing_baseline_marker = '{"marker": "pre-existing baseline, must not change"}'
    existing_baseline_path.write_text(existing_baseline_marker)
    fake_v2 = FakeLLMClient([VALID_VERDICT_JSON])

    rc_v2 = main(
        [
            "--golden",
            str(v2_golden),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
            "--write-baseline",
            "--baseline",
            str(existing_baseline_path),
        ],
        llm=fake_v2,
    )

    captured_v2 = capsys.readouterr()
    assert rc_v2 == 1
    assert captured_v2.err.startswith("error: config_error:")
    assert existing_baseline_path.read_text() == existing_baseline_marker


# --- m7 task-06: `--publish` appends a docs/results.md row and (optionally) writes the eval_runs
# row task-04 already writes; `--from-artifact` republishes one row with no LLM call (ruling R41)
# -------------------------------------------------------------------------------------------------


def _seed_results_doc(results_path: Path) -> None:
    """Seed a tmp results file with `docs/results.md`'s real shape (header/separator under
    `## Runs`, trailing prose below) -- LOCAL import of `render_header` (not yet defined), so this
    helper only fails the tests that call it, never the whole module at collection."""
    from evals.publish import render_header

    n_cols = len(render_header().strip().strip("|").split("|"))
    separator = "|" + "|".join("---" for _ in range(n_cols)) + "|"
    results_path.write_text(
        "# Evaluation results\n\n## Runs\n\n"
        f"{render_header()}\n{separator}\n\n_No published runs yet._\n"
    )


def _published_rows(results_path: Path) -> list[list[str]]:
    """Every body row (never the header/separator) under `## Runs` in `results_path`, split into
    cells."""
    lines = [
        line
        for line in results_path.read_text().splitlines()
        if line.strip().startswith("|") and "date" not in line and "---" not in line
    ]
    return [[c.strip() for c in line.strip("|").split("|")] for line in lines]


def test_publish_refuses_v1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--publish` refuses a non-v2 golden file before any case runs -- v1 numbers are never
    published (`.claude/rules/evals.md`). `evals.run` has no `--publish` flag yet, so this fails
    RED today with a `usage` error from argparse's "unrecognized arguments", not the
    `config_error` asserted here.
    """
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient([VALID_VERDICT_JSON])

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
            "--publish",
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "Traceback" not in captured.err


def test_publish_appends_row_to_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--publish` appends exactly one row to `RESULTS_PATH` (in `COLUMNS` order) per `--prompt`
    row and prints `"published <n> row(s) to <path>"` -- proven against a temp results file
    (never the real `docs/results.md`) by monkeypatching `evals.run.RESULTS_PATH`, the same
    "import the name, monkeypatch it on the importing module" pattern this file's task-04 section
    already uses for `evals.run.write_eval_run_row`.

    `evals.run` does not import `RESULTS_PATH` yet, so this fails RED today with `AttributeError`
    (`monkeypatch.setattr` on an attribute `evals.run` doesn't have).
    """
    results_path = tmp_path / "results.md"
    _seed_results_doc(results_path)
    monkeypatch.setattr("evals.run.RESULTS_PATH", results_path)

    case = GoldenCase(
        alert=_alert("alert1.json"),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="human-labeled v2 case for the --publish append happy-path test.",
        labeled_by="human",
        labeled_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    golden_path = tmp_path / "v2.jsonl"
    golden_path.write_text(case.model_dump_json() + "\n")
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient([VALID_VERDICT_JSON])

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
            "--publish",
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert f"published 1 row(s) to {results_path}" in captured.out

    rows = _published_rows(results_path)
    assert len(rows) == 1
    assert rows[0][2] == "triage-v1"  # prompt_version
    assert rows[0][3] == "fake-model"  # models: no --strong-model given


def test_publish_from_artifact_appends_without_llm_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--publish --from-artifact PATH` republishes exactly the artifact's own row and makes NO
    LLM call at all -- `--golden`/`--prompt` are ignored with it (ruling R41). `FakeLLMClient([])`
    has zero queued responses, so any call at all raises inside the fake before this test's own
    assertions would even run.

    `evals.run` has no `--from-artifact` flag yet, so this fails RED today with a `usage` error
    from argparse's "unrecognized arguments".
    """
    from evals.publish import metrics_payload
    from evals.scoring import score

    results_path = tmp_path / "results.md"
    _seed_results_doc(results_path)
    monkeypatch.setattr("evals.run.RESULTS_PATH", results_path)

    payload = {
        "prompt_version": "triage-v4",
        "model": "gpt-4o-mini",
        "git_sha": "1a2b3c4",
        "started_at": "2026-09-19T00:00:00+00:00",
        "metrics": metrics_payload(score([])),
        "golden": "evals/golden/v2.jsonl",
        "cases": [],
    }
    artifact_path = tmp_path / "20260919T000000Z-triage-v4.json"
    artifact_path.write_text(json.dumps(payload))

    fake = FakeLLMClient([])  # any LLM call at all fails this test

    rc = main(
        [
            "--golden",
            str(tmp_path / "ignored.jsonl"),  # never read: --from-artifact skips loading it
            "--prompt",
            "ignored",
            "--publish",
            "--from-artifact",
            str(artifact_path),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert fake.calls == []
    assert "published 1 row(s) to" in captured.out

    rows = _published_rows(results_path)
    assert len(rows) == 1
    assert rows[0][0] == "2026-09-19"  # date, taken from the artifact's started_at
    assert rows[0][1] == "1a2b3c4"  # git_sha, taken from the artifact
    assert rows[0][2] == "triage-v4"  # prompt_version, taken from the artifact


def test_publish_writes_eval_runs_row(
    tmp_schema: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--publish` and `--database-url` are independent flags that never double-write: combining
    both on the same run writes exactly ONE `eval_runs` row (the task-04/ruling-R35 write) AND
    appends exactly one `docs/results.md` row -- `--publish` never triggers a second DB write of
    its own.

    `evals.run` has no `--publish` flag yet, so this fails RED today with a `usage` error from
    argparse's "unrecognized arguments".
    """
    from core.db import make_engine, make_session_factory
    from core.models.eval_runs import EvalRunRow

    url, schema = tmp_schema
    results_path = tmp_path / "results.md"
    _seed_results_doc(results_path)
    monkeypatch.setattr("evals.run.RESULTS_PATH", results_path)

    case = GoldenCase(
        alert=_alert("alert1.json"),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="human-labeled v2 case for the --publish + --database-url test.",
        labeled_by="human",
        labeled_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    golden_path = tmp_path / "v2.jsonl"
    golden_path.write_text(case.model_dump_json() + "\n")
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient([VALID_VERDICT_JSON])

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
            "--publish",
            "--database-url",
            url,
            "--schema",
            schema,
        ],
        llm=fake,
    )

    assert rc == 0

    async def _read_back() -> list[EvalRunRow]:
        engine = make_engine(url, schema=schema)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                result = await session.execute(select(EvalRunRow))
                return list(result.scalars().all())
        finally:
            await engine.dispose()

    rows = asyncio.run(_read_back())
    assert len(rows) == 1  # never double-written by combining --publish with --database-url
    assert rows[0].prompt_version == "triage-v1"

    published = _published_rows(results_path)
    assert len(published) == 1  # the docs/results.md row is also appended
