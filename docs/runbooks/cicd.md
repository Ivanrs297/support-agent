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

This needs administrative AWS access, so **not** from the agent host — its
instance profile grants SSM and nothing else, and putting admin credentials on a
public-facing box to work around that defeats the point of the whole setup.

The path with nothing to install is [AWS
CloudShell](https://us-east-2.console.aws.amazon.com/cloudshell), which already
has the CLI and `jq` and runs as your console session:

```bash
curl -fsSLO https://raw.githubusercontent.com/Ivanrs297/support-agent/main/infra/setup-github-oidc.sh
INSTANCE_ID=i-0123456789abcdef0 bash setup-github-oidc.sh
```

Locally instead, if you prefer: `brew install awscli jq && aws configure`.

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

Do this **before** merging the pipeline, or the first deploy arrives at a host
with nothing to check out.

The instance also needs an IAM instance profile, or nothing can reach it over
SSM. Launching an instance without one is the default, and the symptom appears
much later:

```bash
# in CloudShell
INSTANCE_ID=i-0123456789abcdef0 bash infra/attach-ssm-profile.sh
```

```bash
ssh agent
curl -fsSL https://raw.githubusercontent.com/Ivanrs297/support-agent/main/deploy/host-setup.sh | sudo bash
sudo -u ubuntu vi /opt/support-agent/deploy/.env    # add GROQ_API_KEY
```

**If you already deployed by hand from another directory** — `~/deploy` from the
v1 lecture — note that Compose names a project after the directory holding the
compose file. Both are called `deploy`, so both resolve to the same project, the
same containers and the same `deploy_caddy_data` volume. Bringing the new one up
replaces the old containers instead of colliding on ports 80 and 443, and the
Let's Encrypt certificates carry over untouched.

That is convenient, but two checkouts of one project is a trap for whoever
debugs this next. Once the pipeline works, delete the old one:

```bash
rm -rf ~/deploy      # after confirming /opt/support-agent/deploy is serving
```

### 3.4 Make the ghcr package public

The host pulls without credentials, so the package has to be public. It is
private by default even when the repository is public.

**The package does not exist until CI has pushed to it**, so this cannot be done
in advance. The expected sequence is:

1. Merge the pipeline. `test` and `build` pass, and the build creates the
   package — private.
2. `deploy` fails at `pull access denied`.
3. Change the visibility:
   <https://github.com/users/Ivanrs297/packages/container/support-agent/settings>
   → Change visibility → Public.
4. Actions → the failed run → **Re-run failed jobs**.

One failed deploy on the way in is normal here, and worth seeing once: it is the
same error a private base image produces in any pipeline.

### 3.5 Confirm

```bash
cd /opt/support-agent/deploy && docker compose ps
curl -s https://supportagent.lat/health
curl -sX POST https://supportagent.lat/chat -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"What time is check-out?"}]}'
```

The second call is the one that matters. `/health` proves the process is up;
only a real question proves the agent has its API key and can reach Groq.

## 4. Rolling back

Actions → **Rollback** → Run workflow → paste the full 40-character SHA of a
commit that deployed successfully.

It verifies the image exists in ghcr before touching the host, then deploys it
the same way a normal deploy would.

A rollback leaves `main` and production disagreeing. Open a revert PR afterwards,
or the next merge will quietly redeploy the broken commit.

## 5. When it breaks

**`pull access denied` in the SSM output.** The ghcr package is private. See 3.4.

**`Not authorized to perform sts:AssumeRoleWithWebIdentity`.** The subject claim
in the OIDC token did not match the trust policy, which is pinned to
`repo:<owner>/<repo>:ref:refs/heads/main`.

First, know that the claim has two possible shapes:

```
classic     repo:OWNER/NAME:ref:refs/heads/main
immutable   repo:OWNER@26337972/NAME@1333052500:ref:refs/heads/main
```

The immutable form carries the numeric owner and repository IDs, so that a
repository deleted and recreated under the same name does not inherit the old
one's deploy rights — names can be reused, IDs cannot. GitHub decides which form
it emits; `setup-github-oidc.sh` reads the IDs from the API and allows both.

A trust policy written by hand against the classic form fails against a
repository emitting the immutable one, with no hint in the error that the shape
is what differs.

Beyond that, three things produce a mismatch:

- The run is not on `main`. Working as intended — a fork or a feature branch
  cannot deploy.
- The job declares `environment:`. This is the surprising one: referencing a
  GitHub Environment **rewrites** the subject to
  `repo:<owner>/<repo>:environment:<name>`, dropping the branch entirely. The
  workflows here deliberately declare no environment.
- The repository was renamed, so the policy names a repository that no longer
  exists. Re-run `infra/setup-github-oidc.sh`.

To see the policy:

```bash
aws iam get-role --role-name support-agent-github-deploy \
  --query 'Role.AssumeRolePolicyDocument.Statement[0].Condition' --output json
```

To see what the runner actually claims, rather than what it ought to, add a job
that decodes its own token. Print the claims, never the token — it is a real
bearer credential:

```yaml
permissions: { id-token: write }
steps:
  - run: |
      token=$(curl -sH "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
        "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=sts.amazonaws.com" | jq -r .value)
      cut -d. -f2 <<< "$token" | base64 -d 2>/dev/null | jq '{sub, aud}'
```

Comparing those two outputs settles any trust policy question in one run.

**`InvalidInstanceId` — "Instances not in a valid state for account".** SSM
returns this whenever it cannot find the instance as a *managed instance* in the
region the command was sent to. Four different problems share the one message:

```bash
# Is it registered, and where?
aws ssm describe-instance-information --region us-east-2 \
  --query 'InstanceInformationList[].{Id:InstanceId,Ping:PingStatus,Agent:AgentVersion}' \
  --output table

# Is it running, and does it have an instance profile?
aws ec2 describe-instances --region us-east-2 --instance-ids i-0123456789abcdef0 \
  --query 'Reservations[].Instances[].{State:State.Name,Profile:IamInstanceProfile.Arn}' \
  --output json
```

- **Wrong region.** `AWS_REGION` must be where the instance lives. An instance ID
  is meaningless in another region, and the error does not say so.
- **No instance profile**, or one without `AmazonSSMManagedInstanceCore`. The
  `Profile` field above is `null` when this is the problem, and it is the most
  confusing of the four: the agent is installed, running, and reporting
  `active` — it simply has no credentials to register with, so SSM has never
  heard of the host. Fix it without stopping the instance:

  ```bash
  INSTANCE_ID=i-0123456789abcdef0 bash infra/attach-ssm-profile.sh
  ```

  Note that EC2 attaches an *instance profile*, not a role. The profile is a
  container that normally carries the same name as the role inside it, which is
  why the distinction goes unnoticed until something needs it.
- **The instance is stopped.**
- **The agent is not running.** `systemctl is-active amazon-ssm-agent` on the
  host. Ubuntu AMIs ship it as a snap, which the bootstrap replaces — see the
  provisioning runbook for why that matters.

The deploy job checks this before sending anything, so the log names the actual
cause rather than repeating the opaque error.

**The container never becomes healthy, right after adding a setting.**
`config.py` refuses to import when a required variable is missing, so a deploy
that introduces one fails on every host whose `.env` has not been updated. That
is deliberate — a container that starts without its configuration and only fails
when a guest talks to it is worse than one that never starts — but it means new
settings are a two-step release:

```bash
ssh agent
sudo -u ubuntu vi /opt/support-agent/deploy/.env   # add the new variable first
```

Then deploy. The host rolls back to the previous image in the meantime, so the
site stays up while the variable is missing.

**The container never becomes healthy.** The host has already rolled back, so
production is up on the previous image. Read the SSM output in the job log, then
reproduce locally:

```bash
docker run --rm -p 8000:8000 --env-file project/.env ghcr.io/ivanrs297/support-agent:<sha>
```

The most common cause is a missing variable in `deploy/.env`: `config.py`
deliberately raises at import rather than letting the container start and fail on
the first guest request.

**`denied: permission_denied` with a rate-limit body when pushing to ghcr.** A
GitHub secondary rate limit, not a permissions problem, despite the wording. It
comes from pushing the same tags repeatedly in a short window, which is what
debugging a pipeline looks like.

The build now checks whether the commit is already in the registry and skips
itself if so, which makes a re-run cheap instead of another push. If it happens
anyway, wait a few minutes — or, when the image is already built, deploy it with
the **Rollback** workflow, which skips the build entirely. Rolling "back" to the
current commit of `main` is a perfectly good way to deploy it.

**`set: Illegal option -o pipefail` in the SSM output.** `AWS-RunShellScript`
runs its commands with `/bin/sh`, which on Ubuntu is dash. Bash syntax fails
there — `pipefail`, `[[`, arrays, `local`. The inline commands are kept POSIX
and everything else lives in `deploy/remote-deploy.sh`, which is invoked with
`bash` explicitly.

**`detected dubious ownership in repository`.** SSM runs commands as root; the
checkout at `/opt/support-agent` belongs to `ubuntu` so a human can work in it
over SSH. Git will not cross that boundary unattended. `host-setup.sh` declares
the path trusted system-wide; if the message appears anyway, run it directly:

```bash
sudo git config --system --add safe.directory /opt/support-agent
```

**`Conflict. The container name "/api" is already in use`.** A stack from an
earlier lecture is still running. Docker container names are global, not scoped
to a compose project, so two projects cannot both use one. The compose file here
sets no `container_name` for that reason — Compose generates `deploy-api-1` — but
an older stack that does set one still holds the name, and still holds ports 80
and 443.

Find and retire it:

```bash
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Label "com.docker.compose.project.working_dir"}}'
cd <that directory> && docker compose down
```

Note that its Caddy volume does not carry over to the new project, so Caddy will
request fresh certificates on the next deploy. That is fine — Let's Encrypt
allows 50 per domain per week — but it is why the padlock takes a few extra
seconds to appear the first time.

**"Too many failed attempts" and you are the one locked out.** The lockout is
keyed on the caller's real address — Caddy replaces `X-Forwarded-For` with it and
discards whatever the client claimed, so it cannot be spoofed, and it also cannot
be talked around. Every counter is in memory, so a restart clears them all:

```bash
cd /opt/support-agent/deploy && docker compose restart api
```

If the token itself is being rejected, compare it without printing it:

```bash
docker compose exec api python -c \
  "import os; t=os.environ['API_TOKEN']; print(len(t), repr(t[:3]), repr(t[-3:]))"
```

`openssl rand -hex 32` gives 64 characters. 66 starting with a quote means the
value was quoted in `.env` — `env_file` is not a shell and keeps the quote marks.
65 means a stray space or newline. Both are now stripped on read, but an old
container still has the old value.

**A setting changed in `.env` but the container ignores it.** `docker compose
restart` reuses the existing container, and environment is resolved when a
container is *created*, not when it starts. Use `up -d`, which reconciles the
running container against its declared configuration and recreates it when the
`env_file` changed:

```bash
cd /opt/support-agent/deploy && docker compose up -d
```

To see what the container actually has, without printing the secrets:

```bash
docker compose exec api python -c \
  "import os; print(sorted(k for k in os.environ if k.startswith(('BEDROCK','AWS','GROQ','DEFAULT'))))"
```

**"The connection dropped mid-answer" in the browser.** The streamed endpoint
sends its 200 before the first token, so a failure after that point cannot be an
HTTP status — the stream simply ends. It now emits an `{"error": "..."}` event
instead, and the interface shows it. The most common cause is a `BEDROCK_MODEL_ID`
that does not exist: Bedrock ids carry a version suffix (`openai.gpt-oss-20b-1:0`,
not `openai.gpt-oss-20b`), and an invalid one fails at the first question rather
than at startup.

**Nothing ran at all.** Check the path filters. A change confined to `docs/`,
`labs/` or the root `README.md` does not deploy, which is the intended behaviour.
