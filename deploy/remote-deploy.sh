#!/usr/bin/env bash
#
# STEP 19.2 — see README §19.
#
# Runs on the EC2 host, invoked by SSM from the Deploy workflow. Takes the
# commit SHA to deploy, which is also the image tag.
#
# The host has already checked this repository out at that SHA, so this script
# is the version of itself that belongs to the commit being deployed. Read that
# sentence twice: a bug you fix here does not fix the deploy that is running.
#
#   bash deploy/remote-deploy.sh <sha>
#
# The shape:
#
#   1. Read the CURRENT IMAGE_TAG out of .env before you overwrite it. That
#      value is the only thing that makes a rollback possible, and it is gone
#      the moment you write the new one.
#
#   2. Write the new tag. Not with `sed -i`: GNU sed takes no argument for that
#      flag and BSD sed requires one, so the in-place form runs on the host and
#      never on the laptop of whoever is changing this script. A temp file and
#      `cat` back over the original works on both and keeps the 600 permissions.
#
#   3. `docker compose pull`. A failure here is almost always a private ghcr
#      package. Restore the previous tag and exit — do not proceed to `up`.
#
#   4. `docker compose up -d --remove-orphans`. Not `restart`: restart reuses
#      the existing container, and environment is resolved at container
#      creation, so a changed .env has no effect. This costs people an hour
#      each time they meet it.
#
#   5. Wait for health, and this is where the care goes. Ask compose which
#      container belongs to this project — `docker compose ps -q --status
#      running api` — rather than inspecting a container called "api". Names are
#      global in Docker, so you may be inspecting a container from an earlier
#      lecture, or one with no healthcheck at all, which looks identical to a
#      deploy that never came up. Restrict to running containers and take the
#      last line: during a recreate both containers exist for a moment, and two
#      IDs in one argument makes `docker inspect` fail.
#
#   6. On failure: restore the previous tag, bring it back up, and exit
#      non-zero. Say so on stderr. A rollback that happens silently is
#      indistinguishable from a deploy that worked.
#
# Make COMPOSE_DIR and HEALTH_TIMEOUT overridable. You want to exercise all
# three paths — success, unpullable image, unhealthy container — against a local
# registry before trusting this in production, and you cannot do that if the
# path is hardcoded.
#
set -euo pipefail

SHA="${1:?usage: remote-deploy.sh <sha>}"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/support-agent/deploy}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"

# TODO
