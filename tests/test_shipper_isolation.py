"""Pins the shipper's isolation from this repo (m6 task-02): its package imports nothing under
`core`/`api`/`worker`/`evals`/`tests`, its vendored `sign_body` matches `core.signing.sign_body`
byte-for-byte, and its systemd unit + `pyproject.toml` carry the documented hardening/shape.

`core.signing` is imported here only for the vendored-equality comparison — the shipper's OWN
package under test never imports it (that is exactly what `test_shipper_imports_nothing_from_the_
repo` below pins with an `ast` walk, never a live import of the shipper's modules for this
purpose).

`sentinelbrief_shipper` is imported via `pythonpath = ["honeypot/shipper"]`
(`pyproject.toml`) — until the implementer adds the package, every test below fails at
collection with `ModuleNotFoundError: No module named 'sentinelbrief_shipper'` (and this file's
own `ast` walk below independently fails on the missing `honeypot/shipper/sentinelbrief_shipper/`
directory).
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
from sentinelbrief_shipper.signing import sign_body as shipper_sign_body

from core.signing import sign_body as core_sign_body

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE_DIR = _REPO_ROOT / "honeypot" / "shipper" / "sentinelbrief_shipper"
_UNIT_PATH = _REPO_ROOT / "honeypot" / "shipper" / "sentinelbrief-shipper.service"
_PYPROJECT_PATH = _REPO_ROOT / "honeypot" / "shipper" / "pyproject.toml"

_FORBIDDEN_ROOTS = {"core", "api", "worker", "evals", "tests"}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


def test_shipper_imports_nothing_from_the_repo() -> None:
    """Interfaces: the shipper's own package never imports `core`/`api`/`worker`/`evals`/`tests`
    — an `ast` walk (never a live import, so a forbidden import inside a function body still
    counts) over every `.py` file under `sentinelbrief_shipper/` — mutant: importing
    `core.signing` instead of vendoring it.
    """
    assert _PACKAGE_DIR.is_dir(), f"{_PACKAGE_DIR} does not exist yet"
    python_files = sorted(_PACKAGE_DIR.glob("*.py"))
    assert python_files, f"no .py files under {_PACKAGE_DIR}"

    for path in python_files:
        forbidden = _imported_roots(path) & _FORBIDDEN_ROOTS
        assert not forbidden, f"{path.name} imports forbidden module(s): {sorted(forbidden)}"


@pytest.mark.parametrize(
    ("secret", "body"),
    [
        ("empty-body-secret", b""),
        ("one-kib-secret", b"x" * 1024),
        ("sécret-ñ", "pâyloàd-ünicode".encode()),
    ],
    ids=["empty-body", "one-kib-body", "non-ascii-secret-and-body"],
)
def test_vendored_sign_body_matches_core(secret: str, body: bytes) -> None:
    """Interfaces `sentinelbrief_shipper.signing.sign_body`: byte-for-byte identical to
    `core.signing.sign_body` across an empty body, a 1 KiB body, and a non-ASCII secret+body —
    mutant: any divergence in the vendored HMAC algorithm or hex/prefix formatting.
    """
    assert shipper_sign_body(secret, body) == core_sign_body(secret, body)


def test_unit_file_hardening_and_pyproject_shape() -> None:
    """Interfaces `sentinelbrief-shipper.service`/`pyproject.toml`: the unit runs unprivileged
    under the documented hardening directives, and the package declares exactly the one
    dependency (`httpx`) at the documented version range — mutant: dropping a hardening line, or
    an extra/mismatched dependency.
    """
    unit_text = _UNIT_PATH.read_text()
    for expected_line in (
        "User=shipper",
        "EnvironmentFile=/etc/sentinelbrief-shipper.env",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "StateDirectory=sentinelbrief-shipper",
        "Restart=always",
    ):
        assert expected_line in unit_text

    pyproject = tomllib.loads(_PYPROJECT_PATH.read_text())
    assert pyproject["project"]["dependencies"] == ["httpx>=0.27,<1"]
    assert pyproject["project"]["requires-python"] == ">=3.12"
