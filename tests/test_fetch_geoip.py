"""Pins `scripts/fetch_geoip.py`'s `main(argv, *, transport)` contract — m4 task-03. Deploy-time
helper (PRD §11): downloads the two GeoLite2 editions from MaxMind with `MAXMIND_LICENSE_KEY`
into a directory, refuses before any network access when the key is unset, extracts only the
`.mmdb` member by basename (never a member's own path — no path traversal), and never prints the
key or a URL that carries it.

`scripts/` has no `__init__.py`, so the module is loaded via
`importlib.util.spec_from_file_location`, mirroring `tests/test_post_alert.py`. `main`'s
`transport` parameter is the seam these tests inject an `httpx.MockTransport` through — the real
MaxMind endpoint is never touched (CONVENTIONS.md §10, `@pytest.mark.live` is reserved for
`tests/test_geo_live.py`). Every response tarball is built in memory with `tarfile` + `io.BytesIO`
— no `.mmdb` byte ever touches disk in the repo, only under `tmp_path` (brief Goal / rules).

The literal license key used throughout is the synthetic `"test-key"` — a real key never appears
anywhere in this file, a fixture, or test output.

Fix round 1 (review Findings M2/M3/M5) adds three RED tests against HEAD's `dd9b2d7` behavior:
`test_non_regular_member_is_bad_archive` (a directory-typed archive member trips a bare `assert`
into an uncaught `AssertionError`), `test_symlink_member_is_bad_archive_and_writes_nothing` (a
dangling-symlink member is mislabeled `no .mmdb in archive` via a `LookupError`/`KeyError`
coincidence), and `test_main_restores_httpx_logger_levels` (the `httpx`/`httpcore` logger-level
mutation `main()` makes to close the MUT-2 key leak is never restored, leaking across the pytest
session).
"""

from __future__ import annotations

import importlib.util
import io
import logging
import subprocess
import tarfile
from pathlib import Path
from types import ModuleType

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = REPO_ROOT / "scripts" / "fetch_geoip.py"


def _load_fetch_geoip() -> ModuleType:
    """Load `scripts/fetch_geoip.py` as a standalone module (no package `__init__.py` exists)."""
    spec = importlib.util.spec_from_file_location("fetch_geoip", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_geoip = _load_fetch_geoip()


def _make_tar_gz(members: dict[str, bytes]) -> bytes:
    """Build a `.tar.gz` in memory with one member per `(name, content)` pair — no `.mmdb` file
    is ever written under the repo (only `tmp_path`, via `main`'s own extraction).
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _forbidden_transport() -> httpx.MockTransport:
    """A transport that fails the test if the script ever attempts a network call."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"network should not be reached: {request.url}")

    return httpx.MockTransport(handler)


def test_refuses_without_license_key_before_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.delenv("MAXMIND_LICENSE_KEY", raising=False)

    exit_code = fetch_geoip.main(["--out-dir", str(tmp_path)], transport=_forbidden_transport())

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "error: config_error: MAXMIND_LICENSE_KEY is not set"


def test_downloads_both_editions_into_out_dir(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "test-key")
    dummy_bytes = {"GeoLite2-Country": b"country-dummy-bytes", "GeoLite2-ASN": b"asn-dummy-bytes"}

    def handler(request: httpx.Request) -> httpx.Response:
        edition = request.url.params["edition_id"]
        body = _make_tar_gz(
            {
                f"{edition}_20260901/{edition}.mmdb": dummy_bytes[edition],
                f"{edition}_20260901/COPYRIGHT.txt": b"copyright notice",
            }
        )
        return httpx.Response(200, content=body)

    exit_code = fetch_geoip.main(
        ["--out-dir", str(tmp_path)], transport=httpx.MockTransport(handler)
    )

    assert exit_code == 0
    for edition, content in dummy_bytes.items():
        path = tmp_path / f"{edition}.mmdb"
        assert path.read_bytes() == content

    assert not any(tmp_path.rglob("COPYRIGHT.txt"))

    out_lines = capsys.readouterr().out.strip().splitlines()
    assert set(out_lines) == {
        f"{edition}: {len(content)} bytes -> {tmp_path / f'{edition}.mmdb'}"
        for edition, content in dummy_bytes.items()
    }


def test_license_key_is_sent_in_the_query_and_never_printed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "test-key")
    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        edition = request.url.params["edition_id"]
        seen_params.append(
            {
                "edition_id": edition,
                "license_key": request.url.params["license_key"],
                "suffix": request.url.params["suffix"],
            }
        )
        body = _make_tar_gz({f"{edition}_20260901/{edition}.mmdb": b"x" * 16})
        return httpx.Response(200, content=body)

    with caplog.at_level(logging.DEBUG):
        exit_code = fetch_geoip.main(
            ["--out-dir", str(tmp_path)], transport=httpx.MockTransport(handler)
        )

    assert exit_code == 0
    assert {p["edition_id"] for p in seen_params} == {"GeoLite2-Country", "GeoLite2-ASN"}
    assert all(p["license_key"] == "test-key" for p in seen_params)
    assert all(p["suffix"] == "tar.gz" for p in seen_params)

    captured = capsys.readouterr()
    assert "test-key" not in captured.out
    assert "test-key" not in captured.err
    assert "test-key" not in caplog.text


