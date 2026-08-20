# `project/` — your work area

The agent. This is the only thing that gets deployed.

**The guide is the [root README](../README.md).** Steps 7–15 build this
directory, and steps 21–26 come back to it. Every blank in here carries a
`STEP n` marker pointing at the section that explains it.

```
project/
├── Dockerfile              # STEP 15
├── .env.example
├── tests/                  # given — written, and failing
└── app/
    ├── config.py           # STEP 7
    ├── tools.py            # STEPS 8, 9, 10
    ├── agent.py            # STEPS 11, 12, 14, 26
    ├── main.py             # STEPS 13, 14, 24, 25
    ├── security.py         # STEPS 21, 22, 23
    ├── providers.py        # STEP 25
    ├── static/index.html   # STEPS 14, 24, 25, 26 — four blank functions
    ├── requirements.txt    # given
    └── data/               # given
        ├── kb/*.md         # 28 sections, the corpus the tests are tuned against
        └── reservations.json
```

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export GROQ_API_KEY=test-key-not-used API_TOKEN=test-token-not-real
pytest -q
```

The first run is four **collection errors**, not 64 failures: `config.py` builds
its settings at import and every module imports it, so nothing can be collected
until step 7 exists. Each blank names its own step in the exception.

The finished version is in [`../solution/project/`](../solution/project/). Read
it after you have tried, not before.
