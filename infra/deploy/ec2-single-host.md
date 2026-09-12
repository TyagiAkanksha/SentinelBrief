# EC2 single-host deploy — owner-run walkthrough (both hosts)

**Read this top to bottom, in order, and run every fenced command exactly as written** (fill in
each `<placeholder>` yourself — a placeholder is never a real value typed by an agent, never
written into a file). Region is `us-east-1` throughout; AWS account `181040156847`. This document
brings up the app host AND the honeypot host and ends with the dashboard public at the domain and
the first real attacker session `triaged`. It links to, and never duplicates, this milestone's own
runbooks: [`honeypot/README.md`](../../honeypot/README.md) (task-01), the shipper's
[`honeypot/shipper/README.md`](../../honeypot/shipper/README.md) (task-02),
[`prod/README.md`](prod/README.md) + [`env-checklist.md`](env-checklist.md) (task-03), and
[`database.md`](database.md) (task-04). The final check is [`VERIFY.md`](VERIFY.md).

## 0. Prerequisites & decisions

- **Domain** (PRD §13 — CONFIRMED by the owner 2026-09-11): `sentinelbrief.tyagiakanksha.com` /
  `api.sentinelbrief.tyagiakanksha.com`. If a different domain is ever used, `sed` over every hit
  listed in task-03's implementer report before starting (the Caddyfile, the compose file's
  `CORS_ORIGINS`, `push_ecr.sh`'s `API_PUBLIC_URL` default, `env-checklist.md`).
- **AWS account** `181040156847` (AdvisorDesk's account — the honeypot goes in a SEPARATE VPC of
  the SAME account per PRD §10.4, not a separate account). Confirm the CLI is wired to it:

  ```sh
  aws sts get-caller-identity
  ```

  Expected: `"Account": "181040156847"`.
- **VPCs.** The app host joins the **default VPC** `vpc-00735b325754614bd` (172.31.0.0/16),
  subnet `subnet-025c3ac4df23404f5` (us-east-1b) — the same VPC that already hosts the
  AdvisorDesk instance. The honeypot gets its **own** VPC, `10.99.0.0/24` (step 9) — PRD §10.4's
  "share nothing with the app host."
- **Instance sizes.** App: `t3.small`, x86_64. Honeypot: `t4g.nano`, arm64 (the honeypot compose
  has no arch-specific image and Cowrie publishes multi-arch, so the cheaper Graviton instance is
  free to use).
- **Local tools:** `aws` CLI v2, Docker, `git`. **Run every `aws` command below from the
  repository root** — every `file://infra/deploy/…` and `file://honeypot/user-data.sh` path is
  relative to it (task-05 fix-1, review M9).
- **Generate the four secrets locally, in the shell only** — never write one to a file, never
  paste one into a chat, never let one touch this repo:

  ```sh
  python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # run once each for INGEST_HMAC_SECRET, ADMIN_TOKEN, POSTGRES_PASSWORD
  ```

  Plus the LLM API key from the provider account, and — optionally — an AbuseIPDB key and a
  MaxMind license key. An absent optional key is fine: `fetch-secrets.sh` (step 8) writes it
  empty and the matching tool answers `{"unavailable": true}`.

## 1. IAM

App host role, with the least-privilege inline policy that reads only `/sentinelbrief/*` and
writes only the backup bucket ([`iam/app-host-trust.json`](iam/app-host-trust.json),
[`iam/app-host-inline.json`](iam/app-host-inline.json) — no `"*"` Action, no `"*"` Resource):

```sh
aws iam create-role --role-name sentinelbrief-app-host \
  --assume-role-policy-document file://infra/deploy/iam/app-host-trust.json
aws iam attach-role-policy --role-name sentinelbrief-app-host \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam attach-role-policy --role-name sentinelbrief-app-host \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
aws iam put-role-policy --role-name sentinelbrief-app-host \
  --policy-name sentinelbrief-app-host-inline \
  --policy-document file://infra/deploy/iam/app-host-inline.json
aws iam create-instance-profile --instance-profile-name sentinelbrief-app-host
aws iam add-role-to-instance-profile --instance-profile-name sentinelbrief-app-host \
  --role-name sentinelbrief-app-host
```

