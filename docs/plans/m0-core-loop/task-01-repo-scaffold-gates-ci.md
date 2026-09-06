---
id: task-01
milestone: m0-core-loop
depends_on: []
status: planned
spec: PRD.md §4 (layout, stack), §12 M0; CONVENTIONS.md §2, §9, §10
---

# task-01 — Scaffold the uv project and stand up the tooling gates and CI

## Goal

The PRD §4 Python layout exists as one `uv` project at the repo root, the five gate commands run
clean on real-but-empty packages, the two gates-as-tests exist, import-linter contracts are live
(and proven to fail when violated), and GitHub Actions runs the same gates on every push and PR.
Everything in M0–M8 builds inside this skeleton.

## Context (read ONLY these)

- `PRD.md` §4 — the layout tree (normative) and stack.
- `CONVENTIONS.md` §2 (layering + the five contracts), §9 (tool config values, gate commands),
  §10 (test rules, gates-as-tests, `live` marker).
- `.env.example` — already complete; do not edit in this task.
- AdvisorDesk's `.github/workflows/ci.yml` and `lefthook.yml` as shape templates (read-only).

## Files

- Create: `pyproject.toml`, `uv.lock` (generated), `api/__init__.py`, `worker/__init__.py`,
  `worker/prompts/.gitkeep`, `core/__init__.py`, `core/models/__init__.py`,
  `core/schemas/__init__.py`, `core/services/__init__.py`, `evals/__init__.py`,
  `evals/golden/.gitkeep`, `fixtures/alerts/.gitkeep`, `scripts/.gitkeep`
- Create: `tests/test_lint_clean.py`, `tests/test_import_contracts.py`
- Create: `.github/workflows/ci.yml`, `.github/dependabot.yml`, `lefthook.yml`, `package.json`
  (root: `packageManager`, `prepare`, `gates:api`), `pnpm-workspace.yaml` (`packages: ["web"]`;
  `web/` arrives at M3)
- Modify: `README.md` — replace the "Gates" placeholder text with the real commands if they differ

## Interfaces

- **Consumes:** nothing (first task).
- **Produces (later tasks rely on — produce exactly):**
  - `pyproject.toml`: `[project] name = "sentinelbrief"`, `requires-python = ">=3.12"`,
    `dependencies = ["pydantic>=2", "pydantic-settings", "openai>=2", "httpx"]`;
    `[dependency-groups] dev = ["pytest", "pytest-asyncio", "pytest-cov", "ruff", "mypy",
    "import-linter", "types-pyyaml"]`; `[build-system]` hatchling with
    `[tool.hatch.build.targets.wheel] packages = ["api", "worker", "core", "evals"]`.
  - `[tool.ruff]` `line-length = 100`; `[tool.ruff.lint]` `select = ["E","F","I","UP","B"]`;
    `[tool.ruff.lint.flake8-bugbear]` `extend-immutable-calls` FastAPI block verbatim from
    CONVENTIONS.md §9.
  - `[tool.mypy]` `strict = true`, `files = ["api", "worker", "core", "evals"]`.
  - `[tool.pytest.ini_options]` `testpaths = ["tests"]`, `asyncio_mode = "auto"`,
    `asyncio_default_fixture_loop_scope = "function"`,
    `markers = ["live: calls a real external API; excluded from CI and the default run"]`,
    `addopts = "-m 'not live'"`.
  - `[tool.importlinter]` `root_packages = ["api", "worker", "core", "evals"]` and five
    `forbidden` contracts named exactly: `core.models is a pure leaf`;
    `core never imports api, worker or evals`;
    `api never imports worker or core.llm` (`source_modules = ["api"]`,
    `forbidden_modules = ["worker", "core.llm"]`, `allow_indirect_imports = true`);
    `worker and evals never import api`; `nothing imports api.main or worker.main`.
  - Gate commands exactly as CONVENTIONS.md §9 (all later Verify blocks reuse them).
  - `tests/test_lint_clean.py::test_ruff_check_clean`, `::test_ruff_format_clean`,
    `::test_mypy_clean` (each a subprocess of the gate command with **no path args**, asserting
    `returncode == 0` with stdout+stderr in the failure message);
    `tests/test_import_contracts.py::test_lint_imports_clean`.
  - CI workflow `.github/workflows/ci.yml`: job `python` on `push` to `main` and `feat/**` and
    `pull_request` to `main`; `astral-sh/setup-uv` pinned to an exact version with
    `python-version: "3.12"`; env `UV_LOCKED: "1"`; one named step per gate; then
    `uv run pytest -q --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90`;
    then `pip-audit` (`continue-on-error: true`, `shell: bash`,
    `uv export --format requirements-txt --no-emit-project | uvx pip-audit -r /dev/stdin`).
  - `package.json` script `gates:api` = the five gate commands joined with `&&`.

