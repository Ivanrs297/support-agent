"""Steps 16-20 — merging to main reaches the host on its own.

Every check here is static, and that limit is worth stating plainly: this
autograder cannot merge to your main and watch what happens. What it can do is
confirm that each of the decisions the guide spent an afternoon on is present,
because every one of them was found the expensive way.

The live evidence for this part is elsewhere: if the deployment answers at all,
in part I, then something reached the host.
"""

import re

from ..registry import STATIC, Context, Result, bad, check, ok, skip, without_comments


def _workflow(ctx: Context) -> str | None:
    text = ctx.read(".github/workflows/deploy.yml")
    if not text or "Not implemented yet" in text:
        return None
    return text


# --------------------------------------------------------------------------
# 16. OIDC
# --------------------------------------------------------------------------


@check(16, "the trust policy allows both subject forms", STATIC)
def both_subject_forms(ctx: Context) -> Result:
    text = ctx.read("infra/setup-github-oidc.sh")
    if not text or "TODO" in text:
        return bad("infra/setup-github-oidc.sh is still the work area stub")
    immutable = "@" in text and re.search(r"repo:[^\s\"']*@", text) is not None
    classic = re.search(r"repo:[^\s\"']*/[^\s\"']*:ref:", text) is not None
    if not (immutable and classic):
        missing = "the immutable form with numeric IDs" if not immutable else "the classic form"
        return bad(
            f"only one subject form is allowed; {missing} is missing. GitHub issues "
            "the token with repo:OWNER@ownerID/NAME@repoID:ref:... while every "
            "tutorial shows repo:OWNER/NAME:ref:..., and matching one gets you "
            '"Not authorized to perform sts:AssumeRoleWithWebIdentity" with no hint why'
        )
    return ok("both the immutable and the classic subject are accepted")


@check(16, "the deploy policy is scoped to one instance", STATIC)
def scoped_policy(ctx: Context) -> Result:
    text = ctx.read("infra/setup-github-oidc.sh")
    if not text or "TODO" in text:
        return bad("infra/setup-github-oidc.sh is still the work area stub")
    if "ssm:SendCommand" not in text:
        return bad("the policy never grants ssm:SendCommand, so the deploy cannot run anything")
    if re.search(r'"Resource"\s*:\s*"\*"', text):
        return bad(
            'the policy grants Resource "*". The entire argument for OIDC over an '
            "SSH key is that the credential is scoped and short-lived, and a "
            "wildcard hands that back"
        )
    if "instance/" not in text and ":instance" not in text:
        return skip("could not find an instance ARN to confirm the scope")
    return ok("ssm:SendCommand against one instance ARN")


@check(16, "the deploy job does not reference an environment", STATIC)
def no_environment(ctx: Context) -> Result:
    text = _workflow(ctx)
    if text is None:
        return bad(".github/workflows/deploy.yml is still the work area stub")
    if re.search(r"^\s{4}environment:", text, re.MULTILINE):
        return bad(
            "the job declares an `environment:`. That rewrites the OIDC subject to "
            "repo:OWNER/NAME:environment:NAME and the trust policy stops matching"
        )
    if "id-token: write" not in text:
        return bad("permissions are missing `id-token: write`, so no OIDC token is issued at all")
    return ok("no environment, and id-token: write is granted")


# --------------------------------------------------------------------------
# 17. The instance profile
# --------------------------------------------------------------------------


@check(17, "a role, an instance profile, and the association between them", STATIC)
def instance_profile(ctx: Context) -> Result:
    text = ctx.read("infra/attach-ssm-profile.sh")
    if not text or "TODO" in text:
        return bad("infra/attach-ssm-profile.sh is still the work area stub")
    steps = {
        "create-role": "create-role" in text,
        "AmazonSSMManagedInstanceCore": "AmazonSSMManagedInstanceCore" in text,
        "create-instance-profile": "create-instance-profile" in text,
        "add-role-to-instance-profile": "add-role-to-instance-profile" in text,
        "associate-iam-instance-profile": "associate-iam-instance-profile" in text,
    }
    missing = [name for name, present in steps.items() if not present]
    if missing:
        return bad(
            f"missing: {', '.join(missing)}. A role and an instance profile are two "
            "different objects — EC2 attaches profiles, and a role with the right "
            "policy and no profile wrapping it is invisible to the instance"
        )
    return ok("role, policy, profile, membership and association")


# --------------------------------------------------------------------------
# 18. The build
# --------------------------------------------------------------------------

BUILD_DECISIONS = [
    (
        "an arm64 runner",
        lambda t: "ubuntu-24.04-arm" in t or "arm64" in t,
        "the host is Graviton; building arm64 under QEMU on x86 takes ten times as long",
    ),
    (
        "the repository name lowercased",
        lambda t: "GITHUB_REPOSITORY,," in t or "tr '[:upper:]'" in t,
        "ghcr rejects a name containing capitals and github.repository gives you the owner verbatim",
    ),
    (
        "the commit SHA as a tag",
        lambda t: "github.sha" in t or "GITHUB_SHA" in t,
        "deploying a moving tag leaves a rollback with no fixed point to return to",
    ),
    (
        "a manifest check before building",
        lambda t: "manifests/" in t,
        "re-pushing an existing tag trips ghcr's secondary rate limit, a 403 that reads like a permissions problem",
    ),
    (
        "path filters",
        lambda t: re.search(r"^\s*paths:", t, re.MULTILINE) is not None,
        "editing a lab or a runbook would restart production",
    ),
    (
        "a concurrency group",
        lambda t: "concurrency:" in t,
        "two SSM commands racing docker compose up is how a host ends up serving an image nobody chose",
    ),
]