Honeypot host role — `AmazonSSMManagedInstanceCore` **only**, no inline policy, so it cannot read
a parameter or pull an image ([`iam/honeypot-host-trust.json`](iam/honeypot-host-trust.json) is the
same trust document, kept as its own file so each role's files sit together):

```sh
aws iam create-role --role-name sentinelbrief-honeypot-host \
  --assume-role-policy-document file://infra/deploy/iam/honeypot-host-trust.json
aws iam attach-role-policy --role-name sentinelbrief-honeypot-host \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam create-instance-profile --instance-profile-name sentinelbrief-honeypot-host
aws iam add-role-to-instance-profile --instance-profile-name sentinelbrief-honeypot-host \
  --role-name sentinelbrief-honeypot-host
```

## 2. S3 backup bucket

```sh
aws s3api create-bucket --bucket sentinelbrief-backups-181040156847 --region us-east-1
aws s3api put-public-access-block --bucket sentinelbrief-backups-181040156847 \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-lifecycle-configuration --bucket sentinelbrief-backups-181040156847 \
  --lifecycle-configuration file://infra/deploy/s3-lifecycle.json
aws s3api put-bucket-encryption --bucket sentinelbrief-backups-181040156847 \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

## 3. SSM parameters

**Keep every value out of shell history.** Either `export HISTCONTROL=ignorespace` and prefix
each line below with a leading space, or run `history -c` right after this step.

```sh
aws ssm put-parameter --type SecureString --name /sentinelbrief/INGEST_HMAC_SECRET --value '<value>'
aws ssm put-parameter --type SecureString --name /sentinelbrief/ADMIN_TOKEN --value '<value>'
aws ssm put-parameter --type SecureString --name /sentinelbrief/POSTGRES_PASSWORD --value '<value>'
aws ssm put-parameter --type SecureString --name /sentinelbrief/DATABASE_URL --value 'postgresql://sentinel:<POSTGRES_PASSWORD>@postgres:5432/sentinelbrief'
aws ssm put-parameter --type SecureString --name /sentinelbrief/LLM_API_KEY --value '<value>'
aws ssm put-parameter --type SecureString --name /sentinelbrief/MAXMIND_LICENSE_KEY --value '<value>'   # optional — skip this line if unkeyed
aws ssm put-parameter --type SecureString --name /sentinelbrief/ABUSEIPDB_API_KEY --value '<value>'      # optional — skip this line if unkeyed
history -c
```

`DATABASE_URL` embeds the **same** password as the `POSTGRES_PASSWORD` parameter above — the
literal `<POSTGRES_PASSWORD>` above is a placeholder for you to substitute, not a real value ever
written to disk. See [`env-checklist.md`](env-checklist.md) for what reads each of these.

None of the lines above pass `--overwrite`, deliberately — a fat-fingered replay can never
silently clobber a good value. If you typo'd a value and need to correct it, re-run that one line
with `--overwrite` added.

## 4. App host

Security group — inbound 80/443 only, no port 22 (management is SSM Session Manager only):

```sh
aws ec2 create-security-group --group-name sentinelbrief-app \
  --description "SentinelBrief app host" --vpc-id vpc-00735b325754614bd
# note the returned GroupId as <app-sg-id>
aws ec2 authorize-security-group-ingress --group-id <app-sg-id> --protocol tcp --port 80 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id <app-sg-id> --ip-permissions 'IpProtocol=tcp,FromPort=80,ToPort=80,Ipv6Ranges=[{CidrIpv6=::/0}]'
aws ec2 authorize-security-group-ingress --group-id <app-sg-id> --protocol tcp --port 443 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id <app-sg-id> --ip-permissions 'IpProtocol=tcp,FromPort=443,ToPort=443,Ipv6Ranges=[{CidrIpv6=::/0}]'
```

There is no IPv6 CIDR shorthand flag on the `secgroupsimplify` form of this command (task-05
fix-1, review I1 — the AWS CLI rejects a naturally-guessed flag name here with `Unknown options`);
the two IPv6 rules above use the long-form `--ip-permissions` instead, verified with `--dry-run`
against a real security group in this account: `DryRunOperation: Request would have succeeded`
for both.

Confirm the AL2023 x86_64 AMI alias resolves (read-only — safe to run any time):

```sh
aws ssm get-parameter --region us-east-1 \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
```

Launch the instance from [`user-data-app.sh`](user-data-app.sh) — the source of truth for the app
host's boot script (task-05 fix-1) — which installs Docker + the compose plugin,
`systemctl disable --now sshd && systemctl mask sshd`, creates `/var/backups/sentinelbrief` and
`/opt/sentinelbrief/geoip` (`chown`'d to uid:gid `1001:1001`, the api image's `appuser` — review
I4, so the geoip one-off in step 8 can write into it), and bounds the journal
(`SystemMaxUse=500M`) **persistent in place** (`Storage=persistent` + `mkdir -p /var/log/journal`
— review M6, no relaunch needed). There is deliberately no `usermod -aG docker ssm-user` line
(review I3): AL2023's SSM Agent creates `ssm-user` lazily at the first session, so a `usermod` on
a not-yet-existing user would abort the script under `set -euo pipefail` — every `docker compose`
command below already runs inside an SSM session after `sudo -i`, so no group membership is
needed:

```sh
aws ec2 run-instances \
  --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --instance-type t3.small \
  --iam-instance-profile Name=sentinelbrief-app-host \
  --subnet-id subnet-025c3ac4df23404f5 \
  --security-group-ids <app-sg-id> \
  --user-data file://infra/deploy/user-data-app.sh \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=sentinelbrief-app}]'
# note the returned InstanceId as <app-instance-id>
aws ec2 allocate-address --domain vpc
# note the returned AllocationId as <app-allocation-id> and PublicIp as <app-eip>
aws ec2 associate-address --instance-id <app-instance-id> --allocation-id <app-allocation-id>
```

## 5. DNS

At Cloudflare: create two A records, `sentinelbrief` and `api.sentinelbrief`, both pointing at
`<app-eip>`, **DNS-only (grey cloud — never Proxied)** — Cloudflare's proxy buffers SSE and adds
a redundant TLS hop. Re-check each record's proxy icon *after saving* — the form is known to
silently reflow to Proxied. Then poll until both resolve:

```sh
dig +short sentinelbrief.tyagiakanksha.com
dig +short api.sentinelbrief.tyagiakanksha.com
```

Expected: both print `<app-eip>`. Do not continue to step 8 until they do — Caddy cannot obtain a
certificate before DNS resolves.

## 6. Images

`push_ecr.sh` refuses a dirty working tree — commit first (the SHA tag must describe the image):

```sh
git status --porcelain   # must print nothing
AWS_ACCOUNT_ID=181040156847 ./infra/deploy/push_ecr.sh
```

It prints two `:<sha>` image URIs. Copy them into **both** copies referenced by
[`prod/docker-compose.yml`](prod/docker-compose.yml) — all three `image:` lines (`web`, `api`,
`worker` — `worker` reuses the `api` image) — replacing the `0000000` placeholders with the same
account id and the same SHA everywhere. Commit that edit **on the deploy branch before copying
files to the box** (prod copies equal the box byte-for-byte after every apply).

## 7. Files onto the box

```sh
aws ssm start-session --target <app-instance-id>
sudo -i
cd /opt/sentinelbrief
```

`sudo -i` lands in `/root`, not `/opt/sentinelbrief` — every relative `./…` and `docker compose`
command in steps 7, 8, and 11 below assumes this `cd` was run (task-05 fix-1, review M3).

In the session, for each file: create it with the right owner/mode first (`install`, not `cp` +
`chmod` — `install` sets both in one step, which matters because the SSM session lands as
`ssm-user`, not root, before `sudo -i`), then fill it with a heredoc pasted from the matching
committed copy under [`prod/`](prod/):

```sh
install -o root -g root -m 644 /dev/null /opt/sentinelbrief/docker-compose.yml
cat > /opt/sentinelbrief/docker-compose.yml <<'EOF'
<paste infra/deploy/prod/docker-compose.yml here — the image: lines already carry step 6's SHA>
EOF
```

```sh
install -o root -g root -m 644 /dev/null /opt/sentinelbrief/Caddyfile
cat > /opt/sentinelbrief/Caddyfile <<'EOF'
<paste infra/deploy/prod/Caddyfile here>
EOF
```

```sh
install -o root -g root -m 700 /dev/null /opt/sentinelbrief/fetch-secrets.sh
cat > /opt/sentinelbrief/fetch-secrets.sh <<'EOF'
<paste infra/deploy/prod/fetch-secrets.sh here>
EOF
```

```sh
install -o root -g root -m 700 /dev/null /opt/sentinelbrief/backup.sh
cat > /opt/sentinelbrief/backup.sh <<'EOF'
<paste infra/deploy/prod/backup.sh here>
EOF
```

```sh
install -o root -g root -m 600 /dev/null /opt/sentinelbrief/backup.env
cat > /opt/sentinelbrief/backup.env <<'EOF'
<paste infra/deploy/prod/backup.env here — bucket name already sentinelbrief-backups-181040156847>
EOF
```

```sh
install -o root -g root -m 700 /dev/null /opt/sentinelbrief/restore-rehearsal.sh
cat > /opt/sentinelbrief/restore-rehearsal.sh <<'EOF'
<paste infra/deploy/prod/restore-rehearsal.sh here>
EOF
```

```sh
install -o root -g root -m 644 /dev/null /opt/sentinelbrief/sentinelbrief-backup.service
cat > /opt/sentinelbrief/sentinelbrief-backup.service <<'EOF'
<paste infra/deploy/prod/sentinelbrief-backup.service here>
EOF
```

```sh
install -o root -g root -m 644 /dev/null /opt/sentinelbrief/sentinelbrief-backup.timer
cat > /opt/sentinelbrief/sentinelbrief-backup.timer <<'EOF'
<paste infra/deploy/prod/sentinelbrief-backup.timer here>
EOF
```

## 8. Secrets, geoip, migrate, up

Still in the SSM session:

```sh
cd /opt/sentinelbrief
./fetch-secrets.sh
```

Expected (exact wording depends on which optional parameters were created in step 3 — see the
script's own header comment): `OK wrote 5 vars to /opt/sentinelbrief/.env (4 fetched required, 1
fetched optional, 0 written empty) and 1 var to /opt/sentinelbrief/.env.postgres` (or `0 fetched
optional, 1 written empty` if `ABUSEIPDB_API_KEY` was skipped in step 3).

```sh
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 181040156847.dkr.ecr.us-east-1.amazonaws.com
docker compose pull
```

The geoip one-off (skip if no MaxMind key — the tool then answers `{"unavailable": true}`; exact
command in [`prod/README.md`](prod/README.md#geoip-one-off)):

```sh
MAXMIND_LICENSE_KEY="$(aws ssm get-parameter --region us-east-1 --name \
  /sentinelbrief/MAXMIND_LICENSE_KEY --with-decryption --query Parameter.Value --output text)" \
  docker compose run --rm -e MAXMIND_LICENSE_KEY -v /opt/sentinelbrief/geoip:/app/infra/geoip api \
  uv run python scripts/fetch_geoip.py --out-dir infra/geoip
```

Migrate **before** `up -d` (never `exec` into the still-running old container —
[`prod/README.md`](prod/README.md#migrations) explains why):

```sh
docker compose run --rm api uv run alembic upgrade head
docker compose up -d
docker compose ps
```

Expect all six services `running`/`healthy`. **Note** (task-03 review M8): `caddy` starts on
`service_started`, not `service_healthy`, so the web vhost may answer `502` for a few seconds
until `web` finishes listening — this is normal on first boot, not a fault.

## 9. Honeypot host

Its own VPC, isolated from the app host (PRD §10.4):

```sh
aws ec2 create-vpc --cidr-block 10.99.0.0/24 \
  --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=sentinelbrief-honeypot}]'
