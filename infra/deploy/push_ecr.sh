#!/usr/bin/env bash
set -euo pipefail

# infra/deploy/push_ecr.sh — build, tag, and push SentinelBrief's two container images (api, web)
# to Amazon ECR (PRD §11, m6 task-03). `worker` is not a separate image — the production compose
# file (infra/deploy/prod/docker-compose.yml) runs it from the same `sentinelbrief/api` image with
# a different `command:`.
#
# WHAT THIS SCRIPT DOES: builds each image locally with Docker, tags it TWICE (`latest` and the
# current git short SHA, e.g. `a1b2c3d`), creates the ECR repository the first time it's needed,
# and pushes both tags. That's all — it never touches the EC2 host, DNS, or any runtime
# environment variable. See infra/deploy/ec2-single-host.md for the next step (pulling the pushed
# images onto the deployed host).
#
# WHY TWO TAGS: `latest` is easy to reference while clicking through setup; the git-sha tag is
# what the deployed compose file (infra/deploy/prod/docker-compose.yml) is actually pinned to, so
# a later `git checkout` + rebuild can never silently change what a running service serves out
# from under you.
#
# Beginner notes:
#   - This script BUILDS on the machine it runs on (your laptop, or a CI runner) and then PUSHES
#     the finished image — nothing is built inside AWS. Docker must be running locally.
#   - You need the AWS CLI v2 already configured with credentials (`aws configure`, or exported
#     AWS_* env vars / an SSO profile) that can create/describe ECR repositories and push images
#     in the target account.
#   - Re-running this script is safe: it never fails just because a repo or tag already exists
#     (see "idempotent" notes inline below).
#
# Usage:
#   AWS_ACCOUNT_ID=181040156847 ./infra/deploy/push_ecr.sh
#   AWS_ACCOUNT_ID=181040156847 AWS_REGION=us-east-1 ./infra/deploy/push_ecr.sh
#
# Env vars:
#   AWS_ACCOUNT_ID   REQUIRED — your 12-digit AWS account id. The script fails fast with a clear
#                    message if this is unset or not exactly 12 digits.
#   AWS_REGION       optional — default "us-east-1" (the region pinned by PRD §11/§13 for this
#                    deployment).
#   API_PUBLIC_URL   optional — the production API origin baked into the web image's build args
#                    (NEXT_PUBLIC_API_URL — see infra/Dockerfile.web's own comments on why the web
#                    image needs this as a BUILD-time arg, not just a runtime env var). Default:
#                    "https://api.sentinelbrief.tyagiakanksha.com" — SentinelBrief's pinned
#                    production API domain (PRD §13). Override only if you're building images for
#                    a different environment (e.g. a staging domain).
#   BUILD_PLATFORM   optional — default "linux/amd64" (the deployed host is x86_64).

# ---------------------------------------------------------------------------
# Phase 0: read + validate inputs
# ---------------------------------------------------------------------------
if [[ -z "${AWS_ACCOUNT_ID:-}" ]]; then
  echo "ERROR: AWS_ACCOUNT_ID is required (your 12-digit AWS account id)." >&2
  echo "  Example: AWS_ACCOUNT_ID=181040156847 $0" >&2
  exit 1