@check(18, "the build job makes every decision the step listed", STATIC)
def build_job(ctx: Context) -> Result:
    text = _workflow(ctx)
    if text is None:
        return bad(".github/workflows/deploy.yml is still the work area stub")
    missing = [f"{name} ({why})" for name, test, why in BUILD_DECISIONS if not test(text)]
    if missing:
        return bad("; ".join(missing[:2]) + (f"; +{len(missing) - 2} more" if len(missing) > 2 else ""))
    return ok(f"all {len(BUILD_DECISIONS)} decisions present")


@check(18, "the tests run before anything is pushed", STATIC)
def tests_gate_the_build(ctx: Context) -> Result:
    text = _workflow(ctx)
    if text is None:
        return bad(".github/workflows/deploy.yml is still the work area stub")
    if "pytest" not in text:
        return bad("nothing runs the test suite, so a broken agent builds and deploys green")
    if not re.search(r"needs:\s*\[?\s*test", text):
        return bad("the build job does not declare `needs: test`, so it runs whether the suite passed or not")
    return ok("build waits on test")


# --------------------------------------------------------------------------
# 19. The rollout
# --------------------------------------------------------------------------

ROLLOUT_DECISIONS = [
    (
        "reads the current tag before overwriting it",
        lambda t: re.search(r"(PREVIOUS|PREV)\s*=", t) is not None,
        "that value is the only thing that makes a rollback possible, and it is gone once you write the new one",
    ),
    (
        "does not use sed -i",
        lambda t: not re.search(r"sed\s+-i", t),
        "GNU sed takes no argument for it and BSD requires one, so the script runs on the host and never on your laptop",
    ),
    (
        "brings the stack up rather than restarting it",
        lambda t: "up -d" in t,
        "restart reuses the container, and environment is resolved at creation — a changed .env has no effect",
    ),
    (
        "asks compose which container belongs to the project",
        lambda t: "compose ps -q" in t,
        "container names are global, so inspecting one called 'api' may report on a stack from an earlier lecture",
    ),
    (
        "waits on the container healthcheck",
        lambda t: "Health.Status" in t or "health" in t.lower(),
        "the healthcheck is what tells the rollback whether to fire",
    ),
    (
        "restores the previous tag on failure",
        lambda t: t.count("set_tag") >= 2 or t.count("IMAGE_TAG=") >= 2,
        "a deploy that fails without rolling back leaves production down and nobody watching",
    ),
]


@check(19, "remote-deploy.sh can roll itself back", STATIC)
def remote_deploy(ctx: Context) -> Result:
    raw = ctx.read("deploy/remote-deploy.sh")
    if not raw or "# TODO" in raw and len(raw) < 3000:
        return bad("deploy/remote-deploy.sh is still the work area stub")
    # Comments stripped first. This file explains, in a comment, that it does
    # not use `sed -i` — and a check reading the raw text finds the warning and
    # reports it as the offence.
    text = without_comments(raw)
    missing = [f"{name} ({why})" for name, test, why in ROLLOUT_DECISIONS if not test(text)]
    if missing:
        return bad("; ".join(missing[:2]) + (f"; +{len(missing) - 2} more" if len(missing) > 2 else ""))
    return ok(f"all {len(ROLLOUT_DECISIONS)} decisions present")


@check(19, "host-setup.sh declares the checkout a safe directory", STATIC)
def safe_directory(ctx: Context) -> Result:
    text = ctx.read("deploy/host-setup.sh")
    if not text or "# TODO" in text and len(text) < 1500:
        return bad("deploy/host-setup.sh is still the work area stub")
    if "safe.directory" not in text:
        return bad(
            "no `git config --system --add safe.directory`. Deploys arrive over SSM "
            "as root while the checkout belongs to ubuntu, and git refuses to cross "
            'that boundary with "detected dubious ownership" before doing anything'
        )
    return ok("declared, and --system so it holds for every user")


@check(19, "the inline SSM commands are dash-safe", STATIC)
def dash_safe(ctx: Context) -> Result:
    text = _workflow(ctx)
    if text is None:
        return bad(".github/workflows/deploy.yml is still the work area stub")
    block = re.search(r"commands:\s*\[(.*?)\]", text, re.DOTALL)
    if block is None:
        return skip("could not isolate the SSM commands array")
    # Same trap as remote-deploy.sh: the commands array carries a comment
    # naming the bashism it avoids.
    if "pipefail" in without_comments(block.group(1)):
        return bad(
            "the SSM commands use `set -o pipefail`. AWS-RunShellScript runs them "
            'with /bin/sh, which on Ubuntu is dash: that is a bashism and aborts '
            'the whole script with "Illegal option"'
        )
    return ok("set -eu, with anything needing bash left to remote-deploy.sh")


# --------------------------------------------------------------------------
# 20. Rollback
# --------------------------------------------------------------------------


@check(20, "the rollback refuses a SHA that was never built", STATIC)
def rollback(ctx: Context) -> Result:
    text = ctx.read(".github/workflows/rollback.yml")
    if not text or "Not implemented yet" in text:
        return bad(".github/workflows/rollback.yml is still the work area stub")
    problems = []
    if "manifests/" not in text:
        problems.append(
            "it never asks the registry whether that image exists, so rolling back "
            "to a tag that was never built leaves production down and you out of ideas"
        )
    if "40" not in text:
        problems.append("it does not reject a short SHA, which simply will not be found")
    if "concurrency:" not in text:
        problems.append("no concurrency group, so a rollback and a deploy can race onto the same host")
    return bad("; ".join(problems)) if problems else ok("registry check, full SHA required, shares the deploy's lock")
