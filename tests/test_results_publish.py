"""Pins `evals.publish`'s task-06 additions: `render_row`/`render_header`, the append-only
`docs/results.md` writer (`append_result_row`), `regenerate_header`, and `row_from_artifact` (PRD
§7.5, m7 task-06, rulings R41/R47).

`evals.publish` does not define any of these yet, so every test in this module is RED at
collection with `ImportError: cannot import name '<name>' from 'evals.publish'`, not merely at
first use.

`render_header`/`append_result_row`/`regenerate_header` never touch the real `docs/results.md` —
every test below builds its own tiny fixture file under `tmp_path` (CONVENTIONS.md §10 rule
mirrored here: never mutate a real, append-only artifact from a test). `metrics_from_payload` is
already pinned by `tests/test_gate.py::test_metrics_from_payload_round_trips` (ruling R43) and is
not re-pinned here (task-06's Deliverables note: "skip if so").
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from core.errors import ConfigError
from evals.publish import (
    PublishedRun,
    append_result_row,
    metrics_payload,
    regenerate_header,
    render_header,
    render_row,
    row_from_artifact,
)
from evals.scoring import COLUMNS, PR, ResultRow, RunMetrics


def _sample_metrics() -> RunMetrics:
    """A `RunMetrics` with every field set to a distinct, hand-checkable value so the rendered
    row's formatting (2dp rates, 6dp costs, plain-int latencies, `-` for `None`) is pinned
    exactly, not merely "some number appeared somewhere"."""
    return RunMetrics(
        n_cases=214,
        n_failed=0,
        severity_exact=0.71,
        severity_within_one=0.93,
        category_accuracy=0.85,
        escalate_precision=0.62,
        escalate_recall=0.77,
        critical_recall=0.95,
        escalation_rate=0.14,
        cost_mean_usd=Decimal("0.000412"),
        cost_p95_usd=Decimal("0.000890"),
        cost_total_usd=Decimal("0.088168"),
        latency_p50_ms=1820,
        latency_p95_ms=3400,
        per_severity={
            1: PR(precision=0.50, recall=0.50, support=10),
            2: PR(precision=0.60, recall=0.60, support=20),
            3: PR(precision=0.70, recall=0.70, support=30),
            4: PR(precision=0.90, recall=0.95, support=40),
            5: PR(precision=0.85, recall=0.80, support=24),
        },
        confusion=tuple(tuple(0 for _ in range(6)) for _ in range(5)),
        category_confusion={},
        sev_macro_f1=0.79,
    )


_EXPECTED_METRIC_CELLS = [
    "214",
    "0",
    "0.71",
    "0.93",
    "0.85",
    "0.62",
    "0.77",
    "0.95",
    "0.14",
    "0.000412",
    "0.000890",
    "0.088168",
    "1820",
    "3400",
    "0.95",
    "0.80",
    "0.79",
    "-",
    "-",
    "-",
    "0.000000",
]
"""`_sample_metrics()`'s cells in `COLUMNS[2:]` order (`COLUMNS[0]`/`[1]` -- `"prompt"`/`"model"`
-- are replaced by `prompt_version`/`models` in a published row, per `test_results_doc.py`'s
already-pinned header shape)."""


def _n_columns() -> int:
    """The published row's cell count, derived from `render_header()` itself (never
    hand-counted): `date, git_sha, prompt_version, models` plus every `COLUMNS[2:]` metric."""
    return len(render_header().strip().strip("|").split("|"))


def _separator() -> str:
    return "|" + "|".join("---" for _ in range(_n_columns())) + "|"


def _seed_results_doc() -> str:
    """A tiny stand-in for `docs/results.md`'s real shape: prose sections, a `## Runs` heading,
    the header/separator, and trailing prose BELOW the (currently empty) table body -- the exact
    shape the real file ships today (`docs/results.md`'s `_No published runs yet..._` line), so
    `append_result_row` is proven to insert before that trailing prose, not at the file's end."""
    return (
        "# Evaluation results\n\n"
        "## Publication policy\n\n"
        "- policy prose unrelated to the append mechanism under test.\n\n"
        "## Gate\n\n"
        "gate prose unrelated to the append mechanism under test.\n\n"
        "## Runs\n\n"
        f"{render_header()}\n"
        f"{_separator()}\n"
        "\n"
        "_No published runs yet -- placeholder unrelated to the append mechanism under test._\n"
    )


# --- render_row / render_header -----------------------------------------------------------------


def test_render_row_follows_columns_order_and_formats() -> None:
    row = ResultRow(prompt_version="triage-v4", model="gpt-4o-mini", metrics=_sample_metrics())

    line = render_row(row, date=date(2026, 9, 20), git_sha="1a2b3c4", models="gpt-4o-mini")

    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    assert len(cells) == len(COLUMNS) + 2  # date, git_sha, prompt_version, models + COLUMNS[2:]
    assert cells == ["2026-09-20", "1a2b3c4", "triage-v4", "gpt-4o-mini", *_EXPECTED_METRIC_CELLS]

    # The models cell carries the caller's already-computed escalation marker verbatim, U+2192
    # included -- render_row never re-derives it (ruling: `models` is computed by `evals.run`
    # from its effective config, render_row only renders the string it is handed).
    escalated_line = render_row(
        row, date=date(2026, 9, 20), git_sha="1a2b3c4", models="gpt-4o-mini→gpt-5.4"
    )
    escalated_cells = [c.strip() for c in escalated_line.strip().strip("|").split("|")]
    assert escalated_cells[3] == "gpt-4o-mini→gpt-5.4"
    assert "→" in escalated_cells[3]


# --- append_result_row: append-only ---------------------------------------------------------------


def test_append_result_row_only_appends(tmp_path: Path) -> None:
    path = tmp_path / "results.md"
    original = _seed_results_doc()
    path.write_text(original)
    before_lines = original.splitlines()
    header_idx = before_lines.index(render_header())
    assert before_lines[header_idx + 1] == _separator()
    insert_idx = header_idx + 2  # immediately after the separator, before any trailing prose

    row = ResultRow(prompt_version="triage-v4", model="gpt-4o-mini", metrics=_sample_metrics())
    line_a = render_row(row, date=date(2026, 9, 20), git_sha="1a2b3c4", models="gpt-4o-mini")

    append_result_row(path, line_a)

    after_lines = path.read_text().splitlines()
    # every prior line byte-identical, in the same relative order, with exactly `line_a` spliced
    # in right after the separator -- proves the append never touches a line before OR after it
    # (in particular, the trailing "_No published runs yet..._" prose below the table survives).
    assert after_lines == before_lines[:insert_idx] + [line_a] + before_lines[insert_idx:]
    assert len(after_lines) == len(before_lines) + 1  # exactly one new line

    # A second append lands after the first -- never overwriting it, never reordering it.
    line_b = render_row(row, date=date(2026, 9, 21), git_sha="2b3c4d5", models="gpt-4o-mini")
    append_result_row(path, line_b)

    final_lines = path.read_text().splitlines()
    assert final_lines == before_lines[:insert_idx] + [line_a, line_b] + before_lines[insert_idx:]
    assert len(final_lines) == len(before_lines) + 2


def test_append_refuses_on_header_drift(tmp_path: Path) -> None:
    path = tmp_path / "results.md"
    header_cells = [c.strip() for c in render_header().strip().strip("|").split("|")]
    stale_header = "| " + " | ".join(header_cells[:-1]) + " |"  # one column short of COLUMNS
    original = _seed_results_doc().replace(render_header(), stale_header, 1)
    path.write_text(original)

    row = ResultRow(prompt_version="triage-v4", model="gpt-4o-mini", metrics=_sample_metrics())
    line = render_row(row, date=date(2026, 9, 20), git_sha="1a2b3c4", models="gpt-4o-mini")

    with pytest.raises(ValueError):
        append_result_row(path, line)

    assert path.read_text() == original  # a refused append never touches the file


# --- regenerate_header: rows byte-preserved -------------------------------------------------------


def test_regenerate_header_keeps_rows(tmp_path: Path) -> None:
    path = tmp_path / "results.md"
    old_header = "| date | git_sha | prompt_version | models | old_only_column |"
    old_separator = "|---|---|---|---|---|"
    row_1 = "| 2026-09-01 | aaa1111 | triage-v1 | gpt-4o-mini | 1 |"
    row_2 = "| 2026-09-02 | bbb2222 | triage-v2 | gpt-4o-mini | 2 |"
    original = (
        "# Evaluation results\n\n"
        "## Runs\n\n"
        f"{old_header}\n{old_separator}\n{row_1}\n{row_2}\n"
        "\n_trailing prose the regeneration must not disturb._\n"
    )
    path.write_text(original)

    regenerate_header(path)

    lines = path.read_text().splitlines()
    assert row_1 in lines  # rows byte-preserved, unchanged content
    assert row_2 in lines
    assert old_header not in lines
    assert old_separator not in lines
    new_header_idx = lines.index(render_header())
    assert lines[new_header_idx + 1] == _separator()
    assert lines[new_header_idx + 2] == row_1
    assert lines[new_header_idx + 3] == row_2
    assert "_trailing prose the regeneration must not disturb._" in lines


# --- row_from_artifact: rebuild a PublishedRun from a per-run JSON, no LLM call -------------------


def test_row_from_artifact_rebuilds_a_row(tmp_path: Path) -> None:
    metrics = _sample_metrics()
    payload = {
        "prompt_version": "triage-v4",
        "model": "gpt-4o-mini",
        "git_sha": "1a2b3c4",
        "started_at": "2026-09-20T12:34:56+00:00",
        "metrics": metrics_payload(metrics),
        "golden": "evals/golden/v2.jsonl",
        "cases": [],
    }
    artifact = tmp_path / "20260920T123456Z-triage-v4.json"
    artifact.write_text(json.dumps(payload))

    published = row_from_artifact(artifact)

    assert isinstance(published, PublishedRun)
    assert published.row.prompt_version == "triage-v4"
    assert published.row.model == "gpt-4o-mini"
    assert published.row.metrics == metrics  # round-tripped via metrics_from_payload
    assert published.git_sha == "1a2b3c4"
    assert published.models == "gpt-4o-mini"  # no strong-model info in the per-run JSON (R46)
    assert published.date == date(2026, 9, 20)


def test_row_from_artifact_refuses_non_v2(tmp_path: Path) -> None:
    payload = {
        "prompt_version": "triage-v1",
        "model": "fake-model",
        "git_sha": "abc1234",
        "started_at": "2026-09-20T12:00:00+00:00",
        "metrics": metrics_payload(_sample_metrics()),
        "golden": "evals/golden/v1.jsonl",  # not v2-named -> refused, v1 is never published
        "cases": [],
    }
    artifact = tmp_path / "artifact.json"
    artifact.write_text(json.dumps(payload))

    with pytest.raises((ValueError, ConfigError)):
        row_from_artifact(artifact)


# --- row_from_artifact: fix-1 (I1) -- a malformed artifact raises ValueError, never a traceback --


def test_row_from_artifact_raises_value_error_on_missing_key(tmp_path: Path) -> None:
    """A pre-R47 artifact (no `"golden"` key) or one missing `"metrics"` raises `ValueError`,
    never the raw `KeyError`/`TypeError` -- `evals.run`'s "never raises a traceback" contract
    (fix-1, I1) and this function's own docstring both promise `ValueError` here."""
    base_payload = {
        "prompt_version": "triage-v4",
        "model": "gpt-4o-mini",
        "git_sha": "1a2b3c4",
        "started_at": "2026-09-20T12:34:56+00:00",
        "metrics": metrics_payload(_sample_metrics()),
        "golden": "evals/golden/v2.jsonl",
        "cases": [],
    }

    missing_golden = {k: v for k, v in base_payload.items() if k != "golden"}
    artifact_a = tmp_path / "missing_golden.json"
    artifact_a.write_text(json.dumps(missing_golden))
    with pytest.raises(ValueError):
        row_from_artifact(artifact_a)

    missing_metrics = {k: v for k, v in base_payload.items() if k != "metrics"}
    artifact_b = tmp_path / "missing_metrics.json"
    artifact_b.write_text(json.dumps(missing_metrics))
    with pytest.raises(ValueError):
        row_from_artifact(artifact_b)


def test_from_artifact_missing_key_exits_1_config_error_no_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`evals.run --publish --from-artifact` on a malformed artifact (a pre-R47 shape with no
    `"golden"` key) exits `1` with one `error: config_error: ...` line and no traceback -- never
    a raw `KeyError` escaping `main` (fix-1, I1). `--golden`/`--prompt` are still required by
    argparse but never read on this path; nothing is appended to any results file."""
    from evals.run import main

    for name in ("LLM_API_KEY", "MODEL_PRICES_JSON", "CHEAP_MODEL", "STRONG_MODEL"):
        monkeypatch.delenv(name, raising=False)

    payload = {
        "prompt_version": "triage-v4",
        "model": "gpt-4o-mini",
        "started_at": "2026-09-20T12:34:56+00:00",
        "metrics": metrics_payload(_sample_metrics()),
        "cases": [],
        # deliberately no "golden" key -- a pre-R47 artifact shape.
    }
    artifact = tmp_path / "artifact.json"
    artifact.write_text(json.dumps(payload))
    results_path = tmp_path / "results.md"
    monkeypatch.setattr("evals.run.RESULTS_PATH", results_path)

    rc = main(
        [
            "--golden",
            str(tmp_path / "ignored.jsonl"),
            "--prompt",
            "ignored",
            "--publish",
            "--from-artifact",
            str(artifact),
        ]
    )

    captured = capsys.readouterr()
    assert rc == 1
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "Traceback" not in captured.err
    assert captured.out == ""
    assert not results_path.exists()  # nothing was ever appended
