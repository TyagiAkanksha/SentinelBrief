---
id: task-01
milestone: m6-real-data-deploy
depends_on: []
status: planned
spec: PRD.md §3 (the honeypot VM is network-isolated from the app host; it shares no credentials, no SSH keys, no database access; it can only POST to one ingest URL with an HMAC secret), §10.4 (separate provider account or isolated VPC; no shared secrets; outbound only to the ingest URL — assume it will be fully compromised), §10.9 (SSM-only management, no real `sshd` on any port — Cowrie owns port 22), §11 Phase 1 ("Honeypot host" paragraph), §1.2 (one alert = one Cowrie session), §12 M6; `docs/deployment.md` → "Honeypot host"; `.claude/rules/infra.md`; `CONVENTIONS.md` §11; `.claude/skills/cowrie-fixture/references/cowrie-events.md` (the JSON log shape the shipper reads)
---

# task-01 — `honeypot/`: Cowrie compose file (official image, JSON log on a named volume, port 22 owned outright), the host runbook (Amazon Linux 2023, SSM-only, `sshd` disabled, egress 443), `assets.yaml` reconciled with the real sensor, static pin tests

## Goal

The honeypot host runs exactly one thing: Cowrie in Docker, listening on the host's real port 22
(mapped to the container's 2222 — Cowrie's default), writing one JSON object per event to
`cowrie.json` on a named volume the shipper (task-02) tails. Everything else about the host is
hardening: Amazon Linux 2023 with `sshd` disabled and masked so Cowrie can own 22, management
only through SSM Session Manager, a security group that admits TCP 22 from anywhere and allows
outbound TCP 443 only (the ingest hostname and the SSM endpoints), Docker log rotation, and
`restart: unless-stopped` so a reboot brings Cowrie back without a human. The host never holds a
repo checkout, a database URL, an LLM key, or any secret but `INGEST_HMAC_SECRET` (task-02 puts
that in the shipper's systemd `EnvironmentFile`, not here). `honeypot/assets.yaml` names the
real sensor (`hp-use-01`) as the one deployed honeypot; the other nineteen entries remain the
synthetic fleet the fixtures and golden set use, and the file says so. Nothing in this task
touches AWS: the console/CLI steps are written as an owner-run runbook (task-05 sequences them);
what this task delivers is the compose file, the Cowrie config, the runbook text, and tests that
pin the shape of both files.

## Context (read ONLY these)

- `PRD.md` §3, §10.4, §10.9, §11 ("Honeypot host"), §12 M6.
- `docs/plans/m6-real-data-deploy.md` — Global Constraints (no secret value in the repo; the
  honeypot host shares nothing but `INGEST_HMAC_SECRET`; egress 443 only; every production
  service has json-file log rotation).
- `docs/deployment.md` → "Honeypot host" (the paragraph this task makes concrete).
- `.claude/rules/infra.md`; `CONVENTIONS.md` §11; `.claude/skills/cowrie-fixture/references/cowrie-events.md`
  (the `cowrie.json` line shape — `eventid`, `timestamp`, `session`, `src_ip`, `sensor`, …).
- Code you build on: `tests/test_compose_config.py` (the `docker compose config --format json`
  rendering pattern from a `tmp_path` copy — copy the helper, do not import it), `honeypot/assets.yaml`
  (20 synthetic sensors), `infra/docker-compose.yml` (the `x-logging` anchor to mirror).
- Cowrie facts (verified against the upstream README/`docker/README.rst` at briefing time; the
  implementer re-verifies the image tag by pulling it): the official image is `cowrie/cowrie`;
  inside the container Cowrie listens on `2222` (ssh) and `2223` (telnet) and its files live
  under `/cowrie/cowrie-git`; the JSON output plugin writes `var/log/cowrie/cowrie.json` and is
  enabled by `[output_jsonlog] enabled = true` (default in `etc/cowrie.cfg.dist`); the sensor
  name comes from `[honeypot] sensor_name`; a bind-mounted `etc/cowrie.cfg` overrides the
  `.dist` defaults. **Pin the image by digest** in the compose file before the first deploy
  (`docker pull cowrie/cowrie:latest && docker inspect --format '{{index .RepoDigests 0}}'`),
  recorded as `image: cowrie/cowrie@sha256:<digest>` — the "never a moving tag on the box" rule
  applies to third-party images too. At briefing (2026-09-11) `docker buildx imagetools inspect
  cowrie/cowrie:latest` reported the multi-arch manifest digest
  `sha256:42e01e0e5fe705a0a63dacc0f1992b2d230740ac149af7675f48197abf740d44` (linux/amd64 +
  linux/arm64 — so the `t4g.nano` arm64 host is fine); the implementer re-runs that command,
  and if the digest still matches commits the `@sha256:` form directly (the pin test accepts
  either form; the runbook still makes re-checking the digest a required step before deploy).

## Files

- Create: `honeypot/docker-compose.yml`, `honeypot/etc/cowrie.cfg`, `honeypot/README.md`
  (the host runbook), `fixtures/cowrie/README.md` (where task-02's replay log will live; one
  paragraph)
- Create (test-author): `tests/test_honeypot_compose.py`
- Modify: `honeypot/assets.yaml` (header comment + `hp-use-01` marked as the deployed sensor),
  `docs/deployment.md` → "Honeypot host" (three sentences: the compose file path, the digest
  pin step, the runbook path), `README.md` (one line under Deployment pointing at
  `honeypot/README.md`)

## Interfaces

- **Consumes:** the `x-logging` anchor shape from `infra/docker-compose.yml`; the Cowrie JSON
  log path; `honeypot/assets.yaml`'s existing schema (`assets: {<sensor>: {role, exposure,
  criticality}}`).
- **Produces (task-02 and task-05 rely on — produce exactly):**

  ```yaml
  # honeypot/docker-compose.yml
  name: sentinelbrief-honeypot
  x-logging: &default-logging { driver: json-file, options: { max-size: "10m", max-file: "3" } }
  services:
    cowrie:
      image: cowrie/cowrie:latest        # PIN BY DIGEST before the first deploy (honeypot/README.md step 4): cowrie/cowrie@sha256:<digest>
      ports:
        - "22:2222"                      # the host's real port 22 — sshd is disabled and masked on this host (README step 2), Cowrie owns it
      volumes:
        - ./data/log:/cowrie/cowrie-git/var/log/cowrie            # cowrie.json (+ daily rotations) — the host-side shipper (task-02, an unprivileged systemd unit) tails /opt/sentinelbrief-honeypot/data/log/cowrie.json; a named volume would force the shipper to run as root under /var/lib/docker
        - ./data/lib:/cowrie/cowrie-git/var/lib/cowrie            # downloads + tty logs (attacker artifacts) — never committed: honeypot/data/ is gitignored AND dockerignored
        - ./etc/cowrie.cfg:/cowrie/cowrie-git/etc/cowrie.cfg:ro   # sensor name + JSON output; no secrets in it
      restart: unless-stopped
      logging: *default-logging
  # NO env_file, NO environment secrets, NO other services, NO top-level volumes (the shipper is a
  # systemd unit on the host, task-02). Both bind sources are created by the runbook's user-data,
  # owned by uid:gid 1000:1000 — the `cowrie` user inside the official image — and are world-readable
  # (Cowrie's files land 0644), which is all the shipper's read access needs.
  ```

  ```ini
  # honeypot/etc/cowrie.cfg — only the keys that differ from cowrie.cfg.dist
  [honeypot]
  sensor_name = hp-use-01          # the deployed sensor; matches honeypot/assets.yaml and the alert's `sensor`
  hostname = svr04                 # the fake hostname attackers see (Cowrie default kept)
  [output_jsonlog]
  enabled = true
  logfile = ${honeypot:log_path}/cowrie.json
  ```

  `honeypot/README.md` (the runbook, owner-run; task-05 sequences it with the app host):
  1. Instance: Amazon Linux 2023, `t4g.nano` (arm64) or `t3.nano`, in a **separate VPC** (or
     account), public subnet, Elastic IP; instance role = `AmazonSSMManagedInstanceCore` only.
  2. Security group: inbound TCP 22 from `0.0.0.0/0` (and `::/0`); outbound TCP 443 to
     `0.0.0.0/0` only (the ingest hostname and the SSM/EC2-messages endpoints — narrow to VPC
     endpoints later if the owner adds them); nothing else in either direction.
  3. User data (pasted verbatim in the runbook): `dnf install -y docker python3.12`,
     `systemctl enable --now docker`, install the compose plugin (`mkdir -p
     /usr/local/lib/docker/cli-plugins && curl -fsSL
     https://github.com/docker/compose/releases/download/v5.5.1/docker-compose-linux-$(uname
     -m) -o /usr/local/lib/docker/cli-plugins/docker-compose && chmod +x …` — AL2023's `docker`
     package ships no compose plugin; `v5.5.1` is the latest release at briefing (2026-09-11,
     `gh api repos/docker/compose/releases/latest`); the implementer re-verifies it exists),
     `systemctl disable --now sshd && systemctl mask sshd` (port 22 must be free before Cowrie
     starts; SSM Agent is preinstalled on AL2023 and needs no port), `mkdir -p
     /opt/sentinelbrief-honeypot/data/{log,lib} && chown -R 1000:1000
     /opt/sentinelbrief-honeypot/data` (uid 1000 = the image's `cowrie` user), `useradd --system
     --no-create-home --shell /sbin/nologin shipper` (task-02's unit runs as this user),
     `usermod -aG docker ssm-user`.
  4. Copy `honeypot/docker-compose.yml` + `honeypot/etc/cowrie.cfg` to `/opt/sentinelbrief-honeypot/`
     via SSM (`aws ssm start-session` + a heredoc, or S3 as a courier — never `scp`, there is no
     sshd); `docker pull cowrie/cowrie:latest`, record the digest, edit the `image:` line to the
     digest form on the box AND in the repo copy in the same sitting (drift rule).
  5. `docker compose up -d`; verify from a laptop: `ssh -p 22 root@<elastic-ip>` reaches Cowrie's
     fake banner (`SSH-2.0-OpenSSH_6.0p1 Debian-4+deb7u2` by default), a wrong password is
     accepted or rejected per Cowrie's userdb; `docker compose exec cowrie tail -n 3
     /cowrie/cowrie-git/var/log/cowrie/cowrie.json` shows `cowrie.session.connect` … `closed`
     events with `"sensor": "hp-use-01"`.
  6. What is NOT on this host: no repo checkout, no `.env`, no database URL, no LLM key, no
     AWS credentials beyond the instance role. The only secret ever placed here is the shipper's
     `INGEST_HMAC_SECRET` (task-02, `/etc/sentinelbrief-shipper.env`, mode 600).

  `honeypot/assets.yaml`: header comment gains "`hp-use-01` is the deployed honeypot (M6
  task-01); every other entry is synthetic — used by `fixtures/alerts/` and `evals/golden/`"; no
  entry changes (the tool's tests pin the 20-host shape).

## Interfaces → test table

`tests/test_honeypot_compose.py` renders `honeypot/docker-compose.yml` with `docker compose -f
<tmp copy> config --format json` from a `tmp_path` copy at `<tmp>/honeypot/docker-compose.yml`
(skips by name when `docker` is absent — the existing `test_compose_config.py` pattern; a canary
`<tmp>/.env` is written beside it for the no-secrets row) and parses `honeypot/etc/cowrie.cfg`
with `configparser` (`interpolation=None` — the `${honeypot:log_path}` reference is Cowrie's
syntax, not configparser's).

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| validates | `::test_honeypot_compose_validates_and_has_only_cowrie` | `services == {"cowrie"}` |
| port 22 → 2222 | `::test_cowrie_publishes_host_port_22_to_2222` | exactly one port; `published == "22"`, `target == 2222`; NOT bound to 127.0.0.1 (attackers must reach it — the one compose file in this repo that publishes on all interfaces; the docstring says why) |
| log + lib binds | `::test_cowrie_log_and_lib_are_bind_mounts_under_data` | a bind mount with `target == "/cowrie/cowrie-git/var/log/cowrie"` whose `source` resolves to `<tmp>/honeypot/data/log`, and one with `target == "/cowrie/cowrie-git/var/lib/cowrie"` → `<tmp>/honeypot/data/lib`; neither `read_only`; `config.get("volumes") in (None, {})` (no named volumes: the shipper must find the log at a host path) |
| config mount | `::test_cowrie_cfg_is_bind_mounted_read_only` | a bind mount targeting `/cowrie/cowrie-git/etc/cowrie.cfg` with `read_only is True` |
| no secrets | `::test_cowrie_has_no_env_file_or_environment` | `"env_file" not in cowrie`; `cowrie.get("environment") in (None, {}, [])`; rendered against a canary `.env` so a leaked `env_file` would show (the `_ENV_FILE_LEAK_CANARY` pattern) |
| restart + logging | `::test_cowrie_restarts_and_rotates_logs` | `restart == "unless-stopped"`; logging json-file `max-size 10m`, `max-file 3` |
| image pin form | `::test_cowrie_image_is_the_official_image_tag_or_digest` | `image` matches `^cowrie/cowrie(:latest|@sha256:[0-9a-f]{64})$`; the docstring names the digest pin step |
| cfg | `::test_cowrie_cfg_enables_jsonlog_and_names_the_sensor` | `[output_jsonlog] enabled == "true"`, `logfile` endswith `cowrie.json`; `[honeypot] sensor_name == "hp-use-01"` |
| assets | `::test_assets_yaml_marks_the_deployed_sensor_and_keeps_twenty` | the header comment contains `hp-use-01` and "deployed"; `len(assets) == 20`; `assets["hp-use-01"]["role"] == "ssh-honeypot"` |
| runbook | `::test_honeypot_readme_names_the_hardening_steps` | the README text contains `systemctl mask sshd`, `AmazonSSMManagedInstanceCore`, `443`, `@sha256`, and never the strings `INGEST_HMAC_SECRET=` followed by a value (regex `INGEST_HMAC_SECRET=\S+` absent) |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins the test file; the **implementer** does
Steps 3–6.

- [ ] **Step 1 (RED — test-author): write `tests/test_honeypot_compose.py`** per the table (copy
  the compose-render helper and the canary constant from `tests/test_compose_config.py`; do not
  import across test files).
- [ ] **Step 2 (RED — test-author): run to see it fail.** `uv run pytest -q
  tests/test_honeypot_compose.py` → Expected: every test fails on the missing
  `honeypot/docker-compose.yml` / `honeypot/etc/cowrie.cfg` / `honeypot/README.md` (assertion on
  `exists()` or `FileNotFoundError`), the assets test on the missing header text. Pin, commit
  `test(honeypot): cowrie compose/config/runbook pins RED (m6 task-01)`.
- [ ] **Step 3 (GREEN — implementer): `honeypot/docker-compose.yml` + `honeypot/etc/cowrie.cfg`**
  per Interfaces; `docker compose -f honeypot/docker-compose.yml config > /dev/null`.
- [ ] **Step 4 (GREEN — implementer): `honeypot/README.md`** (the six runbook steps, verbatim
  user-data block, the "what is NOT on this host" list) and `fixtures/cowrie/README.md`.
- [ ] **Step 5 (implementer): `honeypot/assets.yaml` header, `docs/deployment.md` sentences,
  README line.** Locally: `docker compose -f honeypot/docker-compose.yml up -d` (Cowrie on
  the laptop's port 22 will FAIL if a local sshd holds it — expected on a dev box; use a
  scratch override mapping `2222:2222` for the smoke, never committed), `ssh -p 2222
  root@127.0.0.1` sees the Cowrie banner, `tail` the JSON log; paste three redacted log lines
  (ids/eventids only); `down -v`.
- [ ] **Step 6 (implementer): full gates (cold) → commit** `feat(honeypot): cowrie compose,
  config, host runbook; assets name the deployed sensor (m6 task-01)` with the two trailers;
  path-scoped `git add honeypot fixtures/cowrie docs/deployment.md README.md`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_honeypot_compose.py tests/test_asset_info_tool.py     # all pass, 0 skipped
docker compose -f honeypot/docker-compose.yml config > /dev/null && echo ok           # ok
grep -c 'sshd' honeypot/README.md                                                     # >= 2 (disable + mask)
git ls-files honeypot | grep -c '\.env'                                               # 0
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90
```

## Acceptance

- `honeypot/docker-compose.yml` runs the official Cowrie image on host port 22 with its JSON log
  and artifact directory bind-mounted under `./data/` (gitignored), log rotation and a restart
  policy, and carries no secret, env file or second service; the config names the deployed sensor
  and enables the JSON log.
- The runbook makes the host SSM-only (`sshd` disabled and masked), egress-443-only, digest-pinned,
  and lists what must never be on the host; `assets.yaml` distinguishes the deployed sensor from
  the synthetic fleet.
- Every shape above is pinned by a test that fails when the file is missing or drifts.
