"""Pins the m6 task-05 deploy walkthrough artifacts against the brief's Interfaces -> test table
(`docs/plans/m6-real-data-deploy/task-05-deploy-walkthrough.md` lines 207-220): the owner-run
walkthrough (`infra/deploy/ec2-single-host.md`), the `VERIFY.md` template, the three IAM JSON
documents (`infra/deploy/iam/{app-host-trust,app-host-inline,honeypot-host-trust}.json`), the two
user-data scripts (`infra/deploy/user-data-app.sh`, `honeypot/user-data.sh`), the finalized
`docs/deployment.md`, and `honeypot/README.md`'s cross-link to the extracted user-data file.

Pure text/JSON/`bash -n` pins, no docker, no live AWS, no SSM. `bash -n` (syntax-only parse, no
execution) is the same pattern `tests/test_deploy_scripts.py::_bash_dash_n` and
`tests/test_backup_artifacts.py::_bash_dash_n` use for the prior tasks' scripts -- copied here, not
imported, per CONVENTIONS.md §10 (unique basenames across `tests/`, no `__init__.py`, so
cross-test-file imports are not used).

At BASE none of `infra/deploy/ec2-single-host.md`, `infra/deploy/VERIFY.md`,
`infra/deploy/iam/*.json`, `infra/deploy/user-data-app.sh`, or `honeypot/user-data.sh` exist yet,
and `docs/deployment.md` / `honeypot/README.md` do not yet carry the text this module pins, so
every test below fails on a missing file or missing text -- except
`test_every_relative_link_in_deploy_docs_resolves`, which is a regression guard: at BASE none of
the scanned markdown files contain a single markdown link target (verified with a `grep` for the
literal two-character sequence `](` followed eventually by `)` across `infra/deploy`, `honeypot`,
and `docs/deployment.md` -> no hits), so the test passes vacuously until the walkthrough/VERIFY
docs add cross-links.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

_EC2_WALKTHROUGH = _REPO_ROOT / "infra" / "deploy" / "ec2-single-host.md"
_VERIFY_MD = _REPO_ROOT / "infra" / "deploy" / "VERIFY.md"
_IAM_DIR = _REPO_ROOT / "infra" / "deploy" / "iam"
_APP_TRUST = _IAM_DIR / "app-host-trust.json"
_APP_INLINE = _IAM_DIR / "app-host-inline.json"
_HONEYPOT_TRUST = _IAM_DIR / "honeypot-host-trust.json"
_USER_DATA_APP = _REPO_ROOT / "infra" / "deploy" / "user-data-app.sh"
_HONEYPOT_USER_DATA = _REPO_ROOT / "honeypot" / "user-data.sh"
_DEPLOYMENT_DOC = _REPO_ROOT / "docs" / "deployment.md"
_HONEYPOT_README = _REPO_ROOT / "honeypot" / "README.md"

_REQUIRED_NEW_ARTIFACTS = [
    _EC2_WALKTHROUGH,
    _VERIFY_MD,
    _APP_TRUST,
    _APP_INLINE,
    _HONEYPOT_TRUST,
    _USER_DATA_APP,
    _HONEYPOT_USER_DATA,
]

_MD_LINK_RE = re.compile(r"\]\(([^)]+)\)")

_SECRET_PATTERNS = {
    "openai-key-shaped": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "postgres-dsn-with-real-password": re.compile(r"postgresql://[^<\s]+:[^<\s'\"]+@"),
    "aws-access-key-id-shaped": re.compile(r"AKIA[0-9A-Z]{16}"),
    # m7 task-08 (M6 final review M6, pinned-edit approved): a `sha256:<64 hex>` image digest
    # (e.g. `honeypot/docker-compose.yml`'s `image: cowrie/cowrie@sha256:...` line) is a public,
    # documented artifact reference, not a secret -- exempted so `infra/deploy/VERIFY.md` can
    # record the digest in full instead of the truncated `sha256:42e01e0e...740d44` form the
    # review flagged. A bare 40+-hex run with no `sha256:` prefix elsewhere still trips it.
    "40-plus-char-hex-run": re.compile(r"(?<!sha256:)\b[0-9a-f]{40,}\b"),
}

# Steps 0-12 of the walkthrough: a numeric prefix present either as a markdown heading
# (`## 4. App host`) or as a bold-numbered list line (`4. **App host**` -- the shape the brief's
# own Produces block uses verbatim, e.g. task-05 lines 76, 85, 94, ...).
_WALKTHROUGH_STEP_NUMBERS = list(range(13))


def _bash_dash_n(path: Path) -> subprocess.CompletedProcess[str]:
    """Syntax-checks a shell script without executing it (copied from
    `tests/test_deploy_scripts.py::_bash_dash_n` / `tests/test_backup_artifacts.py::_bash_dash_n`,
    not imported -- task-05 brief's Context note on `tests/test_deploy_scripts.py`)."""
    return subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=10)


