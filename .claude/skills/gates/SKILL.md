---
name: gates
description: Run every SentinelBrief quality gate (ruff, ruff format, mypy, import-linter, pytest; plus the web gates when web/ exists) with the test database env exported, and paste the real output. Use before every commit, before claiming any task complete, and whenever a report needs gate evidence.
allowed-tools: Bash, Read
---

# Gates

The gate set is the definition of "clean" for this repo (CONVENTIONS.md §9, FRONTEND-CONVENTIONS.md
§1). Run it from the repo root, paste the output verbatim, and never summarize a red result as
"mostly passing".

## 1. Export the test database URL first

```sh
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelbrief_test
```

Use the value from `.env` if it differs. **pytest output produced without this export is not
gate evidence** — DB-fixture tests skip silently and a green run proves nothing. Show the export
line in whatever you paste.

## 2. Python gates (in this order; stop at the first red)

```sh
uv run ruff check --no-cache .  # expected: "All checks passed!"
uv run ruff format --check .    # expected: "N files already formatted"
uv run mypy --no-incremental    # expected: "Success: no issues found in N source files"
uv run lint-imports             # expected: "Contracts: N kept, 0 broken."
uv run pytest -q                # expected: "N passed, S skipped" — S must be 0 when the export line is set
```

A non-zero skip count with the export active means a fixture is misnamed or the DB is
unreachable — that is a red gate, not a pass.

## 3. Web gates (from M3, when `web/` exists)

```sh
pnpm -C web lint
pnpm -C web type-check
pnpm -C web format:check
pnpm -C web test
```

## 4. Baseline drift (from M2 for OpenAPI, M3 for codegen)

```sh
uv run python scripts/export_openapi.py && git diff --exit-code -- api/openapi.json
pnpm -C web codegen && git diff --exit-code -- web/src/types/generated   # from M3
```

## On a red gate

Stop. Paste the failing output. Do not paper over it (no `# noqa`, no `# type: ignore`, no
skipping a test, no relaxing a contract) — fix the cause, or report it if the fix is outside the
current task's scope.

## Cold-cache variant (reviewers, milestone gates)

The ruff check in §2 now always runs cold (`--no-cache`), so reviewers need no special variant
for ruff. Lint caches can still mask latent violations elsewhere; for a fully cold mypy run, use
`uv run mypy --no-incremental`, or run the whole set in a fresh worktree.
