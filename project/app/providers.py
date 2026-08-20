"""The model providers the agent can run on, and what a call costs.

STEP 25 — see README §25.

Two providers, chosen per request rather than per deployment. Groq authenticates
with an API key; Bedrock can authenticate with nothing at all — boto3 finds the
instance role through the metadata service, the same way the SSM agent does.
That asymmetry is the interesting part: one provider needs a secret in a file,
the other needs the machine to be who it says it is.
"""

from dataclasses import dataclass
from functools import lru_cache

from . import config


@dataclass(frozen=True)
class Provider:
    name: str
    model: str
    available: bool
    detail: str


# STEP 25.1 — published prices per million tokens, as (input, output).
#
# A snapshot, not a source of truth: providers change these, and the number the
# interface shows is an estimate so an order of magnitude is visible, not an
# invoice. Date the snapshot in a comment. An undated price table is a lie with
# a timestamp missing.
PRICES: dict[str, tuple[float, float]] = {}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Dollars for one exchange, or None when the model is not in the table.

    STEP 25.2
    Return None rather than zero for an unknown model. An unknown price shown as
    $0.00 reads as free, and that is the one wrong answer a cost display must
    never give.
    """
    raise NotImplementedError("STEP 25.2 — see README §25")


def _bedrock_status() -> tuple[bool, str]:
    """Whether Bedrock can be used, and why not when it cannot.

    STEP 25.3
    Two independent things can be missing — the `langchain-aws` package and
    BEDROCK_MODEL_ID — and the interface has to say which, because the fixes are
    unrelated. Import inside the function: an ImportError at module scope would
    take the whole app down over an optional provider.
    """
    raise NotImplementedError("STEP 25.3 — see README §25")


def available_providers() -> list[Provider]:
    """What the interface offers, and what it greys out.

    STEP 25.4
    Groq is always available: its key is required at import, so if the app is
    running, Groq works. Bedrock reports whatever _bedrock_status found.
    """
    raise NotImplementedError("STEP 25.4 — see README §25")


@lru_cache(maxsize=4)
def _build(provider: str, model: str, temperature: float, timeout: float):
    """Construct a chat model. Cached — building one opens a connection pool.

    STEP 25.5
    Keyed on the settings that shape it rather than on the name alone, so a test
    that swaps settings gets a different client instead of a stale one.

    Groq needs its key passed in. Bedrock gets no credentials at all: boto3
    resolves them through its own chain, which on the host means the EC2
    instance role and locally means whatever ~/.aws holds. Nothing to put in
    .env, nothing to rotate. Passing credentials here would throw that away.
    """
    raise NotImplementedError("STEP 25.5 — see README §25")


def chat_model(provider: str):
    """The model for a provider, or a clear error naming what is missing.

    STEP 25.6
    """
    raise NotImplementedError("STEP 25.6 — see README §25")
