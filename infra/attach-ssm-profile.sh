#!/usr/bin/env bash
#
# STEP 17 — see README §17.
#
#   INSTANCE_ID=i-0123456789abcdef0 bash infra/attach-ssm-profile.sh
#
# The symptom this fixes: the deploy reaches AWS, authenticates fine, and dies
# with `InvalidInstanceId — Instances not in a valid state for account`. The
# instance is running. The SSM agent is running. Nothing in the message says
# what is actually wrong.
#
# What is wrong is that the instance has no way to prove who it is. An EC2
# instance talks to SSM using credentials from its INSTANCE PROFILE, and an
# instance launched without one has `IamInstanceProfile: null`. The agent starts,
# finds nothing to authenticate with, and never registers. `aws ssm
# describe-instance-information` returns an empty list, which reads like the
# agent is not installed.
#
# What it has to do:
#
#   1. Create an IAM role EC2 can assume, with AmazonSSMManagedInstanceCore.
#
#   2. Create an INSTANCE PROFILE and put the role in it. These are two
#      different objects and this is the part people miss: EC2 attaches
#      profiles, not roles. A role with the right policy and no profile
#      wrapping it is invisible to the instance.
#
#   3. Associate the profile with the running instance. No stop, no restart —
#      associate-iam-instance-profile works live, and the agent picks up
#      credentials within a minute or two.
#
#   4. Poll describe-instance-information until PingStatus is Online, and say so.
#      Otherwise the operator has no idea whether to wait or to start debugging.
#
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:?set INSTANCE_ID}"
AWS_REGION="${AWS_REGION:-us-east-2}"

# TODO
