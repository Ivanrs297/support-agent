"""Settings, read from the environment once at import time.

STEP 7 — see README §7.

Missing configuration has to fail here rather than on the first request. A
container that starts and only breaks when a guest talks to it is worse than one
that never starts: the healthcheck goes green and the deploy looks successful.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Everything the app reads from the environment.

    Frozen on purpose. Settings that can be mutated at runtime turn "why is the
    rate limit different in production" into an unanswerable question.
    """

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
    """Read a variable that has no sensible default, or fail loudly.

    STEP 7.1
    Raise RuntimeError naming the variable and telling the reader how to fix it.
    """
    raise NotImplementedError("STEP 7.1 — see README §7")


def _clean(value: str) -> str:
    """Trim whitespace and surrounding quotes from an environment value.

    STEP 7.2
    Compose's `env_file` is not a shell. `API_TOKEN="abc"` there gives you a
    value with the quote marks still in it, and `API_TOKEN=abc ` keeps the
    trailing space. Either one fails a constant-time comparison against the
    token a person actually types, and the only symptom is a login that will
    not work for a reason nothing reports — you cannot print the secret to
    compare it.

    Quoting a value in a .env file is a habit people bring from shell scripts.
    Accept it rather than punishing it. Strip both `"` and `'`, and only when
    the value both starts and ends with the same one.
    """
    raise NotImplementedError("STEP 7.2 — see README §7")


def load_settings() -> Settings:
    """Build the Settings object from os.environ.

    STEP 7.3
    Two variables are required — GROQ_API_KEY and API_TOKEN. Everything else
    has a default. Two of those defaults are decisions rather than conveniences,
    and README §7 explains why:

    - GROQ_MODEL: pick the small model, not the impressive one.
    - BEDROCK_MODEL_ID: no default at all.

    Run `_clean` over every string you read, not just the required ones.
    """
    raise NotImplementedError("STEP 7.3 — see README §7")


settings = load_settings()
