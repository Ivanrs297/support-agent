# CI/CD — From merge to running on the host

**Goal:** merging to `main` builds an arm64 image, pushes it to `ghcr.io`, and
deploys it to the EC2 host, with the host rolling itself back if the new image
does not come up healthy.

**Audience:** AI Engineering Fellowship — Deployment module.

---

## Table of contents

1. [What happens on a merge](#1-what-happens-on-a-merge)
2. [Why SSM and not SSH](#2-why-ssm-and-not-ssh)
3. [One-time setup](#3-one-time-setup)
4. [Rolling back](#4-rolling-back)
5. [When it breaks](#5-when-it-breaks)

---

## 1. What happens on a merge

```
merge to main
   │
   ├── paths touched?  project/** deploy/** .github/workflows/deploy.yml
   │      no  → nothing runs. Editing a lab or a runbook does not restart production.
   │      yes ↓
   │
   ├── test    ubuntu-latest    pytest over retrieval and reservation lookup
   ├── build   ubuntu-24.04-arm docker build → ghcr.io/ivanrs297/support-agent:<sha> and :latest
   └── deploy  ubuntu-latest    OIDC → assume role → ssm:SendCommand
                                   │
                                   └── on the host:
                                       git checkout <sha>
                                       IMAGE_TAG=<sha> in deploy/.env
                                       docker compose pull && up -d
                                       wait for the container healthcheck
                                       not healthy within 90s → restore the previous
                                                                 tag, bring it back,
                                                                 exit non-zero
```

Two details worth understanding rather than copying:

**The image is tagged with the commit SHA, and that is what gets deployed.**
`latest` also moves, but only so the registry is readable by a human. Deploying
`latest` means a rollback has no fixed point to return to.

**The build runs on an arm64 runner.** The host is Graviton. Building on x86 with
QEMU emulation produces the same image and takes about ten times as long. Arm
runners are free for public repositories.

## 2. Why SSM and not SSH

The obvious way to deploy from CI is an SSH key in a GitHub secret. This does not
do that, and the reasons are the lecture:

- **No inbound port.** SSM works because the instance's agent polls AWS outbound.
  Port 22 can stay closed to the internet.
- **No long-lived credential.** GitHub's OIDC token is exchanged for a session
  that lasts the length of the job. There is nothing in the repository to leak,
  rotate, or forget to rotate.
- **The permission is one action on one instance.** The IAM policy allows
  `ssm:SendCommand` against a single instance ARN and a single document. A
  compromised workflow cannot reach the rest of the account.

The cost is that debugging a deploy means reading SSM command output rather than
an SSH session, which is why the workflow prints both stdout and stderr.

## 3. One-time setup

### 3.1 AWS: the role GitHub assumes

From a machine with admin AWS access:

```bash
INSTANCE_ID=i-0123456789abcdef0 bash infra/setup-github-oidc.sh
```

It is idempotent. It creates the OIDC provider if the account does not have one,
creates or updates the role, and attaches the policy. It prints the three values
needed next.

### 3.2 GitHub: three variables, no secrets

Settings → Secrets and variables → Actions → **Variables** (not Secrets):

| Name | Value |
|---|---|
| `AWS_ROLE_ARN` | printed by the script |
| `AWS_REGION` | `us-east-2` |
| `EC2_INSTANCE_ID` | `i-0123456789abcdef0` |

None of these is a secret: a role ARN is useless without the trust policy
matching your repository and branch. `GROQ_API_KEY` is never given to GitHub at
all — it lives only on the host.

### 3.3 The host

```bash
ssh agent
curl -fsSL https://raw.githubusercontent.com/Ivanrs297/support-agent/main/deploy/host-setup.sh | sudo bash
sudo -u ubuntu vi /opt/support-agent/deploy/.env    # add GROQ_API_KEY
```

### 3.4 Make the ghcr package public

The host pulls without credentials, so the package has to be public. It is
private by default even when the repository is public, and this is the single
most common reason a first deploy fails.

<https://github.com/users/Ivanrs297/packages/container/support-agent/settings> →
Change visibility → Public.

### 3.5 Confirm

```bash
cd /opt/support-agent/deploy && docker compose up -d && docker compose ps
curl -s https://supportagent.lat/health
```

Then merge anything under `project/` and watch the Actions tab.

## 4. Rolling back

Actions → **Rollback** → Run workflow → paste the full 40-character SHA of a
commit that deployed successfully.

It verifies the image exists in ghcr before touching the host, then deploys it
the same way a normal deploy would.

A rollback leaves `main` and production disagreeing. Open a revert PR afterwards,
or the next merge will quietly redeploy the broken commit.

## 5. When it breaks

**`pull access denied` in the SSM output.** The ghcr package is private. See 3.4.

**The deploy job fails at "Assume the deploy role".** The trust policy did not
match. It is pinned to `repo:<owner>/<repo>:ref:refs/heads/main`; a run from a
branch or a fork will not match, by design. Re-run `infra/setup-github-oidc.sh`
if the repository was renamed.

**`InvalidInstanceId` from SSM.** The instance is not registered with SSM — check
`systemctl is-active amazon-ssm-agent` on the host. Note that the Ubuntu AMI
ships the agent as a snap; see the provisioning runbook for why that matters.

**The container never becomes healthy.** The host has already rolled back, so
production is up on the previous image. Read the SSM output in the job log, then
reproduce locally:

```bash
docker run --rm -p 8000:8000 --env-file project/.env ghcr.io/ivanrs297/support-agent:<sha>
```

The most common cause is a missing variable in `deploy/.env`: `config.py`
deliberately raises at import rather than letting the container start and fail on
the first guest request.

**Nothing ran at all.** Check the path filters. A change confined to `docs/`,
`labs/` or the root `README.md` does not deploy, which is the intended behaviour.
