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

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EC2_WALKTHROUGH = _REPO_ROOT / "infra" / "deploy" / "ec2-single-host.md"
_USER_DATA_APP = _REPO_ROOT / "infra" / "deploy" / "user-data-app.sh"
_HONEYPOT_USER_DATA = _REPO_ROOT / "honeypot" / "user-data.sh"
_IAM_DIR = _REPO_ROOT / "infra" / "deploy" / "iam"


def _as_list(value: object) -> list[object]:
    """IAM allows a bare string or a list anywhere a list is accepted (copied from
    `tests/test_deploy_docs.py::_as_list`, not imported — CONVENTIONS.md §10: no
    cross-test-file imports)."""
    return value if isinstance(value, list) else [value]


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


def test_iam_deny_documents_bound_both_instance_roles() -> None:
    """M6 final-review C1/I1: `AmazonSSMManagedInstanceCore` — attached to BOTH instance roles —
    allows `ssm:GetParameter` on `Resource: "*"`, so an Allow-only policy set bounds neither role.
    The boundary is an explicit Deny per role, and this test is what stops a future edit deleting
    or weakening one. `tests/test_deploy_docs.py::test_iam_documents_parse_and_have_no_wildcard_
    action_or_resource` reads `statement["Resource"]` and would `KeyError` on the app-host Deny's
    `NotResource`, which is why the Deny documents live in their own files and are pinned here
    (that file is pinned; this one is not).

    Pure JSON/text pins — no live AWS. The live proof is the
    `simulate-principal-policy` → `explicitDeny` block the walkthrough's step 1 now carries.
    """
    honeypot_deny = _IAM_DIR / "honeypot-host-deny.json"
    app_deny = _IAM_DIR / "app-host-deny.json"

    for path in (honeypot_deny, app_deny):
        assert path.exists(), f"{path} does not exist (final review C1/I1: the Deny is the control)"

    honeypot_doc = json.loads(honeypot_deny.read_text())
    honeypot_statements = [s for s in honeypot_doc["Statement"] if s["Effect"] == "Deny"]
    assert honeypot_statements, "honeypot-host-deny.json has no Deny statement"
    honeypot_actions = {a for s in honeypot_statements for a in _as_list(s["Action"])}
    for action in (
        "ssm:GetParameter",
        "ssm:GetParameters",
        "ssm:GetParametersByPath",
        "kms:Decrypt",
    ):
        assert action in honeypot_actions, (
            f"honeypot-host-deny.json does not deny {action!r}: {sorted(honeypot_actions)}"
        )

    app_doc = json.loads(app_deny.read_text())
    app_statements = [s for s in app_doc["Statement"] if s["Effect"] == "Deny"]
    assert app_statements, "app-host-deny.json has no Deny statement"
    app_actions = {a for s in app_statements for a in _as_list(s["Action"])}
    for action in ("ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"):
        assert action in app_actions, (
            f"app-host-deny.json does not deny {action!r}: {sorted(app_actions)}"
        )
    # The app host must keep reading its OWN namespace, so the Deny is scoped by NotResource.
    for statement in app_statements:
        assert "NotResource" in statement, (
            "app-host-deny.json's Deny must use NotResource so /sentinelbrief/* stays readable: "
            f"{statement}"
        )
        assert any(
            str(r).endswith("parameter/sentinelbrief/*") for r in _as_list(statement["NotResource"])
        ), f"app-host-deny.json's NotResource does not exempt /sentinelbrief/*: {statement}"

    walkthrough = _EC2_WALKTHROUGH.read_text()
    for needle in (
        "iam/honeypot-host-deny.json",
        "iam/app-host-deny.json",
        "simulate-principal-policy",
        "explicitDeny",
    ):
        assert needle in walkthrough, (
            f"ec2-single-host.md step 1 does not mention {needle!r} (final review C1/I1)"
        )


def test_both_run_instances_blocks_require_imdsv2_at_one_hop() -> None:
    """M6 final review M2: AL2023 defaults to `HttpPutResponseHopLimit=2`, which lets a process
    inside a container on the bridge network reach IMDS and mint the instance role's credentials
    — on the honeypot, a host the PRD tells us to assume is fully compromised. Both
    `run-instances` blocks must pin `HttpTokens=required,HttpPutResponseHopLimit=1`, and the
    walkthrough must carry the in-place `modify-instance-metadata-options` form for instances
    that are already running (the two live instances were fixed that way, not relaunched).
    """
    text = _EC2_WALKTHROUGH.read_text()
    count = text.count("HttpTokens=required,HttpPutResponseHopLimit=1")
    assert count >= 2, (
        f"ec2-single-host.md has only {count} "
        "'HttpTokens=required,HttpPutResponseHopLimit=1' occurrence(s), expected >= 2 (the app "
        "host's and the honeypot's run-instances blocks)"
    )
    assert "modify-instance-metadata-options" in text, (
        "ec2-single-host.md does not carry the in-place "
        "'aws ec2 modify-instance-metadata-options' form for an already-running instance"
    )


