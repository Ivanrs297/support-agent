#!/usr/bin/env bash
#
# Lets GitHub Actions deploy to the EC2 host without any long-lived AWS
# credentials. Run once.
#
#   INSTANCE_ID=i-0abc... bash infra/setup-github-oidc.sh
#
# WHERE TO RUN THIS: somewhere with administrative AWS access. The easiest is
# AWS CloudShell, which already has the aws CLI and jq and uses your console
# session:
#
#   https://us-east-2.console.aws.amazon.com/cloudshell
#   curl -fsSLO https://raw.githubusercontent.com/Ivanrs297/support-agent/main/infra/setup-github-oidc.sh
#   INSTANCE_ID=i-0abc... bash setup-github-oidc.sh
#
# NOT on the agent host. Its instance profile grants SSM and nothing else, so
# every IAM call here would come back AccessDenied — and putting administrative
# credentials on a public-facing box to work around that is exactly the thing
# this whole OIDC setup exists to avoid.
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

# ---------- 0. Preflight ----------
# Fail with an explanation rather than `aws: command not found` forty lines in,
# or halfway through, having created the provider but not the role.
for cmd in aws jq; do
  command -v "$cmd" >/dev/null || {
    cat >&2 <<EOF
$cmd is not installed.

Run this from AWS CloudShell, which has both and needs no setup:
  https://us-east-2.console.aws.amazon.com/cloudshell

Do not run it on the agent host: that instance has SSM permissions only, and
this script needs to create IAM roles.
EOF
    exit 1
  }
done

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) || {
  echo "No usable AWS credentials. In CloudShell they are already there; locally, run 'aws configure'." >&2
  exit 1
}

# Check for IAM write access before creating anything, so a permissions problem
# does not leave the account half-configured.
if ! aws iam list-open-id-connect-providers >/dev/null 2>&1; then
  cat >&2 <<EOF
These credentials cannot read IAM (account $ACCOUNT_ID).

If you are on the EC2 host, that is expected: its instance profile grants SSM
and nothing more. Run this from CloudShell or another admin session instead.
EOF
  exit 1
fi

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
#
# There are two forms of that claim in the wild, and which one a repository gets
# is not something this script can choose:
#
#   classic     repo:OWNER/NAME:ref:refs/heads/main
#   immutable   repo:OWNER@1234/NAME@5678:ref:refs/heads/main
#
# The immutable form carries the numeric owner and repository IDs. It exists so
# that deleting a repository and recreating one with the same name does not
# inherit its deploy permissions — the names can be reused, the IDs cannot.
#
# Both are allowed here. A list under StringEquals means "any of these", and
# pinning both costs nothing: each still names one repository and one branch.
echo "reading the numeric IDs for $REPO"
REPO_META=$(curl -fsSL "https://api.github.com/repos/$REPO") || {
  echo "Could not read https://api.github.com/repos/$REPO — is the name right?" >&2
  exit 1
}
OWNER=$(jq -r .owner.login <<< "$REPO_META")
OWNER_ID=$(jq -r .owner.id <<< "$REPO_META")
REPO_NAME=$(jq -r .name <<< "$REPO_META")
REPO_ID=$(jq -r .id <<< "$REPO_META")

SUB_IMMUTABLE="repo:${OWNER}@${OWNER_ID}/${REPO_NAME}@${REPO_ID}:ref:refs/heads/main"
SUB_CLASSIC="repo:${OWNER}/${REPO_NAME}:ref:refs/heads/main"
echo "  $SUB_IMMUTABLE"
echo "  $SUB_CLASSIC"

TRUST=$(jq -n \
  --arg provider "$PROVIDER_ARN" \
  --arg sub_immutable "$SUB_IMMUTABLE" \
  --arg sub_classic "$SUB_CLASSIC" '{
  Version: "2012-10-17",
  Statement: [{
    Effect: "Allow",
    Principal: { Federated: $provider },
    Action: "sts:AssumeRoleWithWebIdentity",
    Condition: {
      StringEquals: {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": [$sub_immutable, $sub_classic]
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
