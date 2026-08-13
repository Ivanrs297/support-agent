# Support Agent

Repository for the **Deployment** module of the AI Engineering Fellowship, and for
the agent deployed in it: a **Support Agent** running 24/7 on a single AWS
`t4g.nano` instance, behind Caddy with automatic TLS, at
**[supportagent.lat](https://supportagent.lat)**.

Everything taught here runs on that same machine. There is no toy environment: the
host you provision in Lecture 1 is the host the agent lives on at the end of the
module.

---

## What's in this repo

| Directory | Contents |
|---|---|
| `project/` | The Support Agent. **The only thing that gets deployed.** |
| `deploy/` | Compose file, Caddyfile, and the host `bootstrap.sh`. |
| `labs/` | One hands-on lab per lecture, with `starter/` and `solution/`. |
| `docs/runbooks/` | Reproducible step-by-step procedures. |
| `docs/decisions/` | Architecture decisions and the reasoning behind them. |
| `infra/` | Infrastructure definitions. |

**Slides are private** and do not live in this repo — `.gitignore` blocks them.

---

## Labs by lecture

One lab per lecture, nine in total. Each is added alongside its lecture slides —
see [`labs/`](labs/).

Two runbooks already back the infrastructure lectures:
[provisioning the agent host](docs/runbooks/aws-agent-host.md) and
[domain + TLS](docs/runbooks/caddy-domain-tls.md).

---

## The agent

`project/` holds the Support Agent: a guest support assistant for **Hotel
Aurora**, a fictional 120-room hotel. It answers from the hotel's documentation
and can look up a reservation by confirmation code, and it is built to say "I
don't know" rather than invent a pet fee.

```bash
cd project
cp .env.example .env          # add your GROQ_API_KEY
docker build -t support-agent .
docker run --rm -p 8000:8000 --env-file .env support-agent

curl -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"What time is check-out?"}]}'
```

Interactive API documentation at `/docs`. See [`project/`](project/) for how it
answers and why nothing about the hotel lives in the system prompt.

---

## Deploying

The host is provisioned once, with `deploy/bootstrap.sh` as user-data. After that,
deploying means pulling the new image and recreating the container:

```bash
ssh agent
cd ~/deploy
docker compose pull && docker compose up -d
```

The full runbook — including the known bootstrap failures and how to diagnose
them — is in [`docs/runbooks/aws-agent-host.md`](docs/runbooks/aws-agent-host.md).

---

## The constraint that shapes everything

`t4g.nano` means **512 MiB of RAM**. Caddy takes ~25 MiB and the API ~72 MiB once
the agent is loaded — 39 MiB of that is LangChain at import, measured rather than
guessed.

That is not a budget accident, it is the exercise: it forces **everything stateful
out of the instance**, and it makes every dependency a decision rather than a
reflex. No local embeddings, no local vector store, no state on disk. The
container ends up stateless by construction, and can be replaced mid-conversation
without anyone noticing.

See [`docs/decisions/`](docs/decisions/) for the full reasoning, including the
tension this creates with the module's *open-source, self-hosted* principle.

### Cost

| Component | USD/month |
|---|---|
| `t4g.nano` 24/7 (Ohio) | 3.07 |
| Public IPv4 address | 3.65 |
| EBS gp3, 15 GiB | 1.20 |
| Everything else (snapshots, traffic) | ~0.92 |
| **Total** | **~8.84** |

The public IP costs more than the instance. That is teaching material, not trivia.

---

## Versions

Each version is a [tag](https://github.com/Ivanrs297/support-agent/tags), so the
project can be read as it was built rather than only as it ended up.

### v2 — The agent

A guest support assistant for **Hotel Aurora**, a fictional 120-room hotel.

- A ReAct agent built with LangChain and LangGraph, on Groq's
  `llama-3.3-70b-versatile`.
- Two tools of deliberately different kinds. `search_hotel_policies` ranks the 28
  sections of the hotel documentation by IDF term overlap and returns prose to
  interpret. `get_reservation` looks up a confirmation code and returns
  structured fields, or nothing.
- Three endpoints, all stateless: `/health`, `/chat`, and `/chat/stream`, the
  last streaming the reply as server-sent events. Interactive documentation at
  `/docs`.
- `GROQ_API_KEY` is the only required secret.

Retrieval has a **relevance floor**, and that is the part worth studying. Term
overlap alone will answer "do you offer babysitting?" with the room service
section, because both contain the word "service" — and a model handed an
irrelevant-but-plausible passage answers from it. Weighting terms by how rare
they are in the corpus separates a real match from a coincidental one, and a
passage below the floor is dropped rather than passed along. Returning nothing is
the better failure.

Deliberately absent: **no memory between requests**. The client sends the whole
conversation each time. There is also no evaluation of whether the answers are
any good — the retrieval thresholds were tuned against 29 hand-written cases,
which is a spot check, not a measurement. That gap is the next module's subject,
and naming it here is more honest than a passing test suite would be.

Measured cost of the agent: **39 MiB**, taking the api container from ~33 to ~72
MiB at idle. Almost all of it is LangChain at import.

### v1 — AWS host with domain and TLS

The machine the agent will live on, and nothing more.

- A `t4g.nano` (Ubuntu 24.04 arm64) in `us-east-2`, provisioned **entirely from
  user-data**: Docker and Compose v2, 2 GB of swap, the SSM agent, log rotation,
  weekly image pruning, and a sentinel file at `/var/log/bootstrap-done.log` that
  is the only reliable way to tell a completed bootstrap from one that died
  halfway.
- A FastAPI container — multi-stage build, non-root, healthcheck — behind Caddy,
  serving `supportagent.lat` and `www` with Let's Encrypt certificates issued and
  renewed automatically.
- Images built in CI and pulled from `ghcr.io`. The host never builds: it cannot
  compile and serve traffic in the same 512 MiB.

Deliberately absent: **there is no agent yet**. The app answers a hello world with
host details. That is the point — this version proves DNS, the security group, the
reverse proxy and TLS all work before anything harder is stacked on top. Debugging
four layers at once through a single symptom ("the browser won't connect") is a
trap worth not walking into.

Measured footprint: caddy ~25 MiB, api ~33 MiB, leaving ~150 MiB for the agent.
Running cost, 24/7: ~$8.84/month.

---

## License

TBD.
