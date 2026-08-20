"""Who may call the agent, and how often.

STEPS 21, 22 and 23 — see README §21, §22 and §23.

Three separate concerns, deliberately kept apart because they fail differently:

- **Authentication.** A shared bearer token. One secret, compared in constant
  time, and nothing else.
- **Brute force.** Repeated wrong tokens from one address stop being answered.
- **Rate limiting.** The agent spends money on every request it accepts. A per
  token window keeps one caller from crowding out the rest, and a daily cap is
  the one that protects the bill.

All state is in memory. That is a real limitation and it is the right trade
here: the container is stateless by design, so a deploy resets every counter,
and this works only because exactly one container with one worker serves the
site. Two workers would mean two independent sets of counters and limits that
are silently double what they claim. Externalising the state means a database
this host has no room for.
"""

import time
from collections import defaultdict, deque
from secrets import compare_digest

from fastapi import HTTPException, Request, status

from . import config

# Read settings through the module rather than binding the object. Settings is
# frozen, so a test that needs different limits replaces the whole instance;
# binding `settings` here would keep this module pointed at the original.

# STEP 22.1 — failed attempts and lockouts, keyed by client address.
_failures: dict[str, int] = defaultdict(int)
_locked_until: dict[str, float] = {}

# STEP 23.1 — request timestamps, for the two rate limit windows.
_token_calls: dict[str, deque[float]] = defaultdict(deque)
_daily_calls: deque[float] = deque()

DAY_SECONDS = 86_400


def client_address(request: Request) -> str:
    """The caller's address, as seen through Caddy.

    STEP 22.2
    `X-Forwarded-For` is trustworthy here only because nothing else can reach
    this process: the api service publishes no ports, so the only path in is
    through the reverse proxy on the same Docker network. Expose the port
    directly and this header becomes attacker-controlled, and with it the
    lockout key — five forged headers and anyone can lock out anyone.

    The first entry is the original client; the rest are proxies. Fall back to
    request.client.host when the header is absent, which is what happens when
    you run this locally without Caddy in front.

    Verify the assumption rather than trusting it. README §22 shows how to check
    whether your proxy replaces this header or appends to it — if it appends,
    the first entry is whatever the caller claimed and this is worthless.
    """
    raise NotImplementedError("STEP 22.2 — see README §22")


def _bearer_token(request: Request) -> str | None:
    """Pull the token out of `Authorization: Bearer <token>`, or None.

    STEP 21.1
    """
    raise NotImplementedError("STEP 21.1 — see README §21")


def _prune(timestamps: deque[float], window: float, now: float) -> None:
    """Drop timestamps that have fallen out of the window.

    STEP 23.2
    A deque and this three-line function are the whole sliding window. Anything
    fancier here is a dependency you have to justify at 512 MiB.
    """
    raise NotImplementedError("STEP 23.2 — see README §23")


def _too_many_requests(detail: str, retry_after: int) -> HTTPException:
    """A 429 that says when to come back.

    STEP 23.3
    A 429 without `Retry-After` tells a client it failed but not what to do
    about it, so the only strategy left is to retry immediately and make it
    worse.
    """
    raise NotImplementedError("STEP 23.3 — see README §23")


def check_lockout(address: str, now: float) -> None:
    """Refuse an address that has already failed too many times.

    STEP 22.3
    When a lockout has expired, clear both the lockout and the failure count so
    a later mistake starts from zero. Leave the count behind and the sixth typo
    of someone's life locks them out instantly.
    """
    raise NotImplementedError("STEP 22.3 — see README §22")


def register_failure(address: str, now: float) -> None:
    """Count a wrong token, and lock the address at the limit.

    STEP 22.4
    Keyed on the address, not the token. Locking the token would hand any
    stranger a denial of service for the price of five bad requests.
    """
    raise NotImplementedError("STEP 22.4 — see README §22")


def register_success(address: str) -> None:
    """A correct token clears the record.

    STEP 22.5
    Otherwise four typos followed by months of correct use still end in a
    lockout, which punishes the one caller proven to hold the token.
    """
    raise NotImplementedError("STEP 22.5 — see README §22")


def check_rate_limits(token: str, now: float) -> None:
    """Enforce the per-token window first, then the daily cap.

    STEP 23.4
    Order matters for the message the caller gets. Being told to slow down is
    actionable; being told the day's budget is gone is not, and the second
    should only be said when it is true.

    Record the call against both windows only after both have passed. Charge
    first and a rejected request still spends quota.
    """
    raise NotImplementedError("STEP 23.4 — see README §23")


async def require_token(request: Request) -> str:
    """Authenticate the caller. Returns the token, for the rate limiter to key on.

    STEP 21.2
    Compare with `compare_digest`, not `==`. String equality returns early at
    the first differing byte, and the time it takes leaks the prefix.

    Check the lockout BEFORE checking the token, or a locked-out address still
    gets a free guess on every request.

    Say how many attempts are left in the 401 detail. That is not a leak worth
    worrying about — the limit is published in the docs — and without it the
    interface can only tell someone they are locked out after it has happened.

    `/health` is deliberately not guarded: the container healthcheck calls it,
    and a healthcheck that needs a secret is a healthcheck that fails for the
    wrong reasons.
    """
    raise NotImplementedError("STEP 21.2 — see README §21")


async def enforce_rate_limit(request: Request) -> None:
    """Charge a request against the windows. Only on endpoints that spend money.

    STEP 23.5
    Kept separate from authentication so that checking a token costs nothing.
    The interface validates a token before it will let anyone type, and that
    check must not consume anybody's quota.
    """
    raise NotImplementedError("STEP 23.5 — see README §23")


def reset_state() -> None:
    """Clear every counter. For tests, which must not leak state into each other.

    STEP 23.6
    Module-level mutable state is the price of not having a database. This
    function is the receipt.
    """
    raise NotImplementedError("STEP 23.6 — see README §23")
