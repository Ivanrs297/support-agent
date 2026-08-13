#!/usr/bin/env bash
#
# Prepares the EC2 host to receive deploys. Run once, on the instance, after
# bootstrap.sh has finished.
#
#   ssh agent
#   curl -fsSL https://raw.githubusercontent.com/Ivanrs297/support-agent/main/deploy/host-setup.sh | sudo bash
#
# Afterwards, fill in /opt/support-agent/deploy/.env — the deploy will fail
# loudly until GROQ_API_KEY is set, which is the intended behaviour.
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Ivanrs297/support-agent.git}"
TARGET="/opt/support-agent"
OWNER="${OWNER:-ubuntu}"

# git is not part of the bootstrap image. Deploys check out the commit being
# released, so the host needs it.
if ! command -v git >/dev/null; then
  apt-get update -y
  apt-get install -y git
fi

if [ -d "$TARGET/.git" ]; then
  echo "$TARGET already a checkout, fetching"
  git -C "$TARGET" fetch --quiet origin main
else
  echo "cloning into $TARGET"
  git clone --quiet "$REPO_URL" "$TARGET"
fi

chown -R "$OWNER:$OWNER" "$TARGET"

cd "$TARGET/deploy"
if [ -f .env ]; then
  echo ".env already present, leaving it alone"
else
  cp .env.example .env
  chmod 600 .env
  chown "$OWNER:$OWNER" .env
  echo "created .env from the example"
fi

cat <<EOF

Host ready.

Next:
  1. Put a real GROQ_API_KEY in $TARGET/deploy/.env
  2. Make the ghcr package public, or the host cannot pull the image:
     https://github.com/users/Ivanrs297/packages/container/support-agent/settings
  3. Start it once by hand to confirm:
     cd $TARGET/deploy && docker compose up -d && docker compose ps

From then on, merging to main deploys automatically.
EOF
