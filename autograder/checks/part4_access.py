"""Steps 21-24 — who may call the agent, how often, and what they see.

Two checks here have consequences, so neither runs unless you ask for it:

- The lockout check sends five wrong tokens on purpose, which locks the grading
  machine's address out of the deployment for the configured window. It is
  registered last for that reason, and a student who forgets and runs the
  grader twice will be blocked by their own first run.
- The rate limit check spends the daily budget it is measuring.

`--destructive` turns both on. The default report says clearly that it skipped
them rather than quietly passing.
"""

import re
import time

from ..registry import LIVE, LOCAL, STATIC, Context, Result, bad, body_of, check, is_stub, ok, skip


def _needs_url(ctx: Context) -> Result | None:
    if not ctx.base_url:
        return skip("no deployment configured — set GRADE_BASE_URL")
    if not ctx.token:
        return skip("no token configured — set GRADE_API_TOKEN")
    return None


# --------------------------------------------------------------------------
# 21. The token
# --------------------------------------------------------------------------


@check(21, "authentication, in the student's own suite", LOCAL)
def token_suite(ctx: Context) -> Result:
    code, summary = ctx.pytest("tests/test_security.py", "token or auth")
    return ok(summary) if code == 0 else bad(summary)


@check(21, "the token is compared in constant time", STATIC)
def constant_time(ctx: Context) -> Result:
    text = ctx.read("project/app/security.py") or ""
    # The import sits at the top of the work area's stub, so searching the whole
    # file passes before a line has been written. Look inside the function that
    # has to use it.
    if is_stub(text, "require_token"):
        return bad("require_token is not written yet")
    body = body_of(text, "require_token") or ""
    if "compare_digest" not in body:
        return bad(
            "require_token does not compare with compare_digest. String equality "
            "returns early at the first differing byte, and the time it takes leaks "
            "the prefix"
        )
    return ok("compare_digest, inside require_token")


