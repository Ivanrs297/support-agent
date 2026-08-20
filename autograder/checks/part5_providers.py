"""Steps 25-26 — two providers, and the record of what each answer cost.

The live checks here reuse the single /chat response already bought in step 13.
The trace is part of that response, so grading it costs nothing extra — which is
the same argument the guide makes for sending the trace down the stream instead
of letting the client ask for it separately.
"""

import re

from ..registry import LIVE, LOCAL, STATIC, Context, Result, bad, check, ok, skip


def _needs_url(ctx: Context) -> Result | None:
    if not ctx.base_url:
        return skip("no deployment configured — set GRADE_BASE_URL")
    if not ctx.token:
        return skip("no token configured — set GRADE_API_TOKEN")
    return None


# --------------------------------------------------------------------------
# 25. The second provider
# --------------------------------------------------------------------------


@check(25, "an unknown price is None, never zero", LOCAL)
def unknown_price(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from app.providers import estimate_cost\n"
        "v = estimate_cost('a-model-nobody-has-priced', 1000, 1000)\n"
        "assert v is None, f'returned {v!r}'\n"
        "print('None')\n"
    )
    if code != 0:
        return bad(
            "estimate_cost returns a number for a model it has no price for. An "
            "unknown price displayed as $0.00 reads as free, which is the one wrong "
            "answer a cost display must never give"
        )
    return ok("None for an unpriced model")


@check(25, "each provider explains itself when it cannot be used", LOCAL)
def providers_explain(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from app.providers import available_providers\n"
        "ps = {p.name: p for p in available_providers()}\n"
        "assert {'groq', 'bedrock'} <= set(ps), f'providers listed: {sorted(ps)}'\n"
        "for p in ps.values():\n"
        "    assert p.detail, f'{p.name} reports no detail'\n"
        "    if not p.available:\n"
        "        assert len(p.detail) > 8, f'{p.name} is unavailable and says only {p.detail!r}'\n"
        "print(', '.join(f\"{p.name}={'ok' if p.available else 'unavailable'}\" for p in ps.values()))\n"
    )
    if code != 0:
        return bad((err.splitlines() or ["failed"])[-1][:180])
    return ok(out)


@check(25, "Bedrock passes no credentials of its own", STATIC)
def bedrock_uses_the_chain(ctx: Context) -> Result:
    text = ctx.read("project/app/providers.py") or ""
    bedrock = re.search(r"ChatBedrockConverse\((?:[^()]|\([^()]*\))*\)", text)
    if bedrock is None:
        return skip("could not find where the Bedrock client is constructed")
    leaked = [
        name
        for name in ("aws_access_key_id", "aws_secret_access_key", "credentials_profile_name", "aws_session_token")
        if name in bedrock.group(0)
    ]
    if leaked:
        return bad(
            f"credentials are passed explicitly ({', '.join(leaked)}). Let boto3 resolve "
            "them through its own chain — on the host that is the instance role, and "
            "there is nothing to put in .env and nothing to rotate"
        )
    return ok("boto3 resolves them; no secret in the code")


@check(25, "/providers is public and reports availability", LIVE)
def live_providers(ctx: Context) -> Result:
    if not ctx.base_url:
        return skip("no deployment configured — set GRADE_BASE_URL")
    response = ctx.request("/providers", token=None)
    if response.status != 200:
        return bad(
            f"GET /providers returned {response.status} without a token. The browser "
            "client needs it to render the switch before anyone has typed one"
        )
    body = response.json()
    if not isinstance(body, list) or not body:
        return bad(f"/providers did not return a list: {response.body[:120]}")
    names = {p.get("name") for p in body}
    if not {"groq", "bedrock"} <= names:
        return bad(f"/providers lists {sorted(names)}; both groq and bedrock should appear, available or not")
    for provider in body:
        if not provider.get("detail"):
            return bad(f"{provider.get('name')} reports no detail, so an unavailable provider says nothing about why")
    return ok(", ".join(f"{p['name']}: {p['detail'][:28]}" for p in body))


