"""Pins `infra/deploy/push_ecr.sh` and `infra/deploy/prod/fetch-secrets.sh` against the m6 task-03
brief's Interfaces block: `push_ecr.sh` builds and pushes both images with a required
`AWS_ACCOUNT_ID` guard and git-SHA tags; `fetch-secrets.sh` renders SSM parameters into two
root-only files and never echoes a decrypted value to stdout/stderr
(`.claude/rules/infra.md`: "No secret values anywhere in the repo").

`bash -n` (a syntax-only parse, no execution) is enough to pin shape without running either script
against real AWS credentials or SSM parameters.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PUSH_ECR = _REPO_ROOT / "infra" / "deploy" / "push_ecr.sh"
_FETCH_SECRETS = _REPO_ROOT / "infra" / "deploy" / "prod" / "fetch-secrets.sh"

_REQUIRED_PARAMS = {"DATABASE_URL", "LLM_API_KEY", "INGEST_HMAC_SECRET", "ADMIN_TOKEN"}
_OPTIONAL_PARAMS = {"ABUSEIPDB_API_KEY"}

_FOR_P_LINE = re.compile(r"for P in ([^\n;]+)")


def _bash_dash_n(path: Path) -> subprocess.CompletedProcess[str]:
    """Syntax-checks a shell script without executing it."""
    return subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=10)


def test_push_ecr_shape() -> None:
    """Brief Interfaces (`infra/deploy/push_ecr.sh`): valid bash; fails fast on a missing
    `AWS_ACCOUNT_ID`; builds for a stated `--platform`; pushes both `sentinelbrief/api` and
    `sentinelbrief/web` repos; tags with `git rev-parse --short HEAD`; bakes `NEXT_PUBLIC_API_URL`
    into the web build and points its server-side `API_URL` at the compose network; is executable
    and `set -euo pipefail`-safe (a partial failure must not silently continue to `docker push`).
    """
    assert _PUSH_ECR.exists(), (
        f"{_PUSH_ECR} does not exist yet — task-03's GREEN step creates it per the brief's "
        "Interfaces block."
    )
    text = _PUSH_ECR.read_text()

    proc = _bash_dash_n(_PUSH_ECR)
    assert proc.returncode == 0, f"bash -n {_PUSH_ECR} failed:\n{proc.stderr}"

    assert "AWS_ACCOUNT_ID" in text, text
    assert "ERROR: AWS_ACCOUNT_ID" in text, text
    assert "--platform" in text, text
    assert "sentinelbrief/api" in text, text
    assert "sentinelbrief/web" in text, text
    # Semantics, not a literal form (m6 task-03 fix-1, M2): either the bare `git rev-parse --short
    # HEAD` or AdvisorDesk's `git -C <path> rev-parse --short HEAD` resolves the SHA the same way.
    assert re.search(r"git (-C [^\n]+ )?rev-parse --short HEAD", text), text
    assert "NEXT_PUBLIC_API_URL=" in text, text
    assert "API_URL=http://api:8000" in text, text
    assert "set -euo pipefail" in text, text

    assert os.access(_PUSH_ECR, os.X_OK), f"{_PUSH_ECR} is not executable"


def test_fetch_secrets_shape_never_echoes_values() -> None:
    """Brief Interfaces (`infra/deploy/prod/fetch-secrets.sh`): valid bash; root-only output
    (`umask 077` + `chmod 600`); decrypts SecureString SSM parameters (`--with-decryption`); the
    required parameter set is exactly `{DATABASE_URL, LLM_API_KEY, INGEST_HMAC_SECRET,
    ADMIN_TOKEN}` and the optional set is exactly `{ABUSEIPDB_API_KEY}`; `POSTGRES_PASSWORD` is
    written to `.env.postgres`; `MAXMIND_LICENSE_KEY` is never rendered to a file (it is read
    inline by the deploy-time geoip one-off, prod/README.md); and no decrypted value `$V` is ever
    echoed/printf'd to stdout or stderr — the only sanctioned `$V` use writes it into the output
    file.
    """
    assert _FETCH_SECRETS.exists(), (
        f"{_FETCH_SECRETS} does not exist yet — task-03's GREEN step creates it per the brief's "
        "Interfaces block."
    )
    text = _FETCH_SECRETS.read_text()

    proc = _bash_dash_n(_FETCH_SECRETS)
    assert proc.returncode == 0, f"bash -n {_FETCH_SECRETS} failed:\n{proc.stderr}"

    assert "umask 077" in text, text
    assert "chmod 600" in text, text
    assert "--with-decryption" in text, text

    for_lines = _FOR_P_LINE.findall(text)
    parsed_sets = [set(re.split(r"[\s,]+", line.strip())) - {""} for line in for_lines]
    assert _REQUIRED_PARAMS in parsed_sets, (
        f"no `for P in ...` line lists exactly the required parameter set "
        f"{_REQUIRED_PARAMS}: {parsed_sets}"
    )
    assert _OPTIONAL_PARAMS in parsed_sets, (
        f"no `for P in ...` line lists exactly the optional parameter set "
        f"{_OPTIONAL_PARAMS}: {parsed_sets}"
    )

    assert "POSTGRES_PASSWORD" in text, text
    assert ".env.postgres" in text, text
    assert "MAXMIND_LICENSE_KEY" not in text, text

    assert not re.search(r"echo[^\n]*\$V", text), "fetch-secrets.sh echoes $V to stdout"
    assert not re.search(r">&2[^\n]*\$V", text), "fetch-secrets.sh writes $V to stderr"
    assert re.search(r"printf\s+'%s=%s\\n'\s+\"\$P\"\s+\"\$V\"\s*>>\s*\"\$OUT\"", text), (
        'no `printf \'%s=%s\\n\' "$P" "$V" >> "$OUT"` line — the only sanctioned $V use'
    )
