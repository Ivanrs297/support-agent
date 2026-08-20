"""The model providers the agent can run on, and what a call costs.

Two providers, chosen per request rather than per deployment. Groq authenticates
with an API key; Bedrock authenticates with nothing at all — boto3 finds the
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


# Published prices per million tokens, captured 2026-08-13. A snapshot, not a
# source of truth: providers change these, and the number shown in the interface
# is an estimate so an order of magnitude is visible, not an invoice.
PRICES: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Dollars for one exchange, or None when the model is not in the table.

    Returning None rather than zero matters: an unknown price shown as $0.00
    reads as free.
    """
    price = PRICES.get(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def _bedrock_status() -> tuple[bool, str]:
    """Whether Bedrock can be used, and why not when it cannot.

    Two independent things can be missing — the package and the model id — and
    the interface needs to say which, because the fixes are unrelated.
    """
    try:
        import langchain_aws  # noqa: F401
    except ImportError:
        return False, "langchain-aws is not installed"
    if not config.settings.bedrock_model_id:
        return False, "BEDROCK_MODEL_ID is not set"
    return True, f"{config.settings.bedrock_model_id} in {config.settings.aws_region}"


def available_providers() -> list[Provider]:
    """What the interface offers, and what it greys out."""
    bedrock_ok, bedrock_detail = _bedrock_status()
    return [
        Provider(
            name="groq",
            model=config.settings.groq_model,
            available=True,
            detail=config.settings.groq_model,
        ),
        Provider(
            name="bedrock",
            model=config.settings.bedrock_model_id or "",
            available=bedrock_ok,
            detail=bedrock_detail,
        ),
    ]


@lru_cache(maxsize=4)
def _build(provider: str, model: str, temperature: float, timeout: float):
    """Construct a chat model. Cached — building one opens a connection pool.

    Keyed on the settings that shape it rather than on the name alone, so a test
    that swaps settings gets a different client instead of a stale one.
    """
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=model,
            api_key=config.settings.groq_api_key,
            temperature=temperature,
            timeout=timeout,
        )

    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse

        # No credentials passed. boto3 resolves them through its own chain,
        # which on the host means the EC2 instance role and locally means
        # whatever ~/.aws holds. Nothing to put in .env, nothing to rotate.
        return ChatBedrockConverse(
            model=model,
            region_name=config.settings.aws_region,
            temperature=temperature,
        )

    raise ValueError(f"Unknown provider: {provider}")


def chat_model(provider: str):
    """The model for a provider, or a clear error naming what is missing."""
    providers = {p.name: p for p in available_providers()}
    chosen = providers.get(provider)
    if chosen is None:
        raise ValueError(f"Unknown provider '{provider}'. Choose one of {sorted(providers)}.")
    if not chosen.available:
        raise ValueError(f"Provider '{provider}' is not configured: {chosen.detail}.")
    return _build(
        provider,
        chosen.model,
        config.settings.temperature,
        config.settings.request_timeout,
    )