fi
if [[ ! "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
  echo "ERROR: AWS_ACCOUNT_ID must be exactly 12 digits (got: $AWS_ACCOUNT_ID)." >&2
  exit 1
fi

AWS_REGION="${AWS_REGION:-us-east-1}"
API_PUBLIC_URL="${API_PUBLIC_URL:-https://api.sentinelbrief.tyagiakanksha.com}"

# Resolve the repo root from this script's own location (infra/deploy/ -> infra/ -> repo root)
# rather than the caller's current directory, so this script works no matter where you run it
# from. This matters because both Dockerfiles must be built with the REPO ROOT as the Docker
# build context: infra/Dockerfile.api's and infra/Dockerfile.web's COPY lines read top-level
# directories (api/, worker/, core/, web/, ...) — neither resolves correctly from infra/ itself.
# infra/docker-compose.yml builds both the same way (`context: ..`, `dockerfile: infra/Dockerfile.*`)
# — this script mirrors that exactly.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Refuse a dirty working tree (m6 task-03 fix-1, M5): the whole point of the git-SHA tag is that it
# describes exactly what the image contains: `docs/deployment.md`'s "a running service can never
# silently change under a rebuild" promise doesn't hold if HEAD has uncommitted changes the image
# was actually built from. Commit (or stash) first, then push.
if [[ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]]; then
  echo "ERROR: working tree is dirty — commit first (the SHA tag must describe the image)." >&2
  exit 1
fi

echo "== SentinelBrief: build + push images to ECR =="
echo "  account:        $AWS_ACCOUNT_ID"
echo "  region:         $AWS_REGION"
echo "  registry:       $ECR_REGISTRY"
echo "  git sha tag:    $GIT_SHA"
echo "  repo root:      $REPO_ROOT"
echo "  api url baked into the web build: $API_PUBLIC_URL"
echo

# ---------------------------------------------------------------------------
# Phase 1: ECR login
# ---------------------------------------------------------------------------
# One login is enough for both repos below — they live in the same account + region, hence the
# same registry hostname.
echo "-- Logging in to ECR ($ECR_REGISTRY)..."
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY"

# ---------------------------------------------------------------------------
# Phase 2: ensure each ECR repository exists (idempotent)
# ---------------------------------------------------------------------------
# `describe-repositories` first, `create-repository` only on a miss — running this script again
# against repos that already exist is a no-op here, never an error.
ensure_repo() {
  local repo_name="$1"
  if aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$repo_name" \
    >/dev/null 2>&1; then
    echo "-- ECR repo already exists: $repo_name"
  else
    echo "-- Creating ECR repo: $repo_name"
    aws ecr create-repository --region "$AWS_REGION" --repository-name "$repo_name" >/dev/null
  fi
}

ensure_repo "sentinelbrief/api"
ensure_repo "sentinelbrief/web"

# ---------------------------------------------------------------------------
# Phase 3: build, tag, push each image
# ---------------------------------------------------------------------------
# build_tag_push <ecr repo name> <dockerfile, relative to repo root> [extra `docker build` args...]
#
# Builds ONCE, tags the same build TWICE (`latest` + the git sha) so pushing both never
# re-builds — `docker build -t a -t b` attaches both tags to one image id.
build_tag_push() {
  local repo_name="$1"
  local dockerfile="$2"
  shift 2
  local image_uri="${ECR_REGISTRY}/${repo_name}"

  echo
  echo "-- Building $repo_name from $dockerfile (context: $REPO_ROOT)"
  # --platform pinned: the deployed host is x86_64, so an arm64 build machine (e.g. Apple
  # silicon) would otherwise push an unrunnable image. Overridable via BUILD_PLATFORM for a
  # future arm64 host.
  docker build \
    --platform "${BUILD_PLATFORM:-linux/amd64}" \
    -f "$REPO_ROOT/$dockerfile" \
    -t "${image_uri}:latest" \
    -t "${image_uri}:${GIT_SHA}" \
    "$@" \
    "$REPO_ROOT"

  echo "-- Pushing ${image_uri}:latest"
  docker push "${image_uri}:latest"
  echo "-- Pushing ${image_uri}:${GIT_SHA}"
  docker push "${image_uri}:${GIT_SHA}"
}

# api: no build args. infra/Dockerfile.api's own top-of-file comment: runtime config is env-only
# (DATABASE_URL, LLM_API_KEY, ...) — nothing is baked into this image at build time. `worker`
# reuses this same image in the production compose file, with a different `command:`.
build_tag_push "sentinelbrief/api" "infra/Dockerfile.api"

# web: NEXT_PUBLIC_API_URL is a BUILD-time arg — infra/Dockerfile.web's own top comment: Next.js
# inlines NEXT_PUBLIC_* vars into the JS bundle at `next build` time, so this is baked into the
# image, not read at container start. ONE IMAGE PER ENVIRONMENT: if the API's public origin ever
# changes, this image must be rebuilt and repushed — restarting the running container changes
# nothing. API_URL is also passed at build time, pointing at the compose-network service name
# (`http://api:8000`) that `next build`'s own server-side rendering step resolves against; the
# deployed web container ALSO needs API_URL set as a plain runtime environment variable (the
# production compose file sets it) for the same code's per-request calls after the container
# starts — that's a separate step, not this script's job.
build_tag_push "sentinelbrief/web" "infra/Dockerfile.web" \
  --build-arg "NEXT_PUBLIC_API_URL=${API_PUBLIC_URL}" \
  --build-arg "API_URL=http://api:8000"

echo
echo "== Done. =="
echo "Pushed tags 'latest' and '${GIT_SHA}' to both repos:"
echo "  ${ECR_REGISTRY}/sentinelbrief/api:${GIT_SHA}"
echo "  ${ECR_REGISTRY}/sentinelbrief/web:${GIT_SHA}"
echo
echo "Next: infra/deploy/ec2-single-host.md (pull + run all six services on the EC2 host)."