def test_http_error_status_exits_1_without_the_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    exit_code = fetch_geoip.main(
        ["--out-dir", str(tmp_path), "--edition", "GeoLite2-Country"],
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "error: download_failed: GeoLite2-Country: HTTP 403"
    assert "test-key" not in captured.out
    assert "test-key" not in captured.err


def test_transport_exception_prints_only_the_class_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}")

    exit_code = fetch_geoip.main(
        ["--out-dir", str(tmp_path), "--edition", "GeoLite2-Country"],
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "error: download_failed: GeoLite2-Country: ConnectError"
    assert "test-key" not in captured.out
    assert "test-key" not in captured.err


def test_archive_without_mmdb_or_with_traversal_name_is_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Neither a `README.txt`-only archive nor one whose only member is named `../../evil.mmdb`
    contains anything matching `*/{edition}.mmdb` (or exactly `{edition}.mmdb`) — the documented
    matching rule (brief Interfaces, controller ruling R8) — so both are rejected as `no .mmdb in
    archive`, and neither ever writes a file, in or outside `out_dir`: the member path is never
    honored. The companion case where a traversal-named member DOES match the edition rule (and
    must be written by basename) is
    `test_traversal_member_matching_the_edition_is_written_by_basename`.
    """
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "test-key")

    def readme_only_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_make_tar_gz({"README.txt": b"no mmdb here"}))

    out_dir_1 = tmp_path / "out1"
    exit_code_1 = fetch_geoip.main(
        ["--out-dir", str(out_dir_1), "--edition", "GeoLite2-Country"],
        transport=httpx.MockTransport(readme_only_handler),
    )

    assert exit_code_1 == 1
    assert (
        capsys.readouterr().err.strip()
        == "error: download_failed: GeoLite2-Country: no .mmdb in archive"
    )

    def traversal_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_make_tar_gz({"../../evil.mmdb": b"x" * 16}))

    out_dir_2 = tmp_path / "out2"
    exit_code_2 = fetch_geoip.main(
        ["--out-dir", str(out_dir_2), "--edition", "GeoLite2-Country"],
        transport=httpx.MockTransport(traversal_handler),
    )

    assert exit_code_2 == 1
    assert (
        capsys.readouterr().err.strip()
        == "error: download_failed: GeoLite2-Country: no .mmdb in archive"
    )
    assert not any(tmp_path.rglob("*.mmdb")), "a member path was honored outside out_dir"


def test_traversal_member_matching_the_edition_is_written_by_basename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Controller ruling R8 / brief Interfaces (amended row): a member named
    `../../GeoLite2-Country.mmdb` DOES match the documented rule (its name ends with
    `/GeoLite2-Country.mmdb`) despite its leading `..` components, so it must be extracted — but
    stripped of its directory, never honoring the member's own path (which would otherwise
    traverse above `out_dir`). `out_dir` is nested two levels under `tmp_path`
    (`tmp_path/geo/out`) so the literal traversal target `../../GeoLite2-Country.mmdb` resolves
    to a path still inside pytest's own tmp tree even if a broken implementation honored it —
    this test asserts that exact path does not exist, not merely "somewhere outside `out_dir`".
    """
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "test-key")
    out_dir = tmp_path / "geo" / "out"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_make_tar_gz({"../../GeoLite2-Country.mmdb": b"x" * 16}))

    exit_code = fetch_geoip.main(
        ["--out-dir", str(out_dir), "--edition", "GeoLite2-Country"],
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == 0
    written = out_dir / "GeoLite2-Country.mmdb"
    assert written.read_bytes() == b"x" * 16

    traversal_target = (out_dir / "../../GeoLite2-Country.mmdb").resolve()
    assert not traversal_target.exists()


def test_edition_flag_limits_downloads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "test-key")
    requested_editions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        edition = request.url.params["edition_id"]
        requested_editions.append(edition)
        body = _make_tar_gz({f"{edition}_20260901/{edition}.mmdb": b"x" * 16})
        return httpx.Response(200, content=body)

    exit_code = fetch_geoip.main(
        ["--out-dir", str(tmp_path), "--edition", "GeoLite2-ASN"],
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == 0
    assert requested_editions == ["GeoLite2-ASN"]
    assert (tmp_path / "GeoLite2-ASN.mmdb").exists()
    assert not (tmp_path / "GeoLite2-Country.mmdb").exists()

    with pytest.raises(SystemExit) as exc_info:
        fetch_geoip.main(
            ["--out-dir", str(tmp_path), "--edition", "Bogus"],
            transport=_forbidden_transport(),
        )
    assert exc_info.value.code == 2


def test_non_regular_member_is_bad_archive(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Review Finding M2: a member matching the edition rule whose type is not a regular file
    (here a `tarfile.DIRTYPE` directory entry) makes `tar.extractfile()` return `None`; at HEAD an
    unguarded `assert extracted is not None` turns that into an uncaught `AssertionError`
    escaping `main()` — this test pins the documented contract instead (every failure is an
    `error: ...` line and exit 1, never a raw traceback) and fails at HEAD with that
    `AssertionError` propagating out of the `main()` call below rather than a clean exit code.
    """
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "test-key")
    out_dir = tmp_path / "out"

    def handler(request: httpx.Request) -> httpx.Response:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            info = tarfile.TarInfo(name="GeoLite2-Country_20260901/GeoLite2-Country.mmdb")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        return httpx.Response(200, content=buffer.getvalue())

    exit_code = fetch_geoip.main(
        ["--out-dir", str(out_dir), "--edition", "GeoLite2-Country"],
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    stderr_lines = captured.err.strip().splitlines()
    assert stderr_lines == ["error: download_failed: GeoLite2-Country: bad archive"]
    assert "Traceback" not in captured.err
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_symlink_member_is_bad_archive_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Review Finding M3: a member matching the edition rule whose type is a dangling symlink
    (`tarfile.SYMTYPE`, target not present in the archive) makes `tar.extractfile()` raise a
    `KeyError` — itself a `LookupError` subclass — which at HEAD is caught by the same
    `except LookupError` used for "no matching member at all", mislabeling a real (if unusable)
    `.mmdb`-named member as `no .mmdb in archive`. This test pins the distinct, honest reason
    `bad archive` and fails at HEAD because the actual message is `no .mmdb in archive`.
    """
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "test-key")
    out_dir = tmp_path / "out"

    def handler(request: httpx.Request) -> httpx.Response:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            info = tarfile.TarInfo(name="GeoLite2-Country_20260901/GeoLite2-Country.mmdb")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../../../etc/passwd"
            tar.addfile(info)
        return httpx.Response(200, content=buffer.getvalue())

    exit_code = fetch_geoip.main(
        ["--out-dir", str(out_dir), "--edition", "GeoLite2-Country"],
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == 1
    assert (
        capsys.readouterr().err.strip() == "error: download_failed: GeoLite2-Country: bad archive"
    )
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_main_restores_httpx_logger_levels(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Review Finding M5: `main()` sets both the `httpx` and `httpcore` loggers to `WARNING` as a
    process-global side effect (closing the real key-leak MUT-2 proves — see
    `test_license_key_is_sent_in_the_query_and_never_printed`) but never restores the prior level,
    so calling `main()` in-process (as every test in this file does) leaks the mutation across the
    whole pytest session.

    m4 fix-wave (review finding task-03 N1): the baseline before each call is `DEBUG`, not
    `NOTSET`. A `NOTSET` baseline cannot distinguish "the previous level was correctly captured
    and restored" from "the `finally` unconditionally hardcodes `logging.NOTSET` instead of
    restoring whatever `previous_httpx_level`/`previous_httpcore_level` actually held" — the exact
    mutant this test exists to kill, and it would pass this assertion vacuously under the old
    `NOTSET` baseline. A `finally` restores the `NOTSET` baseline again after the test regardless
    of outcome, so this test does not itself leak into later ones.
    """
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "test-key")
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")

    def ok_handler(request: httpx.Request) -> httpx.Response:
        edition = request.url.params["edition_id"]
        body = _make_tar_gz({f"{edition}_20260901/{edition}.mmdb": b"x" * 16})
        return httpx.Response(200, content=body)

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    try:
        httpx_logger.setLevel(logging.DEBUG)
        httpcore_logger.setLevel(logging.DEBUG)

        exit_code = fetch_geoip.main(
            ["--out-dir", str(tmp_path)], transport=httpx.MockTransport(ok_handler)
        )

        assert exit_code == 0
        assert httpx_logger.level == logging.DEBUG
        assert httpcore_logger.level == logging.DEBUG

        httpx_logger.setLevel(logging.DEBUG)
        httpcore_logger.setLevel(logging.DEBUG)

        exit_code_err = fetch_geoip.main(
            ["--out-dir", str(tmp_path), "--edition", "GeoLite2-Country"],
            transport=httpx.MockTransport(error_handler),
        )

        assert exit_code_err == 1
        assert httpx_logger.level == logging.DEBUG
        assert httpcore_logger.level == logging.DEBUG
    finally:
        httpx_logger.setLevel(logging.NOTSET)
        httpcore_logger.setLevel(logging.NOTSET)


def test_mmdb_is_gitignored_and_dockerignored() -> None:
    gitignore_lines = [line.strip() for line in (REPO_ROOT / ".gitignore").read_text().splitlines()]
    dockerignore_lines = [
        line.strip() for line in (REPO_ROOT / ".dockerignore").read_text().splitlines()
    ]

    assert "*.mmdb" in gitignore_lines
    assert "*.mmdb" in dockerignore_lines

    result = subprocess.run(
        ["git", "ls-files", "*.mmdb"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""