## Steps (TDD)

- [ ] **Step 1: Write the two failing gate-tests first.** `tests/test_lint_clean.py`:

  ```python
  from __future__ import annotations
  import subprocess, sys
  import pytest

  GATES = {
      "ruff_check": ["uv", "run", "ruff", "check", "."],
      "ruff_format": ["uv", "run", "ruff", "format", "--check", "."],
      "mypy": ["uv", "run", "mypy"],
  }

  @pytest.mark.parametrize("name", list(GATES))
  def test_gate_clean(name: str) -> None:
      proc = subprocess.run(GATES[name], capture_output=True, text=True)
      assert proc.returncode == 0, f"{name} failed:\n{proc.stdout}\n{proc.stderr}"
  ```

  Write three explicit functions (`test_ruff_check_clean`, `test_ruff_format_clean`,
  `test_mypy_clean`) rather than one parametrized test, so each gate has a stable test id.
  `tests/test_import_contracts.py::test_lint_imports_clean` runs `uv run lint-imports` the same
  way.
- [ ] **Step 2: Run them to see them fail** (no pyproject yet):
  `uv run pytest -q` → Expected: uv cannot resolve a project — command fails.
- [ ] **Step 3: Create `pyproject.toml`** with every value from Interfaces; `uv lock`; `uv sync`.
- [ ] **Step 4: Create the package skeleton** (`__init__.py` files each with
  `from __future__ import annotations` and a one-line docstring naming the layer per
  CONVENTIONS.md §2; the `.gitkeep`s).
- [ ] **Step 5: Run the gates to see them pass:** `uv run pytest -q` → Expected: `4 passed`
  (0 skipped). Then the other four gate commands → Expected: all exit 0 (`lint-imports` prints
  `Contracts: 5 kept, 0 broken.`).
- [ ] **Step 6: Contract-verification ritual (CONVENTIONS.md §2):** add `from api import x`
  inside `core/models/__init__.py` → `uv run lint-imports` exits 1 and `test_lint_imports_clean`
  fails → revert → both green again. Then add `import worker` inside `api/__init__.py` → exits 1
  → revert. Record both runs in the report.
- [ ] **Step 7: CI, hooks, dependabot:** `.github/workflows/ci.yml` per Interfaces;
  `.github/dependabot.yml` (`uv` ecosystem at `/`, weekly; `npm` at `/` added when `web/` lands);
  `lefthook.yml` pre-commit running `uv run ruff check --fix {staged_files} && uv run ruff format
  {staged_files}` on `*.py` with `stage_fixed: true`; root `package.json` + `pnpm-workspace.yaml`.
- [ ] **Step 8: Gates → commit:**
  `chore(core): uv project scaffold, tooling gates, import contracts, CI (m0 task-01)` with both
  trailers; path-scoped `git add` of exactly the files above.

## Verify

```bash
uv run ruff check . && uv run ruff format --check .   # exit 0, "All checks passed!"
uv run mypy                                            # Success: no issues found in 8 source files
uv run lint-imports                                    # Contracts: 5 kept, 0 broken.
uv run pytest -q                                       # 4 passed
git status --short                                     # no .env, no stray files
```

## Acceptance

- The §4 Python tree exists exactly (`web/`, `honeypot/`, `infra/`, `alembic/` arrive later).
- All five gates green; the two gates-as-tests fail when lint/typing/contracts break (proven
  twice via Step 6).
- CI workflow exists with one step per gate and the coverage floor; it turns green on the first
  push of `feat/m0-core-loop`.