def test_prod_readme_geoip_one_off_uses_the_form_that_works() -> None:
    """M6 final review I2: `prod/README.md`'s geoip fence is what `ec2-single-host.md` step 8
    calls "the exact command", and it prescribed mounting `/opt/sentinelbrief/geoip` read-write on
    the `api` service — whose own definition already mounts that same target `:ro`. The duplicate
    mount point failed on deploy day. The working form skips the service's dependencies
    (`--no-deps`), writes into a separate `/tmp/geoip` target, and moves the files into place
    afterwards. Both runbooks must agree; `ec2-single-host.md` already carries the corrected form.
    """
    readme = (_REPO_ROOT / "infra" / "deploy" / "prod" / "README.md").read_text()
    fence = readme.split("## Geoip one-off", 1)
    assert len(fence) == 2, "prod/README.md has no '## Geoip one-off' section"
    section = fence[1].split("\n## ", 1)[0]

    assert "--no-deps" in section, (
        "prod/README.md's geoip one-off does not pass --no-deps (final review I2)"
    )
    assert "-v /tmp/geoip:/app/infra/geoip" in section, (
        "prod/README.md's geoip one-off does not use the separate /tmp/geoip mount target — "
        "mounting /opt/sentinelbrief/geoip rw collides with the api service's own :ro mount "
        "(final review I2)"
    )
    assert "-v /opt/sentinelbrief/geoip:/app/infra/geoip" not in section, (
        "prod/README.md's geoip one-off still mounts /opt/sentinelbrief/geoip read-write on the "
        "api service — the form that failed on deploy day (final review I2)"
    )


def test_walkthrough_waits_for_cloud_init_after_each_sudo_i() -> None:
    """task-05 review N8: the SSM agent answers as soon as it is up, which can be before the
    instance's user-data has finished; a `docker compose` command run in that window fails for a
    reason that looks like a bug. Both on-box entry points (step 7's app host, step 9's honeypot)
    must block on `cloud-init status --wait` first.
    """
    text = _EC2_WALKTHROUGH.read_text()
    count = text.count("cloud-init status --wait")
    assert count >= 2, (
        f"ec2-single-host.md has only {count} 'cloud-init status --wait' occurrence(s), expected "
        ">= 2 (one after each on-box `sudo -i`: step 7's app host and step 9's honeypot)"
    )


def test_shipper_tarball_paste_never_uses_base64_w0() -> None:
    """task-05 fix-2 review N1, re-graded FIX-NOW at the M6 final review: `base64 -w0` produces one
    40,000+ character line, and the heredoc that receives it is read from the SSM session's tty in
    canonical mode, whose ~4096-byte line-discipline buffer silently discards the rest. The paste
    arrives truncated and `tar xzf` fails. Nothing caught the regression before — the runbook's
    prose explains the hazard, but no test stopped a future edit from "tidying" the command.

    The check is scoped to the FENCED commands so the prose can keep naming `-w0` to explain why
    it is wrong. The `sha256sum` compare is the runtime guard for a truncated paste, so both ends
    of it must still be present: once on the laptop, once on the box.
    """
    text = _EC2_WALKTHROUGH.read_text()

    fenced = "\n".join(re.findall(r"```(?:sh)?\n(.*?)```", text, re.DOTALL))
    assert "base64 -w0" not in fenced, (
        "ec2-single-host.md has a fenced `base64 -w0` command — its single-line output is "
        "silently truncated by the SSM session's tty line buffer (task-05 review N1)"
    )
    assert "base64 /tmp/shipper.tgz" in fenced, (
        "ec2-single-host.md no longer carries the default-wrapped `base64 /tmp/shipper.tgz` command"
    )

    count = text.count("sha256sum /tmp/shipper.tgz")
    assert count >= 2, (
        f"ec2-single-host.md has only {count} 'sha256sum /tmp/shipper.tgz' line(s), expected >= 2 "
        "(the laptop side and the on-box side — comparing them is what catches a truncated paste)"
    )
