#!/usr/bin/env bash
#
# STEP 16 — see README §16.
#
# Run once, in AWS CloudShell (aws and jq are already there) or anywhere with
# admin credentials:
#
#   INSTANCE_ID=i-0123456789abcdef0 bash infra/setup-github-oidc.sh
#
# What it creates:
#
#   1. An IAM OIDC identity provider for token.actions.githubusercontent.com,
#      if the account does not already have one. Accounts often do — creating a
#      second is an error, so check first.
#
#   2. An IAM role GitHub Actions can assume, with a trust policy pinned to
#      THIS repository and the main branch.
#
#   3. A policy on that role permitting exactly ssm:SendCommand against one
#      instance ARN and the AWS-RunShellScript document. Not `*`. The whole
#      argument for OIDC over an SSH key is that the credential is scoped and
#      short-lived, and a wildcard here gives that back.
#
# The trap, and it will cost you an afternoon if you meet it cold:
#
#   GitHub issues the token with an IMMUTABLE subject claim that carries numeric
#   IDs, not just names:
#
#     repo:OWNER@ownerID/NAME@repoID:ref:refs/heads/main
#
#   while every tutorial shows the classic form:
#
#     repo:OWNER/NAME:ref:refs/heads/main
#
#   A trust policy matching only the classic form fails with "Not authorized to
#   perform sts:AssumeRoleWithWebIdentity" and says nothing about why. Allow
#   BOTH forms. README §16 shows how to decode the token your own repository
#   sends, which is faster than guessing.
#
#   Related: do NOT add `environment:` to the deploy job. It rewrites the
#   subject to repo:OWNER/NAME:environment:NAME and this policy stops matching.
#
# Preflight before doing anything: check that aws and jq exist, that credentials
# work, and that they can read IAM. Failing at step three of five leaves half a
# role behind.
#
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:?set INSTANCE_ID to the EC2 instance the deploy targets}"
AWS_REGION="${AWS_REGION:-us-east-2}"
ROLE_NAME="${ROLE_NAME:-support-agent-github-deploy}"

# TODO
