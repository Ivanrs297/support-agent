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
    model: str
    temperature: float
    max_doc_results: int
    request_timeout: float


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
        model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        temperature=float(os.environ.get("GROQ_TEMPERATURE", "0.2")),
        max_doc_results=int(os.environ.get("MAX_DOC_RESULTS", "3")),
        request_timeout=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60")),
    )


settings = load_settings()