# note the returned VpcId as <hp-vpc-id>
aws ec2 create-subnet --vpc-id <hp-vpc-id> --cidr-block 10.99.0.0/24 --availability-zone us-east-1b \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=sentinelbrief-honeypot}]'
# note the returned SubnetId as <hp-subnet-id>
aws ec2 modify-subnet-attribute --subnet-id <hp-subnet-id> --map-public-ip-on-launch
aws ec2 create-internet-gateway \
  --tag-specifications 'ResourceType=internet-gateway,Tags=[{Key=Name,Value=sentinelbrief-honeypot}]'
# note the returned InternetGatewayId as <hp-igw-id>
aws ec2 attach-internet-gateway --vpc-id <hp-vpc-id> --internet-gateway-id <hp-igw-id>
aws ec2 describe-route-tables --filters Name=vpc-id,Values=<hp-vpc-id>
# note the default route table's RouteTableId as <hp-rtb-id>
aws ec2 create-route --route-table-id <hp-rtb-id> --destination-cidr-block 0.0.0.0/0 --gateway-id <hp-igw-id>
```

Security group: inbound 22 from everywhere (the point of a honeypot); outbound 443 only —
**revoke the default all-traffic egress rule first**, then add the narrow one:

```sh
aws ec2 create-security-group --group-name sentinelbrief-honeypot \
  --description "SentinelBrief honeypot host" --vpc-id <hp-vpc-id>
