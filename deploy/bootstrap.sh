#!/bin/bash
#
# STEP 3 — see README §3.
#
# EC2 user-data. Runs once, as root, the first time the instance boots. Paste it
# into the launch wizard; do not SSH in and run it by hand, because the point is
# that the host is reproducible from nothing.
#
# Order matters here more than in most scripts. Write it so that:
#
#   1. Wait for cloud-init and for the apt lock to clear. Ubuntu is still
#      installing things when user-data starts, and an unguarded apt-get fails
#      with a lock error that reads like a network problem.
#
#   2. Replace the snap SSM agent with the .deb, then purge snapd. Ubuntu AMIs
#      ship amazon-ssm-agent as a snap, and snapd alone costs ~90 MiB of RAM.
#      On a 512 MiB host that is the difference between fitting and not.
#
#   3. 2 GB of swap, with vm.swappiness tuned. Swap is not a substitute for
#      memory; it is what turns an OOM kill into a slow minute.
#
#   4. Docker AND docker-compose-v2. Compose v2 is a separate package on
#      Ubuntu, and without it the first `docker compose up -d` fails with
#      "unknown shorthand flag: 'd'". Install git too — the deploy checks out
#      the commit being released.
#
#   5. Cap the Docker log driver. Unbounded json-file logs fill a 15 GiB disk
#      quietly and then everything fails at once.
#
#   6. Weekly image pruning and unattended-upgrades.
#
#   7. Write a sentinel file at the very end — /var/log/bootstrap-done.log.
#      This is the only reliable way to tell a completed bootstrap from one
#      that died halfway, and you will want it the first time an instance comes
#      up looking healthy and behaving strangely.
#
# Use `set -eux`, not `set -e`. You are reading this back from
# /var/log/cloud-init-output.log with no terminal, and the trace is the only
# thing that tells you which line failed.

set -eux

# TODO