@check(25, "an unconfigured provider is a 400 naming what is missing", LIVE)
def unconfigured_is_400(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    providers = ctx.request("/providers", token=None).json() or []
    unavailable = next((p["name"] for p in providers if not p.get("available")), None)
    if unavailable is None:
        return skip("every provider is configured, so there is nothing to refuse")
    response = ctx.request(
        "/chat",
        method="POST",
        payload={"provider": unavailable, "messages": [{"role": "user", "content": "hi"}]},
        timeout=60,
    )
    if response.status == 500:
        return bad(
            f"asking for {unavailable!r} returned 500. Nothing broke — the request "
            "named something this deployment is not configured for, which is a 400"
        )
    if response.status != 400:
        return bad(f"asking for the unconfigured provider {unavailable!r} returned {response.status}, not 400")
    detail = (response.json() or {}).get("detail", "")
    if unavailable not in detail:
        return bad(f"the 400 does not name the provider or the missing piece: {detail[:100]}")
    return ok(detail[:100])


# --------------------------------------------------------------------------
# 26. The trace
# --------------------------------------------------------------------------


@check(26, "the trace's shape, in the student's own suite", LOCAL)
def trace_suite(ctx: Context) -> Result:
    code, summary = ctx.pytest("tests/test_trace.py")
    return ok(summary) if code == 0 else bad(summary)


@check(26, "an answer with no tool call has no sources", LOCAL)
def memory_answer_has_no_sources(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from langchain_core.messages import AIMessage\n"
        "from app.agent import _sources, _trace_from\n"
        "m = AIMessage(content='Check-out is at 11:00.', "
        "usage_metadata={'input_tokens': 700, 'output_tokens': 8, 'total_tokens': 708})\n"
        "t = _trace_from([m], 'groq', 'some-model', ms=200)\n"
        "assert _sources(t['steps']) == [], 'sources were reported for an answer that called nothing'\n"
        "assert t['model_calls'] == 1, t['model_calls']\n"
        "print('an answer from memory reports no sources')\n"
    )
    if code != 0:
        return bad((err.splitlines() or ["failed"])[-1][:180])
    return ok(out)


@check(26, "a tool step carries the arguments it was called with", LOCAL)
def trace_carries_arguments(ctx: Context) -> Result:
    code, out, err = ctx.python(
        "from langchain_core.messages import AIMessage, ToolMessage\n"
        "from app.agent import _trace_from\n"
        "call = {'name': 'get_reservation', 'args': {'confirmation_code': 'AUR-104582'}, 'id': 'c1'}\n"
        "history = [AIMessage(content='', tool_calls=[call]), "
        "ToolMessage(content='{...}', name='get_reservation', tool_call_id='c1')]\n"
        "step = [s for s in _trace_from(history, 'groq', 'm', ms=1)['steps'] if s['kind'] == 'tool'][0]\n"
        "assert step['args'] == call['args'], step\n"
        "print(step['args'])\n"
    )
    if code != 0:
        return bad(
            "the tool step does not carry its arguments. A ToolMessage does not have "
            "them — they were on the AIMessage that requested it — and without them a "
            "trace shows that a tool ran but not what was asked of it, which is most "
            "of what you need to explain a bad answer"
        )
    return ok(out)


@check(26, "the deployed answer carries a complete trace", LIVE)
def live_trace(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    response = ctx.memo.get("chat")
    if response is None:
        return skip("step 13 did not get an answer to read a trace from")
    if response.status != 200:
        return skip(f"step 13's request returned {response.status}")

    trace = (response.json() or {}).get("trace")
    if not isinstance(trace, dict):
        return bad("the response carries no trace object")
    missing = [
        field
        for field in ("provider", "model", "ms", "model_calls", "input_tokens", "output_tokens", "cost_usd", "steps")
        if field not in trace
    ]
    if missing:
        return bad(f"the trace is missing {', '.join(missing)}")
    if not trace["steps"]:
        return bad("the trace has no steps")
    kinds = [s.get("kind") for s in trace["steps"]]
    if "tool" not in kinds:
        return bad(f"the trace records no tool step, only {kinds} — the agent answered from memory")
    tool_steps = [s for s in trace["steps"] if s.get("kind") == "tool"]
    if any("args" not in s for s in tool_steps):
        return bad("a tool step in the trace has no args")
    cost = trace["cost_usd"]
    return ok(
        f"{trace['provider']} {trace['model']} {trace['ms']}ms "
        f"{trace['model_calls']} calls {len(trace['steps'])} steps "
        f"{'$' + format(cost, '.6f') if isinstance(cost, (int, float)) else 'cost unknown'}"
    )
