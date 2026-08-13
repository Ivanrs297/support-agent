# Support Agent

The deployed application. Everything else in this repository exists to get this
directory onto a `t4g.nano` and keep it there.

```
project/
├── Dockerfile          # multi-stage, non-root, healthcheck
└── app/
    ├── main.py         # FastAPI entrypoint
    └── requirements.txt
```

## Running locally

```bash
cd project
python -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt
uvicorn app.main:app --reload
```

Or in the container it actually ships in:

```bash
docker build -t support-agent .
docker run --rm -p 8000:8000 support-agent
curl localhost:8000/health
```

## The memory budget

This is the binding constraint on every dependency decision here. Measured on the
host:

| Container | RSS |
|---|---|
| caddy | ~25 MiB |
| api | ~33 MiB |
| **Free for the agent** | **~150 MiB** |

Consequences, which are rules and not suggestions:

- **No local embeddings.** No `sentence-transformers`, no `torch`. Embedding is a
  network call.
- **No `langchain` meta-package.** `langgraph` + `langchain-core` + the provider
  SDK only. The meta-package pulls in hundreds of MiB of transitive integrations
  you will not use.
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
