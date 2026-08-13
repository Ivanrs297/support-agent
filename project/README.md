# Support Agent — Hotel Aurora

The deployed application: a guest support assistant for a fictional 120-room
hotel. Everything else in this repository exists to get this directory onto a
`t4g.nano` and keep it there.

```
project/
├── Dockerfile              # multi-stage, non-root, healthcheck
├── .env.example
└── app/
    ├── main.py             # FastAPI: /health, /chat, /chat/stream
    ├── agent.py            # ReAct loop over Groq
    ├── tools.py            # documentation search + reservation lookup
    ├── config.py           # settings, validated at import
    ├── requirements.txt
    └── data/
        ├── kb/*.md         # the hotel documentation the agent answers from
        └── reservations.json
```

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
cd project
cp .env.example .env        # add your GROQ_API_KEY
python -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt
set -a && source .env && set +a
uvicorn app.main:app --reload
```

Or in the container it actually ships in:

```bash
docker build -t support-agent .
docker run --rm -p 8000:8000 -e GROQ_API_KEY=... support-agent
curl localhost:8000/health
```

Interactive API documentation is at `/docs`.

```bash
curl -X POST localhost:8000/chat \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"Can I bring my dog?"}]}'
```

The response carries a `sources` list naming the tools the agent consulted. An
empty list means it answered without checking anything — which the prompt
forbids, and which is worth watching for.

## The memory budget

This is the binding constraint on every dependency decision here. Measured on the
host:

| Container | v1 | v2 |
|---|---|---|
| caddy | ~25 MiB | ~25 MiB |
| api | ~33 MiB | **~72 MiB** |

The agent cost 39 MiB, almost all of it LangChain and LangGraph at import. That
is measured at idle, on the built arm64 image, not estimated. Under a real
conversation it will be higher, and `mem_limit: 192m` on the api service is what
stops a leak from taking the host down with it.

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
