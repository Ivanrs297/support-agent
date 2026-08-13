#!/usr/bin/env bash
#
# Lets GitHub Actions deploy to the EC2 host without any long-lived AWS
# credentials. Run once, from a machine with admin AWS access.
#
#   bash infra/setup-github-oidc.sh
#
# What it creates:
#   - an OIDC identity provider for token.actions.githubusercontent.com
#   - an IAM role that only workflows on main of this repository can assume
#   - a policy allowing exactly one action on exactly one instance
#
# Re-running is safe: every step checks before it creates.
#
set -euo pipefail

REPO="${REPO:-Ivanrs297/support-agent}"
REGION="${REGION:-us-east-2}"
INSTANCE_ID="${INSTANCE_ID:?set INSTANCE_ID to the agent host, e.g. INSTANCE_ID=i-0abc... bash infra/setup-github-oidc.sh}"
ROLE_NAME="${ROLE_NAME:-support-agent-github-deploy}"
POLICY_NAME="${POLICY_NAME:-support-agent-ssm-deploy}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
PROVIDER_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"

echo "account:  $ACCOUNT_ID"
echo "repo:     $REPO"
echo "instance: $INSTANCE_ID ($REGION)"
echo

# ---------- 1. OIDC provider ----------
# One per account. It may already exist from another project, which is fine.
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER_ARN" >/dev/null 2>&1; then
  echo "OIDC provider already exists"
else
  echo "creating OIDC provider"
  # No thumbprint list: IAM has validated GitHub's certificate chain against
  # trusted roots since 2023, and pinned thumbprints only rot.
  aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com >/dev/null
fi

# ---------- 2. Trust policy ----------
# The `sub` condition is the whole security boundary. Without it, any repository
# on GitHub could assume this role. Restricted to main, so a pull request from a
# fork cannot deploy.
TRUST=$(jq -n --arg provider "$PROVIDER_ARN" --arg repo "$REPO" '{
  Version: "2012-10-17",
  Statement: [{
    Effect: "Allow",
    Principal: { Federated: $provider },
    Action: "sts:AssumeRoleWithWebIdentity",
    Condition: {
      StringEquals: {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:\($repo):ref:refs/heads/main"
      }
    }
  }]
}')

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "updating trust policy on $ROLE_NAME"
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST"
else
  echo "creating role $ROLE_NAME"
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --description "GitHub Actions deploys of $REPO" \
    --assume-role-policy-document "$TRUST" \
    --max-session-duration 3600 >/dev/null
fi

# ---------- 3. Permissions ----------
# Send one shell command to one instance, and read the result back. Not
# ssm:* and not Resource "*": a leaked token should not be able to run
# commands across the account.
PERMISSIONS=$(jq -n \
  --arg region "$REGION" --arg account "$ACCOUNT_ID" --arg instance "$INSTANCE_ID" '{
  Version: "2012-10-17",
  Statement: [
    {
      Sid: "RunTheDeployCommand",
      Effect: "Allow",
      Action: "ssm:SendCommand",
      Resource: [
        "arn:aws:ec2:\($region):\($account):instance/\($instance)",
        "arn:aws:ssm:\($region)::document/AWS-RunShellScript"
      ]
    },
    {
      Sid: "ReadTheResult",
      Effect: "Allow",
      Action: ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations", "ssm:ListCommands"],
      Resource: "*"
    }
  ]
}')

echo "attaching inline policy $POLICY_NAME"
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "$POLICY_NAME" \
  --policy-document "$PERMISSIONS"

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)

cat <<EOF

Done.

Set these as repository *variables* (not secrets — none of them is one):
Settings -> Secrets and variables -> Actions -> Variables

  AWS_ROLE_ARN      $ROLE_ARN
  AWS_REGION        $REGION
  EC2_INSTANCE_ID   $INSTANCE_ID

The workflow needs no AWS secret at all. GROQ_API_KEY is never given to GitHub
either: it lives only in /opt/support-agent/deploy/.env on the host.
EOF
