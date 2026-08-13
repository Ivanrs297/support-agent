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
    model: str
    temperature: float
    max_doc_results: int
    request_timeout: float
    max_token_attempts: int
    lockout_seconds: float
    rate_limit_per_minute: int
    daily_request_cap: int


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def load_settings() -> Settings:
    return Settings(
        groq_api_key=_require("GROQ_API_KEY"),
        api_token=_require("API_TOKEN"),
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        temperature=float(os.environ.get("GROQ_TEMPERATURE", "0.2")),
        max_doc_results=int(os.environ.get("MAX_DOC_RESULTS", "3")),
        request_timeout=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60")),
        max_token_attempts=int(os.environ.get("MAX_TOKEN_ATTEMPTS", "5")),
        lockout_seconds=float(os.environ.get("LOCKOUT_SECONDS", "900")),
        rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "10")),
        daily_request_cap=int(os.environ.get("DAILY_REQUEST_CAP", "500")),
    )


settings = load_settings()