def _require(path: Path) -> str:
    assert path.exists(), (
        f"{path} does not exist yet -- task-05's GREEN step creates it per the brief's Interfaces "
        "block."
    )
    return path.read_text()


def _iter_scanned_markdown_files() -> list[Path]:
    """`infra/deploy/**/*.md` (recursive) + `honeypot/**/*.md` (recursive) + `docs/deployment.md`
    -- the link-resolution test's file set (Interfaces -> test table row "links resolve")."""
    files: list[Path] = []
    files.extend(sorted((_REPO_ROOT / "infra" / "deploy").rglob("*.md")))
    files.extend(sorted((_REPO_ROOT / "honeypot").rglob("*.md")))
    files.append(_DEPLOYMENT_DOC)
    return files


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else [value]


def _principal_services(statement: dict[str, object]) -> set[str]:
    principal = statement.get("Principal")
    if not isinstance(principal, dict):
        return set()
    service = principal.get("Service")
    if service is None:
        return set()
    return set(_as_list(service))  # type: ignore[arg-type]


def _step_heading_match(text: str, n: int) -> re.Match[str] | None:
    pattern = re.compile(rf"^(?:#{{1,6}}\s+{n}\.|{n}\.\s+\*\*)", re.MULTILINE)
    return pattern.search(text)


_MD_HEADING_NUMBER_RE = re.compile(r"^##\s+(\d+)\.", re.MULTILINE)
_FENCED_BLOCK_RE = re.compile(r"```(?:sh)?\n(.*?)```", re.DOTALL)


def _section_by_heading_number(text: str, n: int) -> str:
    """Slices `text` from the `## {n}.` heading (inclusive) to the next `## <digits>.` heading (or
    EOF) -- `honeypot/README.md`'s numbered-step shape (`## 1. Instance`, `## 2. Security group`,
    ...), independent of the exact title words after the number.
    """
    headings = list(_MD_HEADING_NUMBER_RE.finditer(text))
    start_idx = next((i for i, m in enumerate(headings) if int(m.group(1)) == n), None)
    assert start_idx is not None, f"honeypot/README.md has no '## {n}.' heading"
    start = headings[start_idx].start()
    end = headings[start_idx + 1].start() if start_idx + 1 < len(headings) else len(text)
    return text[start:end]


