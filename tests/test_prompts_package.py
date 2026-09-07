"""Pins the `worker.prompts` package layout (m1 task-00; M0 final-review plan defect 4).

M0 shipped the loader as `worker/prompts.py` sitting beside the `worker/prompts/` markdown
directory; CPython resolves `import worker.prompts` to the module, never the namespace directory,
so it worked only by that quirk. `evals.run` (task-03) and the M2 Dockerfile depend on
`worker.prompts` being a real package before this stops being cheap to fix. This test pins the
post-move shape: `worker/prompts/__init__.py` is the loader, `PROMPTS_DIR` still resolves to
`worker/prompts/` and still contains the shipped `triage-v1.md`.
"""

from __future__ import annotations

from pathlib import Path

import worker.prompts as prompts
from worker.prompts import PROMPTS_DIR


def test_worker_prompts_is_a_regular_package() -> None:
    """`worker.prompts` must resolve to `worker/prompts/__init__.py`, not a sibling module file.

    Today `worker/prompts.py` exists next to the `worker/prompts/` directory, so this fails until
    the implementer `git mv`s the loader into the package (Step 2 of the task brief).
    """
    assert Path(prompts.__file__).name == "__init__.py"


def test_prompts_dir_contains_v1() -> None:
    """`PROMPTS_DIR` must still point at the directory holding `triage-v1.md`.

    This already passes today (`PROMPTS_DIR = Path(__file__).parent / "prompts"` in the current
    `worker/prompts.py`) and must keep passing unchanged after the move (`PROMPTS_DIR =
    Path(__file__).parent` once the loader itself lives inside `worker/prompts/`) — it is the
    regression guard that the markdown files and the v1 hash pin (`tests/test_prompt_pins.py`)
    never move.
    """
    assert (PROMPTS_DIR / "triage-v1.md").is_file()
