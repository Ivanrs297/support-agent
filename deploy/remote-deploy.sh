#!/usr/bin/env bash
#
# Runs on the EC2 host, invoked by SSM from the Deploy workflow. Takes the commit
# SHA to deploy, which is also the image tag.
#
# The host has already checked this repository out at that SHA, so this script is
# the version of itself that belongs to the commit being deployed.
#
#   bash deploy/remote-deploy.sh <sha>
#
set -euo pipefail

SHA="${1:?usage: remote-deploy.sh <sha>}"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/support-agent/deploy}"  # overridable so this can be exercised off the host
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"

cd "$COMPOSE_DIR"

if [ ! -f .env ]; then
  echo "No .env in $COMPOSE_DIR. Run deploy/host-setup.sh first." >&2
  exit 1
fi

# Remember what is running now. This is the only thing that makes the rollback
# below possible, and it has to be read before the file is rewritten.
PREVIOUS=$(grep -E '^IMAGE_TAG=' .env | cut -d= -f2- || echo latest)
echo "current: $PREVIOUS"
echo "target:  $SHA"

set_tag() {
  # Not `sed -i`: GNU takes no argument for it and BSD requires one, so the
  # in-place form only runs on the host and never on the laptop of whoever is
  # changing this script. Writing through a temp file and back with `cat`
  # works on both and keeps the 600 permissions on .env.
  local tmp
  tmp=$(mktemp)
  sed "s|^IMAGE_TAG=.*|IMAGE_TAG=$1|" .env > "$tmp"
  cat "$tmp" > .env
  rm -f "$tmp"
}

wait_for_health() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT)) status=
  while [ $SECONDS -lt $deadline ]; do
    status=$(docker inspect -f '{{.State.Health.Status}}' api 2>/dev/null || echo missing)
    case "$status" in
      healthy) return 0 ;;
      unhealthy) echo "container reported unhealthy" >&2; return 1 ;;
    esac
    sleep 3
  done
  echo "container did not become healthy within ${HEALTH_TIMEOUT}s (last status: $status)" >&2
  return 1
}

set_tag "$SHA"

if ! docker compose pull --quiet api; then
  echo "could not pull $SHA. Is the ghcr package public?" >&2
  set_tag "$PREVIOUS"
  exit 1
fi

docker compose up -d --remove-orphans

# The Dockerfile's HEALTHCHECK is the source of truth here. Asking Docker
# whether the container is healthy tests the same thing the restart policy
# does, from inside the network the app actually runs in.
if ! wait_for_health; then
  echo "rolling back to $PREVIOUS" >&2
  set_tag "$PREVIOUS"
  docker compose up -d --remove-orphans
  wait_for_health || echo "the previous image is not healthy either" >&2
  exit 1
fi

echo "deployed $SHA"
docker compose ps --format '{{.Service}}\t{{.Image}}\t{{.Status}}'
