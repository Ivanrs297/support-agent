"""Steps 7-15 — the agent, and the thing it is built not to do.

This part is mostly runnable, which makes it the strictest in the report. The
student's own suite is the specification; these checks run it in slices so a
failure lands on the step that caused it rather than on a wall of 64 results.
"""

import re

from ..registry import LIVE, LOCAL, STATIC, Context, Result, bad, check, is_stub, ok, skip


def _needs_url(ctx: Context) -> Result | None:
    if not ctx.base_url:
        return skip("no deployment configured — set GRADE_BASE_URL")
    if not ctx.token:
        return skip("no token configured — set GRADE_API_TOKEN")
    return None


def _from_pytest(ctx: Context, target: str, expression: str | None = None) -> Result:
    code, summary = ctx.pytest(target, expression)
    return ok(summary) if code == 0 else bad(summary)


# --------------------------------------------------------------------------
# 7. Settings
# --------------------------------------------------------------------------


@check(7, "settings load, and survive a quoted .env value", LOCAL)
def config_suite(ctx: Context) -> Result:
    return _from_pytest(ctx, "tests/test_config.py")


@check(7, "a missing required variable fails at import, not at the first request", LOCAL)
def config_fails_loudly(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "import os, importlib\n"
        "os.environ.pop('GROQ_API_KEY', None)\n"
        "try:\n"
        "    importlib.import_module('app.config')\n"
        "except NotImplementedError:\n"
        "    raise SystemExit('load_settings is not written yet')\n"
        "except Exception as e:\n"
        "    msg = str(e)\n"
        "    assert 'GROQ_API_KEY' in msg, 'it failed, but never says which variable: ' + msg[:80]\n"
        "    print(type(e).__name__ + ': ' + msg[:90])\n"
        "else:\n"
        "    raise SystemExit('imported cleanly with no GROQ_API_KEY')\n"
    )
    if code != 0:
        return bad(
            (err.splitlines() or ["app.config accepted a missing GROQ_API_KEY"])[-1][:170]
            + " — a container that starts without its configuration goes green on the"
            " healthcheck and only breaks when a guest talks to it"
        )
    return ok(out)


@check(7, "Bedrock has no default model id", STATIC)
def no_bedrock_default(ctx: Context) -> Result:
    text = ctx.read("project/app/config.py") or ""
    match = re.search(r"BEDROCK_MODEL_ID[\"']\s*,\s*[\"']([^\"']*)[\"']", text)
    if match is None:
        return skip("could not find the BEDROCK_MODEL_ID default to inspect")
    if match.group(1):
        return bad(
            f"BEDROCK_MODEL_ID defaults to {match.group(1)!r}. Bedrock grants access "
            "per model in the console, so a shipped default is wrong for most "
            "accounts — and wrong only at the first question"
        )
    return ok("empty, as the step required")


# --------------------------------------------------------------------------
# 8. The knowledge base
# --------------------------------------------------------------------------


@check(8, "the corpus loads as 28 sections", LOCAL)
def passages(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from app.tools import PASSAGES\n"
        "p = PASSAGES\n"
        "assert p, 'no passages loaded'\n"
        "bad = [x for x in p if not x.title or not x.body or not x.source]\n"
        "assert not bad, f'{len(bad)} passages missing a source, title or body'\n"
        "print(len(p))\n"
    )
    if code != 0:
        return bad((err.splitlines() or ["failed to import app.tools"])[-1][:160])
    if out != "28":
        return bad(f"loaded {out} sections; the corpus in app/data/kb has 28")
    return ok("28 sections, each with a source, a title and a body")


@check(8, "an empty corpus is refused", LOCAL)
def empty_corpus_raises(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "import pathlib, app.tools as t\n"
        "t.KB_DIR = pathlib.Path('/tmp/there-is-no-corpus-here')\n"
        "try:\n"
        "    t._load_passages()\n"
        "except Exception as e:\n"
        "    print(type(e).__name__)\n"
        "else:\n"
        "    raise SystemExit('returned quietly')\n"
    )
    if code != 0:
        return bad(
            "_load_passages returns quietly when there is nothing to load. An agent "
            "silently retrieving from an empty corpus looks exactly like an agent "
            "answering from memory"
        )
    return ok(f"raises {out}")


# --------------------------------------------------------------------------
# 9. Retrieval
# --------------------------------------------------------------------------


@check(9, "the 35 retrieval cases", LOCAL)
def retrieval_suite(ctx: Context) -> Result:
    return _from_pytest(ctx, "tests/test_retrieval.py")


