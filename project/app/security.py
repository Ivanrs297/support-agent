"""Who may call the agent, and how often.

Three separate concerns, deliberately kept apart because they fail differently:

- **Authentication.** A shared bearer token. One secret, compared in constant
  time, and nothing else.
- **Brute force.** Repeated wrong tokens from one address stop being answered.
- **Rate limiting.** The agent spends money on every request it accepts. A per
  token window keeps one caller from crowding out the rest, and a daily cap is
  the one that protects the bill.

All state is in memory. That is a real limitation and it is the right trade here:
the container is stateless by design, so a deploy resets every counter, and this
works only because exactly one container with one worker serves the site. Two
workers would mean two independent sets of counters and limits that are silently
double what they claim. Externalising the state means a database this host has no
room for.
"""

import time
from collections import defaultdict, deque
from secrets import compare_digest

from fastapi import HTTPException, Request, status

from . import config

# Read through the module rather than binding the object: Settings is frozen, so
# a test that needs different limits replaces the whole instance. Binding
# `settings` here would keep this module pointed at the original.

# Failed attempts and lockouts, keyed by client address.
_failures: dict[str, int] = defaultdict(int)
_locked_until: dict[str, float] = {}

# Request timestamps, for the two rate limit windows.
_token_calls: dict[str, deque[float]] = defaultdict(deque)
_daily_calls: deque[float] = deque()

DAY_SECONDS = 86_400


def client_address(request: Request) -> str:
    """The caller's address, as seen through Caddy.

    `X-Forwarded-For` is trusted here because nothing else can reach this
    process: the api service publishes no ports, so the only path in is through
    the reverse proxy on the same Docker network. Expose the port directly and
    this header becomes attacker-controlled, and with it the lockout key.

    The first entry is the original client; the rest are proxies.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value.strip()


def _prune(timestamps: deque[float], window: float, now: float) -> None:
    while timestamps and now - timestamps[0] >= window:
        timestamps.popleft()


def _too_many_requests(detail: str, retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers={"Retry-After": str(max(1, retry_after))},
    )


def check_lockout(address: str, now: float) -> None:
    """Refuse an address that has already failed too many times."""
    until = _locked_until.get(address)
    if until is None:
        return
    if now >= until:
        # Expired. Clear it so a later failure starts from zero.
        del _locked_until[address]
        _failures.pop(address, None)
        return
    raise _too_many_requests(
        "Too many failed attempts. Try again later.", int(until - now)
    )


def register_failure(address: str, now: float) -> None:
    _failures[address] += 1
    if _failures[address] >= config.settings.max_token_attempts:
        _locked_until[address] = now + config.settings.lockout_seconds


def register_success(address: str) -> None:
    """A correct token clears the record.

    Otherwise four typos followed by months of correct use would still end in a
    lockout, which punishes the one caller proven to hold the token.
    """
    _failures.pop(address, None)
    _locked_until.pop(address, None)


def check_rate_limits(token: str, now: float) -> None:
    """Enforce the per-token window first, then the daily cap.

    Order matters for the message the caller gets: being told to slow down is
    actionable, being told the day's budget is gone is not, and the second
    should only be said when it is true.
    """
    calls = _token_calls[token]
    _prune(calls, 60.0, now)
    if len(calls) >= config.settings.rate_limit_per_minute:
        raise _too_many_requests(
            f"Rate limit: {config.settings.rate_limit_per_minute} requests per minute.",
            int(60 - (now - calls[0])),
        )

    _prune(_daily_calls, DAY_SECONDS, now)
    if len(_daily_calls) >= config.settings.daily_request_cap:
        raise _too_many_requests(
            "The daily request budget for this deployment is spent.",
            int(DAY_SECONDS - (now - _daily_calls[0])),
        )

    calls.append(now)
    _daily_calls.append(now)


async def require_token(request: Request) -> str:
    """Authenticate the caller. Returns the token, for the rate limiter to key on.

    `/health` is deliberately not guarded: the container healthcheck calls it,
    and a healthcheck that needs a secret is a healthcheck that fails for the
    wrong reasons.
    """
    address = client_address(request)
    now = time.monotonic()

    check_lockout(address, now)

    token = _bearer_token(request)
    if token is None or not compare_digest(token, config.settings.api_token):
        register_failure(address, now)
        remaining = max(0, config.settings.max_token_attempts - _failures[address])
        # Saying how many attempts are left is not a leak worth worrying about
        # — the limit is published in the docs — and without it the interface
        # can only tell someone they are locked out after it has happened.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"Invalid token. {remaining} attempt{'' if remaining == 1 else 's'} "
                "left before this address is locked out."
                if remaining
                else "Invalid token. This address is now locked out."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    register_success(address)
    return token


async def enforce_rate_limit(request: Request) -> None:
    """Charge a request against the windows. Only on endpoints that call Groq.

    Kept separate from authentication so that checking a token costs nothing.
    The interface validates a token before it will let anyone type, and that
    check must not consume anybody's quota.
    """
    token = await require_token(request)
    check_rate_limits(token, time.monotonic())


def reset_state() -> None:
    """Clear every counter. For tests, which must not leak state into each other."""
    _failures.clear()
    _locked_until.clear()
    _token_calls.clear()
    _daily_calls.clear()
