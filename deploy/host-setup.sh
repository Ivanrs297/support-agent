#!/usr/bin/env bash
#
# STEP 19.1 — see README §19.
#
# Prepares the EC2 host to receive deploys. Run once, on the instance, after
# bootstrap.sh has finished.
#
# What it has to do:
#
#   1. Install git if the bootstrap did not.
#
#   2. Clone the repository to /opt/support-agent, or fetch if it is already
#      there. Deploys check out a specific commit into this directory, so it has
#      to exist before the first one arrives.
#
#   3. chown the checkout to the ubuntu user, so a human can work in it over
#      SSH.
#
#   4. Mark it as a safe directory for git, `--system`. This is the step that
#      looks superstitious until it bites: deploys arrive over SSM, which runs
#      commands as root, while the checkout belongs to ubuntu. Git refuses to
#      operate across that ownership boundary with "detected dubious ownership"
#      and the deploy fails before it has done anything. --system so it holds
#      for every user, not just whichever one happens to run this.
#
#   5. Create .env from .env.example with mode 600, and only if it is not
#      already there — never overwrite a file holding live secrets.
#
#   6. Print what the operator still has to do by hand: the real GROQ_API_KEY,
#      and making the ghcr package public so the host can pull at all.
#
# Make COMPOSE_DIR overridable, the same way remote-deploy.sh does. The two
# scripts have to agree on which directory holds the stack, and a hardcoded path
# in one of them is how they stop agreeing.
#
set -euo pipefail

TARGET="/opt/support-agent"
COMPOSE_DIR="${COMPOSE_DIR:-$TARGET/deploy}"
OWNER="${OWNER:-ubuntu}"

# TODO
