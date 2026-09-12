"""New (unpinned) file, m6 task-05 fix-2: durable regression guards for two mutations that
`tests/test_deploy_docs.py` (pinned) does not catch — recorded in
`.superpowers/sdd/m6-real-data-deploy/task-05-review.md`'s "Re-review 1" mutation table as (a)
(reverting `--ip-permissions ... Ipv6Ranges=...` back to the invalid `--cidr-ipv6 ::/0`) and (c)
(re-appending `usermod -aG docker ssm-user` to `honeypot/user-data.sh`): both mutations left every
test in `tests/test_deploy_docs.py` green. `tests/test_deploy_docs.py` is pinned (sha256 in the
test-author's report), so these two new assertions live here instead, per the fix-2 brief's
ruling — adding a new test file is always allowed; editing a pinned one is not.

Pure text pins — no docker, no live AWS, no SSM, same style as `tests/test_deploy_docs.py`.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EC2_WALKTHROUGH = _REPO_ROOT / "infra" / "deploy" / "ec2-single-host.md"
_USER_DATA_APP = _REPO_ROOT / "infra" / "deploy" / "user-data-app.sh"
_HONEYPOT_USER_DATA = _REPO_ROOT / "honeypot" / "user-data.sh"


def test_walkthrough_uses_ip_permissions_for_ipv6() -> None:
    """Mutation (a): `infra/deploy/ec2-single-host.md`'s IPv6 security-group rules must use the
    long-form `--ip-permissions ... Ipv6Ranges=[{CidrIpv6=::/0}]` shape (task-05 fix-1, review
    I1) — `--cidr-ipv6` is not a valid AWS CLI option and every command using it fails with
    `Unknown options` at deploy time, proven live against the installed CLI with `--dry-run`. The
    walkthrough's own prose avoids the literal flag name too (rephrased at fix-2), so this is a
    real absence, not a coincidental one.
    """
    text = _EC2_WALKTHROUGH.read_text()
    assert "--cidr-ipv6" not in text, (
        "ec2-single-host.md contains the invalid AWS CLI flag '--cidr-ipv6' (task-05 review I1 "
        "regression — mutation (a) in re-review 1)"
    )
    count = text.count("Ipv6Ranges=[{CidrIpv6=::/0}]")
    assert count >= 2, (
        f"ec2-single-host.md has only {count} 'Ipv6Ranges=[{{CidrIpv6=::/0}}]' occurrence(s), "
        "expected >= 2 (the app-host and honeypot security-group ingress rules)"
    )


def test_user_data_scripts_never_usermod_ssm_user() -> None:
    """Mutation (c): neither user-data script may contain `usermod` — AL2023's SSM Agent creates
    `ssm-user` lazily at the first session, so a `usermod -aG docker ssm-user` at cloud-init time
    aborts the script under `set -euo pipefail` (task-05 fix-1, review I3; controller ruling
    R11/PC2: DELETE, not guard). Both scripts must also carry `Storage=persistent` so the journald
    budget they set (`SystemMaxUse=...`) is not inert on a volatile-by-default AL2023 journal
    (review M6).
    """
    for path in (_USER_DATA_APP, _HONEYPOT_USER_DATA):
        text = path.read_text()
        assert "usermod" not in text, (
            f"{path} contains 'usermod' (task-05 review I3 regression — mutation (c) in "
            "re-review 1)"
        )
        assert "Storage=persistent" in text, f"{path} is missing 'Storage=persistent' (review M6)"