# note the returned GroupId as <hp-sg-id>
aws ec2 authorize-security-group-ingress --group-id <hp-sg-id> --protocol tcp --port 22 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id <hp-sg-id> --ip-permissions 'IpProtocol=tcp,FromPort=22,ToPort=22,Ipv6Ranges=[{CidrIpv6=::/0}]'
aws ec2 revoke-security-group-egress --group-id <hp-sg-id> --protocol -1 --port -1 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-egress --group-id <hp-sg-id> --protocol tcp --port 443 --cidr 0.0.0.0/0
```

(`--ip-permissions` again — no IPv6 CIDR shorthand exists for this command, review I1 — verified
with `--dry-run`: `DryRunOperation: Request would have succeeded`.)

Launch from [`honeypot/user-data.sh`](../../honeypot/user-data.sh) (arm64 — Cowrie is multi-arch):

```sh
aws ec2 run-instances \
  --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
  --instance-type t4g.nano \
  --iam-instance-profile Name=sentinelbrief-honeypot-host \
  --subnet-id <hp-subnet-id> \
  --security-group-ids <hp-sg-id> \
  --user-data file://honeypot/user-data.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=sentinelbrief-honeypot}]'
# note the returned InstanceId as <hp-instance-id>
aws ec2 allocate-address --domain vpc
# note the returned AllocationId as <hp-allocation-id> and PublicIp as <hp-eip>
aws ec2 associate-address --instance-id <hp-instance-id> --allocation-id <hp-allocation-id>
```

Open the SSM session on the honeypot host now — **everything from here to the end of step 9**
(and `VERIFY.md` checks 8–9's honeypot half) runs as root in this session (task-05 fix-2, review
N2), dropped only where a command must specifically run as the unprivileged `shipper` user (the
read-path proof below):

```sh
aws ssm start-session --target <hp-instance-id>
sudo -i
```

Now follow [`honeypot/README.md`](../../honeypot/README.md) steps 4–6 (copy the compose files —
each a single text file, so a plain heredoc through the SSM session is enough — pin the Cowrie
image digest, start Cowrie, verify the fake banner). Then install the shipper together with
Cowrie, from
[`honeypot/shipper/README.md`](../../honeypot/shipper/README.md), so its first read of
`cowrie.json` is small (task-02 review notes).

That README's `sudo cp -r honeypot/shipper /opt/sentinelbrief-shipper/src` step assumes a local
repo checkout, which this host never has (`honeypot/README.md`'s "What is NOT on this host"), and
an S3 courier is impossible too — the honeypot role has no inline policy (step 1). The one
mechanism that works on a host with neither: a base64'd tarball, pasted through the SSM session
(task-05 fix-1, review M5), with a `sha256sum` check on both ends so a truncated paste is caught
before extraction, not after (task-05 fix-2, review N1). On the laptop, from the repo root:

```sh
tar czf /tmp/shipper.tgz -C honeypot shipper
sha256sum /tmp/shipper.tgz
base64 /tmp/shipper.tgz > /tmp/shipper.b64
```

**Do not add `-w0`** to `base64` — its unwrapped output is one 40,000+ character line (today's
tree: 46,548 bytes on one line with no newline), and the heredoc below is read from the SSM
session's tty in canonical mode, where the ~4096-byte line-discipline buffer silently discards
input past that length; the paste arrives truncated and `tar xzf` fails. The default 76-column
wrapping (~613 short lines for today's tree) has every line far under that limit, and `base64 -d`
ignores the added newlines.

Then, in the SSM session on the honeypot host:

```sh
base64 -d > /tmp/shipper.tgz <<'EOF'
<paste the contents of /tmp/shipper.b64 here>
EOF
sha256sum /tmp/shipper.tgz
```

**This must match the laptop's `sha256sum` above before you continue** — a mismatch means the
paste was truncated or corrupted; re-copy `/tmp/shipper.b64`'s contents and try again rather than
extracting a partial archive:

```sh
mkdir -p /opt/sentinelbrief-shipper/src
tar xzf /tmp/shipper.tgz -C /opt/sentinelbrief-shipper/src --strip-components=1
```

Continue from `honeypot/shipper/README.md`'s venv-build step onward (its own `cp -r` step is
already done by the tarball above). **Before**
`systemctl enable --now sentinelbrief-shipper`, prove the unprivileged read path works:

```sh
test -d /opt/sentinelbrief-honeypot/data/log
sudo -u shipper head -c 1 /opt/sentinelbrief-honeypot/data/log/cowrie.json
```

Then start the unit and check it **after 60 s, not immediately** — a `RestartSec=5` crash loop is
only visible after a few restarts have had time to happen:

```sh
systemctl enable --now sentinelbrief-shipper
sleep 60
systemctl is-active sentinelbrief-shipper
```

## 10. First real session

From a laptop, not this box:

```sh
ssh -p 22 root@<hp-eip>   # almost any password — Cowrie's stock userdb rejects `root` with `root` and `123456`
uname -a
exit
```

Within ~2 s, on the honeypot host:

```sh
journalctl -u sentinelbrief-shipper -n 3   # "delivered ... status=202"
```

On the app host:

```sh
docker compose logs worker --since 5m | grep 'triage'
curl -s 'https://api.sentinelbrief.tyagiakanksha.com/api/v1/alerts?page_size=1'
```

The alert should show `"status": "triaged"`.

## 11. Backups

On the app host:

```sh
cd /opt/sentinelbrief
cp sentinelbrief-backup.service sentinelbrief-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sentinelbrief-backup.timer
```

Verify the timer actually scheduled a run — AL2023's systemd (≥ 252) accepts the `UTC` suffix in
`OnCalendar`, but confirm rather than assume:

```sh
systemctl list-timers sentinelbrief-backup.timer   # must show a NEXT run, not "n/a"
```

Run one now, before the 48 h soak starts:

```sh
systemctl start sentinelbrief-backup.service && journalctl -u sentinelbrief-backup -n 3   # "backup ok key=..."
aws s3 ls s3://sentinelbrief-backups-181040156847/postgres/
./restore-rehearsal.sh   # "restore ok alerts=... verdicts=... tool_calls=... alembic=..." — paste into database.md's block
```

On the honeypot host (the same root SSM session opened in step 9 — re-run its
`start-session`/`sudo -i` if it dropped), prove the journal is actually persistent —
`SystemMaxUse=200M` is inert if the journal is volatile (`/run`, wiped on reboot) rather than
persistent (`/var/log/journal`). `honeypot/user-data.sh` already sets `Storage=persistent` and
creates `/var/log/journal`, so this should already hold; confirm rather than assume:

```sh
journalctl --disk-usage
grep -E '^(Storage|SystemMaxUse)=' /etc/systemd/journald.conf
```

If `Storage=` is missing or not `persistent` anyway, fix it **in place** — no relaunch needed
(task-05 fix-1, review M6):

```sh
mkdir -p /var/log/journal
printf 'SystemMaxUse=200M\nStorage=persistent\n' >> /etc/systemd/journald.conf
systemctl restart systemd-journald
```

## 12. Verify

Run [`VERIFY.md`](VERIFY.md) top to bottom; paste every real result into its blocks. Then commit
`VERIFY.md`, `database.md`'s restore-rehearsal block, `docs/deployment.md`'s resource table, and
any prod-copy drift discovered along the way, in one sitting:

```sh
git add infra/deploy/VERIFY.md infra/deploy/database.md docs/deployment.md infra/deploy/prod
git commit -m "chore(deploy): m6 first deploy — <date>"
```

## Operational notes

- **Redeploy cycle:** [`prod/README.md`](prod/README.md) — edit here, review, apply via SSM,
  re-run `VERIFY.md`, commit drift back.
- **Rotating a secret:** update the SSM parameter from the owner's terminal, then on the box
  `./fetch-secrets.sh && docker compose up -d api worker` (add `postgres` too, plus
  `ALTER USER sentinel WITH PASSWORD '...'` inside the database, only if `POSTGRES_PASSWORD`
  itself changed — [`prod/README.md`](prod/README.md#secrets-rotation)).
- **Changing the API's public origin** means rebuilding and repushing the `web` image
  (`NEXT_PUBLIC_API_URL` is baked in at build time, not read at container start) — restarting the
  running container changes nothing.
- **The honeypot host is disposable.** Assume it will be fully compromised — that is its job.
  Terminating it and re-running step 9 from a fresh instance is the recovery procedure, not an
  incident.