@check(21, "the deployed /chat refuses a request with no token", LIVE)
def refuses_anonymous(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    response = ctx.request(
        "/chat",
        method="POST",
        payload={"messages": [{"role": "user", "content": "hi"}]},
        token=None,
    )
    if response.status != 401:
        return bad(
            f"an unauthenticated POST /chat returned {response.status}, not 401 — "
            "the agent is answering anyone who knows the URL, at your expense"
        )
    return ok("401")


@check(21, "a wrong token is refused, and says how many tries are left", LIVE)
def refuses_wrong_token(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    # One wrong attempt only. The lockout is five, so this stays well clear of
    # it — and a correct token afterwards clears the count anyway, which the
    # next check relies on.
    response = ctx.request(
        "/chat",
        method="POST",
        payload={"messages": [{"role": "user", "content": "hi"}]},
        token="definitely-not-the-token",
    )
    if response.status != 401:
        return bad(f"a wrong token returned {response.status}, not 401")
    detail = (response.json() or {}).get("detail", "")
    if not re.search(r"\d", detail):
        return bad(
            f"the 401 says {detail!r} and never mentions attempts remaining. Without "
            "it the interface can only tell someone they are locked out after it has happened"
        )
    return ok(detail[:90])


@check(21, "a correct token clears the failure count", LIVE)
def success_clears(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    # /session, not /chat: this has to be provable without spending a model call.
    response = ctx.request("/session")
    if response.status != 200:
        return bad(
            f"/session with the configured token returned {response.status}. Either "
            "the token is wrong, or the previous wrong attempt was not cleared — "
            "otherwise four typos followed by months of correct use still end in a lockout"
        )
    return ok("the record was cleared by a correct token")


# --------------------------------------------------------------------------
# 22. The lockout
# --------------------------------------------------------------------------


@check(22, "lockout behaviour, in the student's own suite", LOCAL)
def lockout_suite(ctx: Context) -> Result:
    code, summary = ctx.pytest("tests/test_security.py", "lockout")
    return ok(summary) if code == 0 else bad(summary)


@check(22, "the lockout is keyed on the address, not the token", STATIC)
def keyed_on_address(ctx: Context) -> Result:
    text = ctx.read("project/app/security.py") or ""
    if "client_address" not in text:
        return bad("there is no client_address; nothing identifies who is failing")
    body = re.search(r"def register_failure\(.*?\n(?:.*?\n)*?(?=\ndef |\Z)", text)
    if body and re.search(r"\btoken\b", body.group(0)):
        return bad(
            "register_failure looks at the token. Lock the token and any stranger "
            "takes the system down for everyone with five bad requests"
        )
    return ok("keyed on the caller's address")


@check(22, "five wrong tokens lock the address out", LIVE, destructive=True)
def live_lockout(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    if not ctx.include_destructive:
        return skip("would lock this address out for the configured window — pass --destructive")

    attempts = 0
    for attempts in range(1, 9):
        response = ctx.request(
            "/session", token=f"wrong-token-{attempts}", timeout=20
        )
        if response.status == 429:
            retry = response.headers.get("retry-after")
            if not retry:
                return bad(
                    f"locked out after {attempts} attempts but sent no Retry-After. "
                    "A 429 without it tells a client it failed and not what to do "
                    "about it, so the only strategy left is to retry immediately"
                )
            return ok(f"locked after {attempts} attempts, Retry-After: {retry}s")
        if response.status != 401:
            return bad(f"attempt {attempts} returned {response.status}, expected 401 or 429")
        time.sleep(0.3)
    return bad(f"{attempts} wrong tokens and still no lockout")


# --------------------------------------------------------------------------
# 23. The rate limits
# --------------------------------------------------------------------------


@check(23, "both windows, in the student's own suite", LOCAL)
def rate_suite(ctx: Context) -> Result:
    code, summary = ctx.pytest("tests/test_security.py", "rate or limit or cap or daily")
    return ok(summary) if code == 0 else bad(summary)


@check(23, "checking a token costs no quota", STATIC)
def session_is_free(ctx: Context) -> Result:
    text = ctx.read("project/app/main.py") or ""
    # The decorator is given in the work area, so checking it alone passes
    # against code nobody has written. Require the endpoint itself first.
    if is_stub(text, "session"):
        return bad("the /session endpoint is not written yet")
    session = re.search(r'@app\.get\(\s*\n?\s*"/session"(?:.*?\n)*?\)', text)
    if session is None:
        return skip("could not isolate the /session route decorator")
    if "enforce_rate_limit" in session.group(0):
        return bad(
            "/session depends on enforce_rate_limit, so merely opening the page "
            "spends the caller's quota. It should depend on require_token alone"
        )
    if "require_token" not in session.group(0):
        return bad("/session does not authenticate at all")
    return ok("require_token, without the rate limiter")


@check(23, "the per-minute window is enforced on the deployment", LIVE, destructive=True)
def live_rate_limit(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    if not ctx.include_destructive:
        return skip("would spend the daily budget it measures — pass --destructive")

    session = ctx.request("/session")
    limit = (session.json() or {}).get("requests_per_minute")
    if not isinstance(limit, int):
        return skip("/session does not report requests_per_minute, so there is no limit to test against")

    for attempt in range(1, limit + 4):
        response = ctx.request(
            "/chat",
            method="POST",
            payload={"messages": [{"role": "user", "content": "hi"}]},
            timeout=90,
        )
        if response.status == 429:
            if not response.headers.get("retry-after"):
                return bad(f"429 after {attempt} requests, but no Retry-After header")
            return ok(f"429 after {attempt} requests, limit is {limit}/min")
    return bad(f"sent {limit + 3} requests against a limit of {limit}/min and was never refused")


# --------------------------------------------------------------------------
# 24. The interface
# --------------------------------------------------------------------------


@check(24, "/ui serves the browser client", LIVE)
def ui(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    response = ctx.request("/ui", token=None)
    if response.status != 200:
        return bad(f"GET /ui returned {response.status}")
    if "<" not in response.body or len(response.body) < 500:
        return bad(f"/ui returned {len(response.body)} bytes that do not look like a page")
    return ok(f"{len(response.body) // 1024} KB of HTML, no build step")


@check(24, "/session validates a token and reports the limits", LIVE)
def session(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    response = ctx.request("/session")
    if response.status != 200:
        return bad(f"/session with a valid token returned {response.status}")
    body = response.json() or {}
    missing = [k for k in ("requests_per_minute", "daily_cap") if k not in body]
    if missing:
        return bad(f"/session does not report {', '.join(missing)}, so the interface has to guess or ask again")
    return ok(f"{body['requests_per_minute']}/min, {body['daily_cap']}/day")


@check(24, "the interface says when an answer had nothing behind it", STATIC)
def names_the_failure(ctx: Context) -> Result:
    text = ctx.read("project/app/static/index.html") or ""
    if "not implemented" in text.lower():
        return bad("the interface still has unimplemented functions")
    if not re.search(r"without checking|no sources|answered from memory", text, re.IGNORECASE):
        return bad(
            "nothing in the page says an answer had no sources. An answer with "
            "nothing behind it is the failure that matters in a support agent, and "
            "it is invisible unless the interface insists on showing it"
        )
    return ok("the empty-sources case is named in the page")
