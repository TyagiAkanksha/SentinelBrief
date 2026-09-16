"""Pins `honeypot/shipper/sentinelbrief-shipper.service`'s sandboxing directives BY VALUE, over
the file's ACTIVE `KEY=VALUE` lines only (m7 task-08 fix-1, review I1).

`tests/test_shipper_v02.py::test_unit_file_has_sandboxing_directives` (pinned) and
`tests/test_shipper_isolation.py::test_unit_file_hardening_and_pyproject_shape` (pinned) both
assert `expected_line in unit_text` — a SUBSTRING check. Commenting a directive out with a leading
`#` (systemd's own comment syntax; the unit already uses one at `:18`) keeps the exact substring
present while systemd ignores the line entirely — the review's mutants M8, M8c, M8e all
**survived** that pattern. This file parses only the lines systemd would actually apply (skipping
`#`/`;`-prefixed comments) into a `key -> value` mapping and asserts each directive's VALUE, so a
commented-out directive is indistinguishable from a missing one.

New, unpinned file (the two files above stay pinned/untouched — the fix shape a pinned-file edit
cannot supply this cheaply is a fresh guard file, `tests.md`: "Adding new test files is always
allowed").
"""

from __future__ import annotations

from pathlib import Path

_UNIT_PATH = (
    Path(__file__).resolve().parent.parent
    / "honeypot"
    / "shipper"
    / "sentinelbrief-shipper.service"
)


def _active_directives(unit_text: str) -> dict[str, str]:
    """Every `KEY=VALUE` line systemd would actually apply — comment lines (`#`/`;`, systemd's
    own two comment prefixes) and section headers (`[Service]`, no `=`) are excluded, so a
    directive hidden behind a leading `#` is simply ABSENT from the result, not present-but-
    disabled. A line with nothing after `=` (`CapabilityBoundingSet=`) maps to `""`, not dropped.
    """
    active: dict[str, str] = {}
    for raw_line in unit_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        active[key] = value
    return active


def test_active_directives_parses_commented_lines_as_absent() -> None:
    """Self-check on the helper above, independent of the real unit file: a `#`-prefixed line
    must never appear in the parsed mapping, and an active line with the same key must win.
    Pins the parsing logic itself against the exact class of mutant (I1) this whole file exists
    to catch.
    """
    synthetic = "\n".join(
        [
            "[Service]",
            "# ProtectSystem=strict",
            "; NoNewPrivileges=true",
            "PrivateTmp=true",
            "CapabilityBoundingSet=",
        ]
    )
    active = _active_directives(synthetic)

    assert "ProtectSystem" not in active
    assert "NoNewPrivileges" not in active
    assert active["PrivateTmp"] == "true"
    assert active["CapabilityBoundingSet"] == ""


def test_sandboxing_directives_are_active_with_the_documented_values() -> None:
    """Review I1 fix shape, ruling R20: every directive below must be an ACTIVE line (never a
    comment) carrying exactly the value shown. `ProtectSystem`/`PrivateTmp`/`NoNewPrivileges`/
    `CapabilityBoundingSet` already shipped at GREEN; R20 adds the five further free directives
    plus `TimeoutStopSec=30` (I3's fix, paired with an interruptible backoff wait) to this same
    expected list. Mutant: prefixing any one of these lines with `#` — each must independently
    make this test fail (the M8/M8c/M8e class the substring-only guards missed).
    """
    active = _active_directives(_UNIT_PATH.read_text())

    expected = {
        "ProtectSystem": "strict",
        "NoNewPrivileges": "true",
        "CapabilityBoundingSet": "",
        "ProtectKernelTunables": "true",
        "ProtectKernelLogs": "true",
        "ProtectControlGroups": "true",
        "RestrictNamespaces": "true",
        "LockPersonality": "true",
        "SystemCallArchitectures": "native",
        "TimeoutStopSec": "30",
    }
    for key, value in expected.items():
        assert key in active, f"{_UNIT_PATH} has no ACTIVE {key}= line (commented out or absent)"
        assert active[key] == value, f"{_UNIT_PATH}'s {key}= is {active[key]!r}, expected {value!r}"

    # PrivateTmp accepts systemd's boolean spelling either way.
    assert "PrivateTmp" in active, f"{_UNIT_PATH} has no ACTIVE PrivateTmp= line"
    assert active["PrivateTmp"] in ("true", "yes"), (
        f"{_UNIT_PATH}'s PrivateTmp= is {active['PrivateTmp']!r}, expected 'true' or 'yes'"
    )


def test_restrict_address_families_is_deliberately_absent() -> None:
    """R20: `RestrictAddressFamilies` is deferred, not forgotten — glibc's `getaddrinfo` opens an
    `AF_NETLINK` socket to enumerate local addresses, and restricting to `AF_INET`/`AF_INET6`
    alone risks a silent DNS regression with no local signal on a host whose whole job is one
    outbound HTTPS POST (review verdict on the deferral). Absence, not a specific value, is the
    pin; a comment line explaining the deferral is fine and expected.
    """
    active = _active_directives(_UNIT_PATH.read_text())

    assert "RestrictAddressFamilies" not in active, (
        f"{_UNIT_PATH} sets RestrictAddressFamilies -- if this is now safe to add, update this "
        "test (and see the R20 deferral note for the correct value, "
        "AF_INET AF_INET6 AF_NETLINK AF_UNIX, not a narrower set)"
    )