@check(9, "the relevance floor was calibrated, not left at zero", STATIC)
def floor_is_set(ctx: Context) -> Result:
    text = ctx.read("project/app/tools.py") or ""
    match = re.search(r"^MIN_RELEVANCE\s*=\s*([0-9.]+)", text, re.MULTILINE)
    if match is None:
        return bad("MIN_RELEVANCE is not defined")
    if float(match.group(1)) == 0.0:
        return bad(
            "MIN_RELEVANCE is still 0.0, so nothing is ever below the floor and "
            "'babysitting service' is answered out of the room service section"
        )
    return ok(f"MIN_RELEVANCE = {match.group(1)}")


@check(9, "adjacent words are joined, so compounds match", LOCAL)
def compound_tokens(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from app.tools import _tokenize\n"
        "a, b = _tokenize('check out time'), _tokenize('checkout')\n"
        "assert a & b, 'no shared token between \"check out\" and \"checkout\"'\n"
        "print(sorted(a & b))\n"
    )
    if code != 0:
        return bad(
            '"checkout" shares no token with "check-out". Join every adjacent pair '
            "of words as well as the words themselves — hyphenated and compound "
            "spellings are everywhere in hotel documentation"
        )
    return ok(f"shared: {out}")


@check(9, "rank and relevance are two different numbers", LOCAL)
def rank_is_not_relevance(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from app.tools import PASSAGES, _score, _tokenize\n"
        "diff = 0\n"
        "for p in PASSAGES:\n"
        "    tokens = _tokenize(p.title)\n"
        "    rank, relevance = _score(tokens, p)\n"
        "    if rank > relevance: diff += 1\n"
        "assert diff, 'rank never exceeds relevance on a title match'\n"
        "print(f'{diff} passages where the title boost applied')\n"
    )
    if code != 0:
        return bad(
            "rank and relevance are the same number. Boost title matches for "
            "ordering only — feed that boost into the threshold too and "
            "'babysitting service' clears it by matching the word 'services' in a heading"
        )
    return ok(out)


@check(9, "an undocumented topic returns nothing", LOCAL)
def refuses_the_undocumented(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from app.tools import search_hotel_policies as s\n"
        # Only the queries the guide's own suite pins. "do you rent bicycles"
        # is a known gap the solution does not close, and asserting it here
        # would fail the reference — which is how this was found.
        "for q in ('babysitting service', 'airport shuttle'):\n"
        "    r = s.invoke({'query': q})\n"
        "    assert 'No section' in r or 'not something' in r, f'{q!r} was answered from: ' + r[:80]\n"
        "print('all three refused')\n"
    )
    if code != 0:
        return bad((err.splitlines() or ["failed"])[-1][:180])
    return ok(out)


@check(9, "a documented topic the corpus words differently still returns", LOCAL)
def finds_pool_hours(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from app.tools import search_hotel_policies as s\n"
        "r = s.invoke({'query': 'pool hours'})\n"
        "assert 'No section' not in r, 'pool hours returned nothing'\n"
        "print(r.splitlines()[0])\n"
    )
    if code != 0:
        return bad(
            "'pool hours' returns nothing. The corpus never uses the word 'hours' — "
            "it says 'open daily from 07:00 to 21:00' — so a fixed floor rejects it. "
            "Move the threshold with the balance of known to unknown words"
        )
    return ok(out)


# --------------------------------------------------------------------------
# 10. The reservation lookup
# --------------------------------------------------------------------------


@check(10, "known and unknown confirmation codes", LOCAL)
def reservation_suite(ctx: Context) -> Result:
    return _from_pytest(ctx, "tests/test_retrieval.py", "reservation")


@check(10, "a miss returns prose that forbids inventing a booking", LOCAL)
def miss_is_explicit(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from app.tools import get_reservation as g\n"
        "r = g.invoke({'confirmation_code': 'AUR-000000'})\n"
        "assert r, 'returned nothing at all'\n"
        "assert 'invent' in r.lower() or 'do not' in r.lower(), 'the refusal does not tell the model what not to do: ' + r[:80]\n"
        "print(r.split('.')[0][:90])\n"
    )
    if code != 0:
        return bad((err.splitlines() or ["failed"])[-1][:180])
    return ok(out)


# --------------------------------------------------------------------------
# 11. The system prompt
# --------------------------------------------------------------------------


