# Support Agent — Hotel Aurora

The deployed application: a guest support assistant for a fictional 120-room
hotel. Everything else in this repository exists to get this directory onto a
`t4g.nano` and keep it there.

This is the **finished** version. The work area is [`../../project/`](../../project/),
and the step-by-step guide is the [root README](../../README.md). Read this after
you have tried, not before — [`../README.md`](../README.md) says why.

```
project/
├── Dockerfile              # multi-stage, non-root, healthcheck
├── .env.example
├── tests/
└── app/
    ├── main.py             # FastAPI: /health, /session, /ui, /chat, /chat/stream
    ├── agent.py            # ReAct loop over Groq
    ├── tools.py            # documentation search + reservation lookup
    ├── security.py         # token, lockout, rate limits
    ├── config.py           # settings, validated at import
    ├── requirements.txt
    ├── static/index.html   # the browser client, one file
    └── data/
        ├── kb/*.md         # the hotel documentation the agent answers from
        └── reservations.json
```

## Two providers, one agent

The same agent runs on **Groq** or **Amazon Bedrock**, chosen per request rather
than per deployment — `{"provider": "bedrock", "messages": [...]}`, or the
buttons in the header of `/ui`. `GET /providers` reports which ones this
deployment can actually use and why it cannot use the others; asking for one it
cannot serve is a `400` naming the missing piece, not a `500`.

Bedrock ships with **no default model id**. Access there is granted per model in
the AWS console, so any id shipped here would be wrong for most accounts — and
wrong in a way that only appears at the first question. List yours:

```bash
aws bedrock list-foundation-models --region us-east-2 \
  --query 'modelSummaries[].modelId' --output text | tr '\t' '\n'
```

Authentication differs between them, and the difference is the interesting part.
Groq needs an API key in a file. Bedrock needs either a key
(`AWS_BEARER_TOKEN_BEDROCK`, which botocore finds on its own) or nothing at all
— an IAM instance role, where the machine proves who it is and no secret exists
to leak or rotate. The second is the same idea as the OIDC in the deploy
pipeline, applied to the runtime.

## What every answer costs

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
choice is per-step timings: the total is measured, the split between steps is
not.

A trace whose only step is a model call that called nothing is the agent
answering from memory. The interface marks that case in red rather than letting
it look like every other answer — it is the failure this project exists to make
visible.

## Getting in

`/chat` and `/chat/stream` need `Authorization: Bearer $API_TOKEN`. `/health` does
not — the container healthcheck calls it, and a healthcheck that needs a secret
fails for the wrong reasons. `/session` validates a token and costs nothing, so
the browser client can check one without spending anybody's quota.

Five wrong tokens from one address lock that address out for fifteen minutes. The
lockout is keyed on the address rather than on the token, because locking the
token would hand any stranger a denial of service for the price of five bad
requests.

Two rate limits sit in front of every request that reaches Groq: ten per minute
per token, and a daily cap across all callers. The first is fairness, the second
is the bill. Both answer with `429` and a `Retry-After`.

Every counter is in memory. A deploy resets them, and they are correct only
because one container with one worker serves the site. That is also the way out
of a lockout you inflicted on yourself: `docker compose restart api`.

The address comes from `X-Forwarded-For`, which Caddy overwrites with the real
client and does not append to — verified, not assumed, because if it appended,
the first entry would be whatever the caller claimed and the lockout would be one
header away from useless.

## How it answers

A ReAct agent with two tools, deliberately different in kind:

`search_hotel_policies` is fuzzy. It ranks the 28 sections of `data/kb/` by IDF
term overlap and returns the top three — no embeddings, no vector store, no
network call. The model reads prose and interprets it.

`get_reservation` is exact. It looks up a confirmation code in
`data/reservations.json` and returns structured fields, or a refusal. The model
gets nothing to interpret.

Both fail, and they fail differently: the first can return something irrelevant,
the second returns nothing at all. Retrieval therefore has a relevance floor —
a passage matching one common word is dropped rather than passed to the model,
because an irrelevant-but-plausible passage is what an agent invents from.

Nothing about the hotel lives in the system prompt. If it did, the agent would
answer confidently from memory and drift from the documentation the moment
either changed.

## Running locally

```bash
cd solution/project
cp .env.example .env        # add your GROQ_API_KEY
python -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt
set -a && source .env && set +a
uvicorn app.main:app --reload
```

Or in the container it actually ships in:

```bash
docker build -t support-agent .
docker run --rm -p 8000:8000 --env-file .env support-agent
curl localhost:8000/health
```

The browser client is at `/ui` and the API documentation at `/docs`.

```bash
curl -X POST localhost:8000/chat \
  -H 'content-type: application/json' \
  -H "Authorization: Bearer $API_TOKEN" \
  -d '{"messages":[{"role":"user","content":"Can I bring my dog?"}]}'
```

The response carries a `sources` list naming the tools the agent consulted. An
empty list means it answered without checking anything — which the prompt
forbids, and which is worth watching for.

## The memory budget

This is the binding constraint on every dependency decision here. Measured on the
host:

| Container | v1 | v2 | v4 |
|---|---|---|---|
| caddy | ~25 MiB | ~25 MiB | ~25 MiB |
| api | ~33 MiB | ~70 MiB | **~72 MiB** |

The agent cost 37 MiB, almost all of it LangChain and LangGraph at import.
Authentication, the rate limiter and the whole browser interface cost 2 MiB
between them — which is the argument for serving one HTML file instead of
running Streamlit, measured at 46 MiB just to import, or Gradio at 132 MiB.

All of it measured at idle on the built arm64 image, not estimated. Two things
push it higher:

- **The healthcheck spikes it by 10 MiB every 30 seconds.** `HEALTHCHECK` runs
  `python -c ...`, which starts a second interpreter inside the container's
  cgroup. Baseline 69.8 MiB, peak 80.2 MiB. Harmless under `mem_limit: 192m`,
  and worth knowing before it is mistaken for a leak.
- A real conversation holds message history and the model client's buffers.

`mem_limit: 192m` on the api service is what stops any of that from taking the
host down with it.

Consequences, which are rules and not suggestions:

- **No local embeddings.** No `sentence-transformers`, no `torch`. Embedding is a
  network call.
- **Measure before excluding a package.** This project deliberately used to
  forbid the `langchain` meta-package, on the grounds that it drags in hundreds
  of MiB of integrations. That was true of LangChain 0.x. In 1.x the integrations
  moved out to `langchain-classic`, and `langchain` on top of `langgraph` and
  `langchain-groq` measures **~1 MB**. The rule was kept past its expiry date;
  `pip install` and `du -sh` settled it in under a minute.
- **No local vector store, no local database, no local trace storage.** Managed
  services, reached over the network.
- **The container is stateless.** It can be killed and recreated at any moment,
  which is precisely what a deploy does.

Pin every dependency. `pip install` resolving a different version on the host than
on your laptop is a class of failure that only shows up at 512 MiB.

## Image builds

Builds happen in CI and get pushed to `ghcr.io/ivanrs297/support-agent`. The host
only ever pulls. Building on the instance means running a compiler and serving
production traffic in the same 512 MiB, and it does not end well.
