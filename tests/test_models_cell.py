"""Pins `evals.publish.models_cell` (ruling R52, whole-branch fix wave finding I1).

`models_cell` is the single source of the `docs/results.md` `models` cell shape, shared by
`evals.run`'s `--publish` path and `evals.publish.row_from_artifact`, so a live-run row and a
`--from-artifact` row for the same config can never disagree on it.
"""

from __future__ import annotations

from evals.publish import models_cell


def test_models_cell_with_strong_model_uses_arrow() -> None:
    assert models_cell("gpt-4o-mini", "gpt-5.4") == "gpt-4o-mini→gpt-5.4"


def test_models_cell_without_strong_model_is_bare_model() -> None:
    assert models_cell("gpt-4o-mini", "") == "gpt-4o-mini"
