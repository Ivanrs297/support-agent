#!/usr/bin/env bash
#
# Gives the agent host an IAM instance profile so it can register with Systems
# Manager. Without one the SSM agent runs happily and registers with nothing:
# `describe-instance-information` returns an empty list, Session Manager cannot
# connect, and SendCommand fails with InvalidInstanceId.
#
# Run from AWS CloudShell, or anywhere with administrative AWS access:
#
#   INSTANCE_ID=i-0abc... bash infra/attach-ssm-profile.sh
#
# The instance does not need to be stopped. Re-running is safe.
#
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:?set INSTANCE_ID, e.g. INSTANCE_ID=i-0abc... bash infra/attach-ssm-profile.sh}"
REGION="${REGION:-us-east-2}"
ROLE_NAME="${ROLE_NAME:-support-agent-ec2-ssm}"
PROFILE_NAME="${PROFILE_NAME:-$ROLE_NAME}"

for cmd in aws jq; do
  command -v "$cmd" >/dev/null || { echo "$cmd is not installed. Use CloudShell." >&2; exit 1; }
done

# ---------- 1. Role the instance assumes ----------
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "role $ROLE_NAME already exists"
else
  echo "creating role $ROLE_NAME"
  aws iam create-role --role-name "$ROLE_NAME" \
    --description "Lets the agent host register with SSM" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": { "Service": "ec2.amazonaws.com" },
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
fi

# The AWS-managed policy for this is the right answer. It grants the agent what
# it needs to register, receive commands and report results, and nothing else.
aws iam attach-role-policy --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

# ---------- 2. Instance profile wrapping it ----------
# EC2 attaches instance profiles, not roles. The profile is a container that
# usually shares the role's name, which is why the distinction is easy to miss
# until something like this fails.
if aws iam get-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null 2>&1; then
  echo "instance profile $PROFILE_NAME already exists"
else
  echo "creating instance profile $PROFILE_NAME"
  aws iam create-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null
fi

if aws iam get-instance-profile --instance-profile-name "$PROFILE_NAME" \
    --query 'InstanceProfile.Roles[0].RoleName' --output text 2>/dev/null | grep -qx "$ROLE_NAME"; then
  echo "role already in the profile"
else
  aws iam add-role-to-instance-profile \
    --instance-profile-name "$PROFILE_NAME" --role-name "$ROLE_NAME"
fi

# ---------- 3. Attach it to the running instance ----------
EXISTING=$(aws ec2 describe-iam-instance-profile-associations --region "$REGION" \
  --filters "Name=instance-id,Values=$INSTANCE_ID" \
  --query 'IamInstanceProfileAssociations[?State!=`disassociated`].AssociationId' \
  --output text)

if [ -n "$EXISTING" ] && [ "$EXISTING" != "None" ]; then
  echo "instance already has a profile associated ($EXISTING), leaving it alone"
else
  # IAM is eventually consistent: the profile can 404 for a few seconds after
  # being created, and associate fails outright rather than waiting.
  echo "associating $PROFILE_NAME with $INSTANCE_ID"
  for attempt in $(seq 1 10); do
    if aws ec2 associate-iam-instance-profile --region "$REGION" \
        --instance-id "$INSTANCE_ID" \
        --iam-instance-profile "Name=$PROFILE_NAME" >/dev/null 2>&1; then
      echo "associated"
      break
    fi
    [ "$attempt" -eq 10 ] && { echo "could not associate after 10 tries" >&2; exit 1; }
    sleep 5
  done
fi

# ---------- 4. Wait for the agent to register ----------
# Credentials reach the instance through the metadata service, which the running
# agent picks up on its own. It usually appears within a minute.
echo "waiting for $INSTANCE_ID to appear in SSM"
for attempt in $(seq 1 24); do
  PING=$(aws ssm describe-instance-information --region "$REGION" \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo None)
  if [ "$PING" = "Online" ]; then
    echo
    aws ssm describe-instance-information --region "$REGION" \
      --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
      --query 'InstanceInformationList[0].{Id:InstanceId,Ping:PingStatus,Agent:AgentVersion,Platform:PlatformName}' \
      --output table
    echo "Done. Re-run the Deploy workflow."
    exit 0
  fi
  sleep 5
done

cat >&2 <<EOF

The profile is attached but the instance has not registered after two minutes.
The agent reads its credentials once at startup, so it may need a nudge:

  ssh agent
  sudo systemctl restart amazon-ssm-agent
  systemctl is-active amazon-ssm-agent

Then re-run this script to confirm.
EOF
exit 1