def test_every_relative_link_in_deploy_docs_resolves() -> None:
    """Interfaces -> test table row "links resolve" (task-05 brief line 213):
    `::test_every_relative_link_in_deploy_docs_resolves` -- for every `.md` file under
    `infra/deploy/` (recursively), `honeypot/` (recursively), and `docs/deployment.md`: every
    markdown link target `](...)` that is not `http(s)://`/`mailto:` (fragment stripped) must
    resolve to a real file relative to the linking file's own directory. A renamed/typo'd
    cross-link (e.g. the walkthrough linking `VERFIY.md` instead of `VERIFY.md`, or
    `iam/app-host-trust.jsn`) is the mutant this catches. NOTE (see module docstring): at BASE
    zero `](...)` links exist across the scanned set, so this test PASSES on BASE and is a
    regression guard from here on, per the brief's own allowance (task-05 brief Step 2).
    """
    broken: list[str] = []
    for md_file in _iter_scanned_markdown_files():
        text = md_file.read_text()
        for match in _MD_LINK_RE.finditer(text):
            target = match.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue  # pure in-page anchor, e.g. ](#section)
            resolved = (md_file.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{md_file}: link target {target!r} -> {resolved} does not exist")
    assert not broken, "broken relative link(s):\n" + "\n".join(broken)


def test_iam_documents_parse_and_have_no_wildcard_action_or_resource() -> None:
    """Interfaces -> test table row "IAM least privilege" (task-05 brief line 214):
    `::test_iam_documents_parse_and_have_no_wildcard_action_or_resource` -- all three IAM JSON
    documents parse; in `app-host-inline.json` no statement has `Action == "*"`, no `"*"` inside an
    Action list, no `Resource == "*"`, and every `Resource` string starts with `arn:aws:` (a
    wildcard grant on either axis is the mutant PRD §10 forbids -- the app host must read only its
    own SSM namespace and write only its own backup bucket); both trust documents
    (`app-host-trust.json`, `honeypot-host-trust.json`) allow only the principal service
    `ec2.amazonaws.com` -- any other/broader principal would let a different AWS principal assume
    the role.
    """
    for path in (_APP_TRUST, _APP_INLINE, _HONEYPOT_TRUST):
        assert path.exists(), (
            f"{path} does not exist yet -- task-05's GREEN step creates it per the brief's "
            "Interfaces block."
        )

    app_inline = json.loads(_APP_INLINE.read_text())
    app_trust = json.loads(_APP_TRUST.read_text())
    honeypot_trust = json.loads(_HONEYPOT_TRUST.read_text())

    for statement in app_inline["Statement"]:
        actions = _as_list(statement["Action"])
        assert "*" not in actions, (
            f"app-host-inline.json statement has a wildcard Action: {statement}"
        )
        resources = _as_list(statement["Resource"])
        for resource in resources:
            assert resource != "*", f"app-host-inline.json statement has Resource '*': {statement}"
            assert isinstance(resource, str) and resource.startswith("arn:aws:"), (
                f"app-host-inline.json Resource does not start with 'arn:aws:': {resource!r}"
            )

    for trust_doc, name in (
        (app_trust, "app-host-trust.json"),
        (honeypot_trust, "honeypot-host-trust.json"),
    ):
        for statement in trust_doc["Statement"]:
            services = _principal_services(statement)
            assert services == {"ec2.amazonaws.com"}, (
                f"{name} statement's Principal.Service is not exactly {{'ec2.amazonaws.com'}}: "
                f"{services}"
            )


def test_deploy_docs_carry_no_value_shaped_secret() -> None:
    """Interfaces -> test table row "no secret values" (task-05 brief line 215):
    `::test_deploy_docs_carry_no_value_shaped_secret` -- over every file under `infra/deploy/`
    (recursively) and every `honeypot/*.md` (non-recursive glob -- deliberately excludes
    `honeypot/docker-compose.yml`'s pinned Cowrie image digest, which is a non-`.md` file directly
    under `honeypot/` and so is never in this scanned set; also excludes
    `honeypot/shipper/README.md`, one directory deeper): 0 hits for an OpenAI-style key, a Postgres
    DSN with a real (non-`<placeholder>`) password, an AWS access-key id, or a 40+-char hex run
    (a token/hash/real digest). A committed real secret value is the mutant this catches
    (`.claude/rules/infra.md`: "No secret values anywhere in the repo"); `<POSTGRES_PASSWORD>`-style
    placeholders are exempt by construction because the literal `<` breaks the DSN regex. Also
    asserts the task's seven new artifacts exist -- otherwise this test would trivially pass on
    BASE (0 hits because the files scanned for hits don't exist yet), which is not the RED reason
    this task pins.
    """
    missing = [str(p) for p in _REQUIRED_NEW_ARTIFACTS if not p.exists()]
    assert not missing, (
        "deploy artifacts not created yet -- task-05's GREEN step creates them: "
        + ", ".join(missing)
    )

    scanned = [p for p in (_REPO_ROOT / "infra" / "deploy").rglob("*") if p.is_file()]
    scanned += sorted((_REPO_ROOT / "honeypot").glob("*.md"))

    hits: list[str] = []
    for path in sorted(scanned):
        text = path.read_text()
        for name, pattern in _SECRET_PATTERNS.items():
            if pattern.search(text):
                # Never print the matched value itself (.claude/rules/infra.md), only its
                # location and which pattern tripped.
                hits.append(f"{path}: {name}")
    assert not hits, (
        "value-shaped secret pattern(s) found (file: pattern name only):\n" + "\n".join(hits)
    )

    # The exemption itself, pinned directly against the pattern (the scanned set above never
    # reads honeypot/docker-compose.yml -- see this test's own docstring -- so a fixture line is
    # the only way to pin the exemption today; m7 task-08).
    digest_line = "    image: cowrie/cowrie@sha256:" + "ab" * 32
    assert not _SECRET_PATTERNS["40-plus-char-hex-run"].search(digest_line), (
        "the 40-plus-char-hex-run pattern must exempt a sha256:<64 hex> image digest"
    )
    bare_hex_line = "token=" + "a" * 40
    assert _SECRET_PATTERNS["40-plus-char-hex-run"].search(bare_hex_line), (
        "the 40-plus-char-hex-run pattern must still catch a bare 40+ hex run with no sha256: "
        "prefix"
    )


def test_walkthrough_names_every_step_and_both_hosts() -> None:
    """Interfaces -> test table row "walkthrough steps" (task-05 brief line 216):
    `::test_walkthrough_names_every_step_and_both_hosts` -- `infra/deploy/ec2-single-host.md` has a
    heading or bold-numbered line for each of steps 0-12 (task-05 brief lines 76-161), in
    ascending order, and contains the load-bearing literal strings that prove both hosts and the
    key operational gates are actually covered: `systemctl mask sshd` (the honeypot AND app host
    hardening step), both host role names, `push_ecr.sh`, `fetch-secrets.sh`,
    `alembic upgrade head`, `restore-rehearsal.sh`, `sentinelbrief-backup.timer`, `grey`
    (DNS -- never Proxied), and `history -c` (the SSM-parameter-typing step never leaves a secret
    in shell history). A walkthrough missing a step, out of order, or missing one of these strings
    (e.g. silently dropping the honeypot host, or telling the owner to Proxy the DNS records) is
    the mutant this catches.
    """
    text = _require(_EC2_WALKTHROUGH)

    last_pos = -1
    for n in _WALKTHROUGH_STEP_NUMBERS:
        match = _step_heading_match(text, n)
        assert match is not None, (
            f"ec2-single-host.md has no heading/bold-numbered line for step {n} (expected "
            f"'## {n}. ...' or '{n}. **...**')"
        )
        assert match.start() > last_pos, (
            f"ec2-single-host.md's step {n} heading appears before an earlier step's heading"
        )
        last_pos = match.start()

    for needle in (
        "systemctl mask sshd",
        "sentinelbrief-honeypot-host",
        "sentinelbrief-app-host",
        "push_ecr.sh",
        "fetch-secrets.sh",
        "alembic upgrade head",
        "restore-rehearsal.sh",
        "sentinelbrief-backup.timer",
        "grey",
        "history -c",
    ):
        assert needle in text, f"ec2-single-host.md missing required text: {needle!r}"


def test_verify_doc_is_filled_for_checks_0_to_10() -> None:
    """Interfaces -> test table row "VERIFY template" (task-05 brief line 217), AMENDED at the M6
    final review (ruling R16 -- approved pinned edit): `infra/deploy/VERIFY.md` is a RECORD, not a
    template. It still has headings `## 0.` through `## 11.` (task-05 brief lines 166-200, the
    AdvisorDesk `## N. <title>` shape); the no-LLM check (check 6) still greps both the `api` and
    `worker` container logs with `grep -ciE`; and the literal strings that prove the ingest-cap
    and hardening checks are actually written down are still required: `payload_too_large` + `411`
    (check 2's declared-length vs. chunked-body gates), `pg_database_size` (check 7),
    `docker-proxy` + `masked` (check 9, the honeypot `sshd` proof).

    What changed: the old assertion counted >= 12 literal `(recorded during deployment)` markers,
    which forced the M6 doc pass to KEEP each marker as a caption above the real output instead of
    replacing it -- a test holding the doc in its template state (task-06's own Verify block
    expected 0 markers at the soak's end; the two briefs disagreed and R16 resolved it in
    task-06's favour). Now: zero `(recorded during deployment)` markers anywhere, exactly one
    `(recorded at M8)` block (check 11, which is genuinely M8's work), and every check 0-10 must
    carry at least one ```text``` block whose first non-blank line is real output -- not empty,
    not a `(recorded ...)` marker. A doc-pass that pastes a check's output but forgets another
    check, or a future edit that re-empties a block, is the mutant this catches.
    """
    text = _require(_VERIFY_MD)

    for n in range(12):  # checks 0 through 11 inclusive
        heading = f"## {n}."
        assert heading in text, f"VERIFY.md has no {heading!r} heading"

    assert re.search(r"docker compose logs api[^\n]*\|\s*grep -ciE", text), (
        "VERIFY.md has no 'docker compose logs api ... | grep -ciE ...' line (check 6, the "
        "api-side no-LLM grep)"
    )
    assert re.search(r"docker compose logs worker[^\n]*\|\s*grep -ciE", text), (
        "VERIFY.md has no 'docker compose logs worker ... | grep -ciE ...' line (check 6, the "
        "worker-side no-LLM grep)"
    )

    recorded_count = text.count("(recorded during deployment)")
    assert recorded_count == 0, (
        f"VERIFY.md still has {recorded_count} '(recorded during deployment)' marker(s) -- it is a "
        "record of the deployment, not a template (M6 final review, ruling R16)"
    )
    m8_count = text.count("(recorded at M8)")
    assert m8_count == 1, (
        f"VERIFY.md has {m8_count} '(recorded at M8)' block(s), expected exactly 1 (check 11's "
        "rate-limit/SSE placeholders are the only work that is genuinely not M6's)"
    )

    text_block_re = re.compile(r"```text\n(.*?)```", re.DOTALL)
    marker_re = re.compile(r"^\(recorded\b.*\)$")
    for n in range(11):  # checks 0 through 10 -- check 11 is M8's
        section = _section_by_heading_number(text, n)
        filled = []
        for block in text_block_re.findall(section):
            first_line = next((line for line in block.splitlines() if line.strip()), "")
            if first_line.strip() and not marker_re.match(first_line.strip()):
                filled.append(first_line)
        assert filled, (
            f"VERIFY.md check {n} has no ```text``` block with real recorded output (every block "
            "is missing, empty, or still a '(recorded ...)' marker)"
        )

    for needle in ("payload_too_large", "411", "pg_database_size", "docker-proxy", "masked"):
        assert needle in text, f"VERIFY.md missing required text: {needle!r}"


def test_user_data_scripts_parse_and_disable_sshd() -> None:
    """Interfaces -> test table row "user-data scripts" (task-05 brief line 218):
    `::test_user_data_scripts_parse_and_disable_sshd` -- `bash -n` on both
    `infra/deploy/user-data-app.sh` and `honeypot/user-data.sh`; each file's text starts with
    `#!/bin/bash` and its exact second line is `set -euo pipefail` (task-01 re-review N4: `bash -n`
    happily parses a shebang-less file, but EC2 cloud-init runs nothing without a leading `#!` --
    a passing `bash -n` alone would not have caught a real `sshd` left running on a
    port-22-from-`0.0.0.0/0` security group); both scripts disable and mask `sshd`; the app script
    creates `/opt/sentinelbrief/geoip`; the honeypot script `chown`s the bind-mounted data dirs to
    uid/gid 999 (the Cowrie image's user) and creates the unprivileged `shipper` system account.
    """
    for path in (_USER_DATA_APP, _HONEYPOT_USER_DATA):
        text = _require(path)

        proc = _bash_dash_n(path)
        assert proc.returncode == 0, f"bash -n {path} failed:\n{proc.stderr}"

        assert text.startswith("#!/bin/bash"), (
            f"{path} does not start with '#!/bin/bash' -- EC2 cloud-init runs nothing without a "
            "shebang (task-01 re-review N4)"
        )
        lines = text.splitlines()
        second_line = lines[1] if len(lines) > 1 else "<missing>"
        assert second_line == "set -euo pipefail", (
            f"{path}'s second line is not 'set -euo pipefail': {second_line!r}"
        )
        assert "systemctl disable --now sshd" in text, (
            f"{path} missing 'systemctl disable --now sshd'"
        )
        assert "systemctl mask sshd" in text, f"{path} missing 'systemctl mask sshd'"

    app_text = _USER_DATA_APP.read_text()
    assert "/opt/sentinelbrief/geoip" in app_text, (
        "user-data-app.sh missing '/opt/sentinelbrief/geoip'"
    )

    honeypot_text = _HONEYPOT_USER_DATA.read_text()
    assert "chown -R 999:999" in honeypot_text, "honeypot/user-data.sh missing 'chown -R 999:999'"
    assert "useradd --system" in honeypot_text, "honeypot/user-data.sh missing 'useradd --system'"


def test_deployment_doc_has_resource_table_and_check_list() -> None:
    """Interfaces -> test table row "deployment.md" (task-05 brief line 219):
    `::test_deployment_doc_has_resource_table_and_check_list` -- `docs/deployment.md` carries the
    "Resources (recorded at deploy)" table with at least 8 rows and ZERO `<recorded at deploy>`
    placeholder cells (task-06 doc pass: the deploy happened and every value is recorded now --
    task-05 brief line 202-205's fields: region, account id, 2 VPC ids, 2 instance ids, 2 EIPs, SG
    names, role names, bucket, ECR repos, domain), and names the `VERIFY.md` check range `0-11` in
    its Verification section. A `docs/deployment.md` left with the old "recorded in the execution
    ledger at M6, not here" placeholder wording, or with any cell still unfilled, is the mutant
    this catches.
    """
    text = _require(_DEPLOYMENT_DOC)

    heading = "## Resources (recorded at deploy)"
    assert heading in text, (
        "docs/deployment.md has no 'Resources (recorded at deploy)' section/table"
    )

    # Scope the row count to just the Resources table (up to the next '## ' heading), not every
    # markdown table in the file (e.g. the "Differences from AdvisorDesk" table lower down).
    section_start = text.index(heading) + len(heading)
    next_heading = text.find("\n## ", section_start)
    section = text[section_start : next_heading if next_heading != -1 else len(text)]

    data_row_count = (
        sum(
            1
            for line in section.splitlines()
            if line.startswith("| ") and not line.startswith("|---")
        )
        - 1
    )  # subtract the header row ("| Resource | Value |")
    assert data_row_count >= 8, (
        f"docs/deployment.md's resource table has only {data_row_count} data row(s), need >= 8"
    )

    recorded_count = section.count("<recorded at deploy>")
    assert recorded_count == 0, (
        f"docs/deployment.md still has {recorded_count} unfilled '<recorded at deploy>' cell(s)"
    )

    assert ("0–11" in text) or ("checks 0–11" in text), (
        "docs/deployment.md does not name the VERIFY.md check range '0–11'"
    )


def test_honeypot_readme_points_at_user_data_file() -> None:
    """Interfaces -> test table row "runbook cross-link" (task-05 brief line 220):
    `::test_honeypot_readme_points_at_user_data_file` -- `honeypot/README.md` step 3's actual
    paste block references the extracted `honeypot/user-data.sh` file instead of re-embedding the
    script inline (task-05 brief line 59: "honeypot/README.md step 3 becomes 'paste
    `honeypot/user-data.sh`'"). NOTE: a bare "does step 3's text contain the string
    'honeypot/user-data.sh' anywhere" check would degenerately PASS on BASE already -- task-01's
    own "Notes on each line" already forward-references the exact filename task-05 creates
    (`honeypot/README.md` line 51: "Task-05 extracts this block verbatim into
    `honeypot/user-data.sh`"), which is inside step 3's section. So this test additionally pins
    that step 3's fenced code block itself no longer re-embeds the full inline script (the
    `dnf install` line) -- a README that keeps duplicating the script inline instead of switching
    the paste step to reference the file is the mutant this catches.
    """
    text = _require(_HONEYPOT_README)
    step3 = _section_by_heading_number(text, 3)

    assert "honeypot/user-data.sh" in step3, (
        "honeypot/README.md step 3 does not reference honeypot/user-data.sh"
    )

    inline_script_blocks = [
        block for block in _FENCED_BLOCK_RE.findall(step3) if "dnf install" in block
    ]
    assert not inline_script_blocks, (
        "honeypot/README.md step 3 still pastes the full user-data script inline instead of "
        "pointing at honeypot/user-data.sh (task-05 brief line 59)"
    )
