# The reference solution

The finished system, every folder, with the reasoning written next to the code.
This is what [`supportagent.lat`](https://supportagent.lat) actually runs — it is
deployed from here by `.github/workflows/deploy-solution.yml` at the repository
root, not from the work areas.

**Read this after you have tried the step it belongs to.** The blanks in the
work areas carry the reasoning; this carries the code. Taking the code first is
faster and teaches nothing, and you cannot tell the difference until the first
thing breaks in production.

```
solution/
├── project/            the agent — the only thing that gets deployed
├── deploy/             compose file, Caddyfile, host bootstrap
├── infra/              IAM, OIDC, the instance profile
└── .github/workflows/  the finished CI/CD, for reading
```

Every design decision here is downstream of one number: the host is a
`t4g.nano` with **512 MiB of RAM**, of which about 150 MiB is free once Caddy and
the API are running. Simplicity is not a style preference in this repository, it
is what fits.

---

## `project/` — the agent

| File | What it is | Steps |
|---|---|---|
| `app/config.py` | settings, validated at import | 7 |
| `app/tools.py` | IDF retrieval over the knowledge base, and the reservation lookup | 8–10 |
| `app/agent.py` | the system prompt, the ReAct loop, the trace | 11, 12, 14, 26 |
| `app/main.py` | `/health` `/session` `/providers` `/ui` `/chat` `/chat/stream` | 13, 14, 24, 25 |
| `app/security.py` | token, lockout, two rate limits | 21–23 |
| `app/providers.py` | Groq and Bedrock, and what a call costs | 25 |
| `app/static/index.html` | the browser client, one file | 14, 24, 26 |
| `app/data/` | 28 documentation sections, 5 fake bookings | 8, 10 |
| `Dockerfile` | multi-stage, non-root, healthcheck | 15 |
| `tests/` | 64 tests, no network | throughout |

### How it answers

A ReAct agent with two tools, deliberately different in kind.

`search_hotel_policies` is **fuzzy**. It ranks the 28 sections of `app/data/kb/`
by IDF term overlap and returns the top three — no embeddings, no vector store,
no network call. The model reads prose and interprets it.

`get_reservation` is **exact**. It looks up a confirmation code in
`reservations.json` and returns structured fields, or a refusal. The model gets
nothing to interpret.

Both fail, and they fail differently: the first can return something irrelevant,
the second returns nothing at all. Retrieval therefore has a **relevance floor** —
a passage matching one common word is dropped rather than passed to the model,
because an irrelevant-but-plausible passage is what an agent invents from.

The floor cannot be a constant, and `tools.py` carries the argument at length.
`pool` and `service` both appear in four sections and score identically at 1.89,
yet "pool hours" must return the pool section and "babysitting service" must
return nothing. What separates them is the balance of known to unknown words in
the query, not the score.

**Nothing about the hotel lives in the system prompt.** If it did, the agent
would answer confidently from memory and drift from the documentation the moment
either changed. Every fact in an answer comes back through a tool, which is what
makes the `sources` list meaningful.

### Two providers, one agent

The same agent runs on **Groq** or **Amazon Bedrock**, chosen per request rather
than per deployment — `{"provider": "bedrock", "messages": [...]}`, or the
buttons in the header of `/ui`. `GET /providers` reports which ones this
deployment can actually use and why not the others; asking for one it cannot
serve is a `400` naming the missing piece, not a `500`.

Bedrock ships with **no default model id**. Access is granted per model in the
AWS console, so any id shipped here would be wrong for most accounts — and wrong
in a way that only appears at the first question.

```bash
aws bedrock list-foundation-models --region us-east-2 \
  --query 'modelSummaries[].modelId' --output text | tr '\t' '\n'
```

The authentication asymmetry is the part worth noticing. Groq needs an API key
in a file. Bedrock needs either a key (`AWS_BEARER_TOKEN_BEDROCK`, which botocore
finds on its own) or **nothing at all** — an IAM instance role, where the machine
proves who it is and no secret exists to leak or rotate. That is the same idea as
the OIDC in the deploy pipeline, applied to the runtime.

### What every answer costs

Each reply carries a trace: provider, model, elapsed time, token counts in and
out, an estimated price, and one entry per model call and tool call with the
arguments each tool was given. `/chat` returns it as `trace`; `/chat/stream`
sends it as the last event, after the tokens and the sources.

```
groq · llama-3.1-8b-instant · 729 ms · 2 model calls · 1,704 tokens
  1  model   in    780 · out  19  → calls search_hotel_policies
  2  tool    search_hotel_policies {"query":"pets"} → 308 chars
  3  model   in    882 · out  22  → answers
  1,663 in · 41 out · about $0.000086
```

It is reconstructed from the messages a run produced rather than observed live,
so the blocking and streaming paths report the same shape. The cost of that
choice is per-step timings: the total is measured, the split between steps is not.

A trace whose only step is a model call that called nothing is the agent
answering from memory. The interface marks that case in red rather than letting
it look like every other answer — it is the failure this project exists to make
visible.

### Getting in

`/chat` and `/chat/stream` need `Authorization: Bearer $API_TOKEN`. `/health`
does not — the container healthcheck calls it, and a healthcheck that needs a
secret fails for the wrong reasons. `/session` validates a token and costs
nothing, so the browser client can check one without spending anybody's quota.

Five wrong tokens from one address lock that address out for fifteen minutes.
Keyed on the address rather than the token, because locking the token would hand
any stranger a denial of service for the price of five bad requests.

Two rate limits sit in front of every request that reaches a model provider: ten
per minute per token, and a daily cap across all callers. The first is fairness,
the second is the bill. Both answer with `429` and a `Retry-After`.

Every counter is in memory. A deploy resets them, and they are correct only
because one container with one worker serves the site. That is also the way out
of a lockout you inflicted on yourself: `docker compose restart api`.

The address comes from `X-Forwarded-For`, which Caddy overwrites with the real
client and does not append to — verified, not assumed, because if it appended,
the first entry would be whatever the caller claimed and the lockout would be one
header away from useless.

### Running it

```bash
cd solution/project
cp .env.example .env        # add your GROQ_API_KEY and API_TOKEN
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
set -a && source .env && set +a
pytest -q                   # 64 passed
uvicorn app.main:app --reload
```

Or in the container it actually ships in:

```bash
docker build -t support-agent .
docker run --rm -p 8000:8000 --env-file .env support-agent
curl localhost:8000/health
```

The browser client is at `/ui`, the API documentation at `/docs`.

---

## `deploy/` — what runs on the host

| File | What it is |
|---|---|
| `docker-compose.yml` | caddy + api, with the memory limits that keep the host alive |
| `Caddyfile` | three lines: two domains and a reverse proxy |
| `.env.example` | every variable, with the reason for each |
| `bootstrap.sh` | EC2 user-data — the whole host, from nothing |
| `host-setup.sh` | run once: clone, permissions, `.env` |
| `remote-deploy.sh` | the rollout, and the rollback that fires on its own |

Four decisions in here are the ones that took the longest to learn:

**No `container_name`, on either service.** Docker container names are global,
not scoped to the compose project. A stack left running from an earlier lecture
owns the name `api`, and every deploy after it dies on a conflict. Compose
generates `deploy-api-1`, and the services still address each other by service
name.

**`expose`, not `ports`, on the api service.** Publishing its port would put the
API on the public internet beside Caddy, and `security.py` trusts
`X-Forwarded-For` precisely because that cannot happen.

**A named volume on Caddy's `/data`.** Certificates live there. Without it, every
restart requests a new one and burns through Let's Encrypt's weekly rate limit.

**`docker compose up -d`, never `restart`.** Environment is resolved when a
container is created, so `restart` does not re-read `env_file`. A changed `.env`
with a `restart` is an hour of debugging a change that never applied.

`remote-deploy.sh` reads the current `IMAGE_TAG` before overwriting it — that
value is the only thing that makes a rollback possible — and waits on the
container's own `HEALTHCHECK`, which tests the same thing the restart policy does
from inside the network the app runs in. If it does not go healthy in 90 seconds,
the previous tag comes back and the script exits non-zero. `COMPOSE_DIR` and
`HEALTH_TIMEOUT` are overridable so all three paths can be exercised against a
local registry rather than discovered in production.

---

## `infra/` — the credentials nobody has to hold

| File | What it creates |
|---|---|
| `setup-github-oidc.sh` | the OIDC provider, the deploy role, and a policy scoped to one instance |
| `attach-ssm-profile.sh` | the IAM role, the instance profile, and the association |

There is no SSH key in a GitHub secret and no long-lived AWS credential anywhere.
The runner exchanges an OIDC token for a session allowed to run **one action
against one instance ARN**. Port 22 stays closed.

Two things in here are pure scar tissue:

**The trust policy allows two subject forms.** GitHub issues the token with an
immutable subject carrying numeric IDs —
`repo:OWNER@ownerID/NAME@repoID:ref:refs/heads/main` — while every tutorial shows
`repo:OWNER/NAME:ref:refs/heads/main`. Matching only the classic form fails with
"Not authorized to perform sts:AssumeRoleWithWebIdentity" and explains nothing.

**A role is not an instance profile.** EC2 attaches profiles, not roles. An
instance launched without one has `IamInstanceProfile: null`, the SSM agent finds
nothing to authenticate with and never registers, and `SendCommand` answers
`InvalidInstanceId` — which reads exactly like the agent not being installed.

---

## `.github/workflows/` — the pipeline, for reading

`deploy.yml` and `rollback.yml` here are the finished versions. The ones that
actually run live at the repository root as `deploy-solution.yml` and
`rollback-solution.yml`, because GitHub only reads `.github/` from the root and
the root copies are work areas.

Three details cost a full afternoon each:

**`runs-on: ubuntu-24.04-arm`.** The host is Graviton. Building arm64 on an x86
runner under QEMU works and takes roughly ten times as long.

**`ghcr.io/${GITHUB_REPOSITORY,,}`.** ghcr rejects a repository name containing
capitals and `github.repository` hands you the owner's name verbatim.

**A manifest check before building.** Re-running a workflow re-pushes tags that
already exist, and ghcr answers a burst of those with a secondary rate limit — a
403 that reads exactly like a permissions problem and is not one.

And the inline SSM commands use `set -eu`, not `set -euo pipefail`.
`AWS-RunShellScript` runs them with `/bin/sh`, which on Ubuntu is dash, and
`pipefail` is a bashism that aborts the entire script with "Illegal option".
Anything needing bash lives in `remote-deploy.sh`, invoked with bash on purpose.

---

## What is deliberately not here

Naming the gaps is more useful than a green test suite.

- **No evaluation of answer quality.** The retrieval thresholds were tuned
  against 35 hand-written cases, which is a spot check, not a measurement. An
  image with a broken system prompt deploys green.
- **No staging environment.** The healthcheck proves the process is up and
  serving `/health`, not that it answers a guest correctly.
- **No trace persistence.** A trace lives as long as the response that carries
  it. Nothing to query later, no history to compare runs against.
- **No memory between requests.** The client sends the whole conversation every
  time, which is what makes the container replaceable mid-conversation.
- **No per-user accounting.** One token for everyone, and no revocation short of
  rotating it.
- **No local embeddings, vector store or database.** At 512 MiB, embedding is a
  network call and state lives somewhere else.

The first three are the evaluation module's subject.
