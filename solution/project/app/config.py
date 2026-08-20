"""Settings, read from the environment once at import time.

Missing configuration fails here rather than on the first request. A container
that starts and only breaks when a user talks to it is worse than one that never
starts: the healthcheck goes green and the deploy looks successful.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    api_token: str
    default_provider: str
    groq_model: str
    bedrock_model_id: str
    aws_region: str
    temperature: float
    max_doc_results: int
    request_timeout: float
    max_token_attempts: int
    lockout_seconds: float
    rate_limit_per_minute: int
    daily_request_cap: int


def _require(name: str) -> str:
    value = _clean(os.environ.get(name, ""))
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def _clean(value: str) -> str:
    """Trim whitespace and surrounding quotes from an environment value.

    Compose's `env_file` is not a shell. `API_TOKEN="abc"` there gives you a
    value with the quote marks in it, and `API_TOKEN=abc ` keeps the trailing
    space. Either one fails a constant-time comparison against the token a
    person actually types, and the only symptom is a login that will not work
    for a reason nothing reports — the secret cannot be printed to compare.

    Quoting a value in a .env file is a habit people bring from shell scripts,
    so this accepts it rather than punishing it.
    """
    value = value.strip()
    for quote in ('"', "'"):
        if len(value) >= 2 and value.startswith(quote) and value.endswith(quote):
            return value[1:-1].strip()
    return value


def load_settings() -> Settings:
    return Settings(
        groq_api_key=_require("GROQ_API_KEY"),
        api_token=_require("API_TOKEN"),
        default_provider=_clean(os.environ.get("DEFAULT_PROVIDER", "groq")),
        # llama-3.1-8b-instant rather than a larger model: it answers this
        # corpus correctly, costs about $0.0001 per exchange, replies in well
        # under a second, and — unlike openai/gpt-oss-120b — calls tools
        # reliably over the streaming endpoint, which is the path the browser
        # client uses. Measured, not assumed.
        groq_model=_clean(os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")),
        # No default model id. Bedrock requires per-model access to be granted
        # in the console, so any id shipped here would be wrong for most
        # accounts — and wrong in a way that only appears at the first question.
        bedrock_model_id=_clean(os.environ.get("BEDROCK_MODEL_ID", "")),
        aws_region=_clean(os.environ.get("AWS_REGION", "us-east-2")),
        temperature=float(os.environ.get("GROQ_TEMPERATURE", "0.2")),
        max_doc_results=int(os.environ.get("MAX_DOC_RESULTS", "3")),
        request_timeout=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60")),
        max_token_attempts=int(os.environ.get("MAX_TOKEN_ATTEMPTS", "5")),
        lockout_seconds=float(os.environ.get("LOCKOUT_SECONDS", "900")),
        rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "10")),
        daily_request_cap=int(os.environ.get("DAILY_REQUEST_CAP", "500")),
    )


settings = load_settings()