@check(11, "the prompt names both tools", STATIC)
def prompt_names_tools(ctx: Context) -> Result:
    text = ctx.read("project/app/agent.py") or ""
    match = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', text, re.DOTALL)
    if match is None:
        return bad("SYSTEM_PROMPT is not a triple-quoted string here")
    prompt = match.group(1)
    if "TODO" in prompt or len(prompt.strip()) < 120:
        return bad("SYSTEM_PROMPT is still the stub")
    missing = [t for t in ("search_hotel_policies", "get_reservation") if t not in prompt]
    if missing:
        return bad(f"the prompt never mentions {', '.join(missing)}, so the model has to guess what each tool covers")
    return ok(f"{len(prompt.split())} words, both tools named")


@check(11, "no hotel facts live in the prompt", STATIC)
def prompt_has_no_facts(ctx: Context) -> Result:
    text = ctx.read("project/app/agent.py") or ""
    match = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', text, re.DOTALL)
    if match is None:
        return skip("could not isolate SYSTEM_PROMPT")
    prompt = match.group(1)
    if "TODO" in prompt or len(prompt.strip()) < 120:
        return bad("SYSTEM_PROMPT is still the stub, so there is no prompt to inspect")
    # A contact detail is fine — it is escalation, not policy. A time or a price
    # is a fact the documentation owns, and the moment one lives here the agent
    # answers from it without calling anything.
    escalation = re.sub(r"\+?\d[\d\s()-]{6,}|\S+@\S+", "", prompt)
    facts = re.findall(r"\b\d{1,2}:\d{2}\b|[$€£]\s?\d+|\b\d+\s?(?:%|EUR|USD)\b", escalation)
    if facts:
        return bad(
            f"the prompt states {', '.join(sorted(set(facts))[:4])}. Every fact here is "
            "one the agent will answer from without calling a tool, and it drifts from "
            "the documentation the moment either changes"
        )
    return ok("no times, prices or fees stated")


# --------------------------------------------------------------------------
# 12. The loop
# --------------------------------------------------------------------------


@check(12, "an agent is built, and cached per provider and model", LOCAL)
def agent_builds(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from app import agent\n"
        "name, model, a = agent._agent_for(None)\n"
        "assert hasattr(a, 'ainvoke'), 'what came back is not a runnable agent'\n"
        "again = agent._agent_for(None)[2]\n"
        "assert a is again, 'the compiled graph is not cached; compiling is not free'\n"
        "print(f'{name} {model}')\n"
    )
    if code != 0:
        return bad((err.splitlines() or ["failed"])[-1][:180])
    return ok(out)


@check(12, "an unknown provider raises ValueError, not an HTTP error", LOCAL)
def unknown_provider(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from app import agent\n"
        "try:\n"
        "    agent._agent_for('not-a-provider')\n"
        "except ValueError as e:\n"
        "    print(str(e)[:80])\n"
        "except Exception as e:\n"
        "    raise SystemExit(f'raised {type(e).__name__}, not ValueError')\n"
        "else:\n"
        "    raise SystemExit('accepted a provider that does not exist')\n"
    )
    if code != 0:
        return bad(
            (err.splitlines() or ["did not raise ValueError"])[-1][:160]
            + " — keeping the HTTP vocabulary out of this module is what lets it be"
            " called from a test, a script or a stream"
        )
    return ok(out)


# --------------------------------------------------------------------------
# 13. The API
# --------------------------------------------------------------------------


@check(13, "/health responds locally", LOCAL)
def local_health(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "r = TestClient(app).get('/health')\n"
        "assert r.status_code == 200, r.status_code\n"
        "print(r.json())\n"
    )
    if code != 0:
        return bad((err.splitlines() or ["failed"])[-1][:180])
    return ok(out)


