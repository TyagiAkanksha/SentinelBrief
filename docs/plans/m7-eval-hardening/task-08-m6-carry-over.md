---
id: task-08
milestone: m7-eval-hardening
depends_on: [M6 tag]
status: planned
spec: M6 whole-branch final review §5 (`.superpowers/sdd/_archive/m6-real-data-deploy/m6-final-review.md`, DEFER-TO-M7 rows — the fix shapes there are the contract); PRD §10.4 (the honeypot is assumed compromised), §11; `honeypot/shipper/` package rules (httpx-only, atomic spool, retry classes); `.claude/rules/infra.md`
---

# task-08 — M6 carry-over: the shipper v0.2 hardening bundle, the per-fence runbook guard, the `_safe_name` and secret-scan regexes

## Goal

Close the items the M6 gate review deferred, in one task, without changing any shipper interface
that the honeypot's unit file or the api's ingest contract depends on. The bundle: (a) shipper
hardening — SIGTERM handler that flushes the in-flight batch to the spool; an unreadable state dir
is a clean startup error; idle-flush goes through the spool in `main`; the `_session_id_for_log`
fallback; vanished-while-open and a corrupt `tail.json` are survivable; a cold start reads the log
in ≤ 8 MiB chunks and stops a batch at N lines (M9); the tail offset is committed AFTER the spool
write so delivery is at-least-once (M11 — the api dedups by fingerprint, so a replayed session is a
200 and never re-triaged; this is a pinned-file edit, approved by this brief); the ENOENT branch is
pinned (M12); `OpenSession.events` is bounded before close (new M5: a session past
`SHIPPER_MAX_EVENTS` is truncated in place, not at close); the unit file's sandboxing gaps (new M8:
`ProtectSystem=strict`, `PrivateTmp`, `NoNewPrivileges`, `CapabilityBoundingSet=`, per the review's
row). (b) `tests/test_deploy_docs_guards.py`'s walkthrough guard asserts per fenced block, not
whole-text (M6 re-review N1). (c) `scripts/check_real_sessions.py::_safe_name` collapses markdown
emphasis (t06 N6); the secret-scan test's 40-plus-hex regex exempts a documented `sha256:` image
digest so a full Cowrie digest can be recorded (new M6; pinned edit approved). (d) Owner item
surfaced, not implemented: S3 bucket versioning against `PutObject` overwrite (new M4).

## Context (read ONLY these)

- The M6 final review §5 rows named above (verbatim fix shapes), `honeypot/shipper/` (all modules
  and `sentinelbrief-shipper.service`), `honeypot/shipper/README.md`, `tests/test_shipper_*.py`,
  `tests/test_deploy_docs_guards.py`, `scripts/check_real_sessions.py`,
  `tests/test_check_real_sessions*.py`, `tests/test_deploy_docs.py` (the secret-scan test).

## Files

- Modify: `honeypot/shipper/sentinelbrief_shipper/{main,tail,assemble,spool}.py`,
  `honeypot/shipper/sentinelbrief-shipper.service`, `honeypot/shipper/README.md`,
  `tests/test_deploy_docs_guards.py`, `scripts/check_real_sessions.py`
- Pinned edits approved by this brief (exactly): the offset-after-spool ordering assertion in the
  pinned shipper test that pins M11's old order; the 40-plus-hex regex in
  `tests/test_deploy_docs.py`'s secret-scan test (add the `sha256:` exemption).
- Test-author: `tests/test_shipper_v02.py` (new), the two approved pinned edits, one per-fence
  assertion in `tests/test_deploy_docs_guards.py`, `tests/test_check_real_sessions_safe_name.py`.

## Interfaces

Unchanged public surface: `ShipperConfig.from_env` env names, the unit's `ExecStart`, the spool
layout, `RunOnceResult`. New: `SHIPPER_READ_CHUNK_BYTES` (default 8 MiB) and
`SHIPPER_MAX_BATCH_LINES` (default 2000) in `ShipperConfig` + `honeypot/shipper/README.md`'s tunables.
`Spool.write` then `Tailer.commit_offset` is the new ordering in `run_once` (at-least-once).

## Interfaces → test table

| row | test | failure branch |
|---|---|---|
| SIGTERM flush | `test_shipper_v02.py::test_sigterm_flushes_in_flight_batch_to_spool` | a batch in memory at SIGTERM lands in the spool, not lost |
| at-least-once | `::test_offset_commits_after_spool_write` (+ the approved pinned-edit flip) | a crash between spool and commit re-ships; never drops |
| chunked cold start | `::test_cold_start_reads_in_bounded_chunks_and_batches` | ≤ 8 MiB per read; batch stops at N lines |
| unreadable state dir | `::test_unreadable_state_dir_is_a_clean_startup_error` | exit 1 with a WARNING naming errno only |
| corrupt tail.json / vanished file | `::test_corrupt_tail_json_and_vanished_log_are_survivable` | both continue from a sane state |
| bounded open session | `::test_open_session_events_bounded_before_close` | truncation happens in place |
| ENOENT | `::test_enoent_branch_logs_and_continues` | caplog assertion |
| unit sandboxing | `::test_unit_file_has_sandboxing_directives` | the four directives present |
| per-fence guard | `tests/test_deploy_docs_guards.py::test_honeypot_put_role_policy_present_in_a_fence` | the M6 re-review's surviving mutant R3a dies |
| `_safe_name` | `test_check_real_sessions_safe_name.py::test_markdown_emphasis_collapsed` | `**` and `__` collapsed |
| secret scan | existing test with the exemption | a `sha256:<64 hex>` after `image:` passes; a bare 40-hex still fails |

## Steps (TDD)

- [ ] Steps 1–2 (test-author): RED; commit `test(honeypot,deploy): shipper v0.2, per-fence guard, safe_name, digest exemption RED (m7 task-08)`.
- [ ] Steps 3–4 (implementer): GREEN; full gates (with `--cov=sentinelbrief_shipper`); commit `fix(honeypot,deploy): shipper v0.2 hardening; per-fence guard; safe_name; digest exemption (m7 task-08)`.
- [ ] Step 5 (controller, deploy grant): ship the new shipper package + unit to the honeypot via SSM (the M6 transport: tgz → base64 → install; `systemctl daemon-reload && restart`), confirm `delivered` resumes and `dead` stays 0; ledger the RSS before/after.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_shipper_v02.py tests/test_deploy_docs_guards.py tests/test_check_real_sessions_safe_name.py tests/test_deploy_docs.py   # all pass, 0 skipped
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov=sentinelbrief_shipper --cov-fail-under=90
```

## Acceptance

- Every DEFER-TO-M7 row of the M6 final review is closed or explicitly re-deferred with a reason;
  the honeypot runs the new shipper with `dead` 0 and no interface change; the M6 re-review's
  surviving mutant dies.
