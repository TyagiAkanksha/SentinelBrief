"""Source-level pin: `evals/run.py` builds one tool registry per run, above the `--prompt` loop
(N-M5) — m5 task-01 fix-1, review finding M3 (a surviving mutant: moving the `build_registry(`
call inside the loop changed no test's observable behavior, because every existing test only
exercises `--prompt` once or with prompt versions cheap enough that a second registry build is
unobservable from the outside).
"""

from __future__ import annotations

from pathlib import Path


def test_evals_run_builds_the_registry_once_above_the_prompt_loop() -> None:
    """N-M5: one registry per run. A source-level pin, because `main` exposes no seam a
    black-box test could count registry constructions through."""
    source = (Path(__file__).resolve().parents[1] / "evals" / "run.py").read_text()
    assert source.count("build_registry(") == 1
    assert source.index("build_registry(") < source.index("for prompt_version in args.prompt:")