@check(13, "the deployed /chat answers, and says what it consulted", LIVE)
def live_chat(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    # Memoised: step 26 reads the same response. Every call here spends the
    # student's quota, so the whole run makes exactly two.
    if "chat" not in ctx.memo:
        ctx.memo["chat"] = ctx.request(
            "/chat",
            method="POST",
            payload={"messages": [{"role": "user", "content": "What time is check-out?"}]},
            timeout=90,
        )
    response = ctx.memo["chat"]
    if response.status != 200:
        return bad(f"POST /chat returned {response.status}: {response.body[:160]}")
    body = response.json() or {}
    if not body.get("reply"):
        return bad("the response carries no reply")
    if not body.get("sources"):
        return bad(
            "sources is empty — the agent answered from memory without calling a "
            "tool, which is the one failure this project exists to prevent"
        )
    return ok(f"consulted {', '.join(body['sources'])}")


# --------------------------------------------------------------------------
# 14. Streaming
# --------------------------------------------------------------------------


@check(14, "the stream sends tokens, then sources, then the trace, then done", LIVE)
def live_stream(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    try:
        events = ctx.stream(
            "/chat/stream",
            {"messages": [{"role": "user", "content": "Can I bring my dog?"}]},
        )
    except Exception as failure:
        return bad(f"{type(failure).__name__}: {failure}")

    order = [next(iter(e)) for e in events if e]
    if "token" not in order:
        return bad(f"no token events arrived; got {sorted(set(order))}")
    for name in ("sources", "trace", "done"):
        if name not in order:
            return bad(f"the stream never sent a {name} event; got {sorted(set(order))}")
    if order.index("sources") > order.index("trace"):
        return bad("the trace arrived before the sources; it is not complete until the run is")
    if order[-1] != "done":
        return bad(f"the last event was {order[-1]!r}, not done")
    tokens = sum(1 for name in order if name == "token")
    return ok(f"{tokens} token events, then sources, trace, done")


@check(14, "the stream is not buffered by the proxy", LIVE)
def no_buffering(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    response = ctx.request(
        "/chat/stream",
        method="POST",
        payload={"messages": [{"role": "user", "content": "hi"}]},
        timeout=90,
    )
    headers = response.headers
    if headers.get("x-accel-buffering") != "no":
        return bad(
            "X-Accel-Buffering: no is missing. A proxy will buffer the whole stream "
            "and deliver it at once, which is indistinguishable from a slow model"
        )
    if "no-cache" not in headers.get("cache-control", ""):
        return bad("Cache-Control does not say no-cache")
    return ok("no-cache, and buffering disabled")


@check(14, "a provider this deployment cannot serve is a 400, not a dead stream", LIVE)
def stream_refuses_early(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    providers = (ctx.request("/providers", token=None).json()) or []
    unavailable = next((p["name"] for p in providers if not p.get("available")), None)
    if unavailable is None:
        return skip("every provider is configured, so there is nothing to refuse")
    response = ctx.request(
        "/chat/stream",
        method="POST",
        payload={"provider": unavailable, "messages": [{"role": "user", "content": "hi"}]},
        timeout=60,
    )
    if response.status != 400:
        return bad(
            f"asking for the unconfigured provider {unavailable!r} returned "
            f"{response.status}. Resolve the provider before the response starts — "
            "after the 200 leaves, nothing can be a status code"
        )
    return ok(f"400 for {unavailable}: {(response.json() or {}).get('detail', '')[:80]}")


# --------------------------------------------------------------------------
# 15. The image
# --------------------------------------------------------------------------

DOCKERFILE_DECISIONS = [
    (
        "two build stages",
        lambda t: len(re.findall(r"^FROM ", t, re.MULTILINE)) >= 2,
        "a single stage carries pip, its cache and the build toolchain into production",
    ),
    (
        "runs as a non-root user",
        lambda t: re.search(r"^USER\s+(?!root)", t, re.MULTILINE) is not None,
        "least privilege, and it costs nothing",
    ),
    (
        "a HEALTHCHECK",
        lambda t: "HEALTHCHECK" in t,
        "this is what the deploy's health gate reads and what tells the rollback whether to fire",
    ),
    (
        "exactly one uvicorn worker",
        lambda t: "--workers" in t and re.search(r"--workers[\"',\s]+1", t) is not None,
        "every rate limit and lockout counter lives in one process's memory; two workers "
        "silently double every limit",
    ),
    (
        "requirements installed before the app is copied",
        # `COPY app/requirements.txt` and `COPY app/ ./app/` both start with
        # "COPY app", so a substring search finds the wrong one and reports the
        # correct Dockerfile as wrong. Match the directory copy specifically.
        lambda t: (
            (m := re.search(r"^COPY\s+app/\s", t, re.MULTILINE)) is not None
            and (r := re.search(r"^RUN\s+pip\s+install", t, re.MULTILINE)) is not None
            and r.start() < m.start()
        ),
        "otherwise editing the agent reinstalls LangChain on every build",
    ),
]


@check(15, "the Dockerfile makes every decision the step listed", STATIC)
def dockerfile(ctx: Context) -> Result:
    text = ctx.read("project/Dockerfile")
    if not text or text.count("FROM") == 0:
        return bad("project/Dockerfile is still the work area stub")
    missing = [f"{name} ({why})" for name, test, why in DOCKERFILE_DECISIONS if not test(text)]
    if missing:
        return bad("; ".join(missing[:2]) + (f"; +{len(missing) - 2} more" if len(missing) > 2 else ""))
    return ok(f"all {len(DOCKERFILE_DECISIONS)} decisions present")
