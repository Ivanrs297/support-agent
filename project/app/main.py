"""FastAPI surface for the Hotel Aurora support agent.

STEPS 13 and 14 — see README §13 and §14.

No state. The conversation lives in the request body, so any instance can serve
any turn and the container can be replaced mid-conversation — which is exactly
what a deploy does.

The request and response models below are given to you. They are the contract,
and arguing about field names is not the lesson. The endpoints are yours.
"""

import json
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import agent
from . import config
from .providers import available_providers
from .security import enforce_rate_limit, require_token

app = FastAPI(
    title="Hotel Aurora — Support Agent",
    version="2.0.0",
    description=(
        "A guest support assistant for a fictional hotel, built as a ReAct agent "
        "over two tools: a keyword search across the hotel's documentation, and a "
        "reservation lookup by confirmation code.\n\n"
        "The API keeps no state. Send the full conversation with every request.\n\n"
        "`/chat` and `/chat/stream` require a bearer token: "
        "`Authorization: Bearer <API_TOKEN>`. Five wrong tokens from one address "
        "lock it out temporarily. Accepted requests are rate limited per token "
        "and against a daily cap, because each one spends money at the model "
        "provider.\n\n"
        "A browser interface for all of this is at [/ui](/ui)."
    ),
)

UI_PAGE = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    provider: Literal["groq", "bedrock"] | None = Field(
        default=None,
        description="Which model provider to answer with. Defaults to DEFAULT_PROVIDER.",
    )
    messages: list[Message] = Field(
        min_length=1,
        description="The conversation so far, oldest first. The last message must be from the guest.",
        examples=[
            [{"role": "user", "content": "Can I bring my dog? He's a small one."}]
        ],
    )


class ChatResponse(BaseModel):
    reply: str
    trace: dict = Field(
        description=(
            "What the run cost and how it got there: provider, model, elapsed "
            "milliseconds, token counts, an estimated price, and one entry per "
            "model call and tool call."
        )
    )
    sources: list[str] = Field(
        description=(
            "Tools the agent consulted before answering. An empty list means it "
            "answered without checking the documentation."
        )
    )


class Health(BaseModel):
    status: str
    provider: str
    model: str


class ProviderInfo(BaseModel):
    name: str
    model: str
    available: bool
    detail: str = Field(description="The model in use, or why this provider cannot be used.")


def _unconfigured(error: ValueError) -> HTTPException:
    """A provider the caller asked for that this deployment cannot serve.

    STEP 25.7
    400, not 500. Nothing broke — the request named something that is not
    configured here. Put the reason in the detail so the caller can fix it
    without reading the server's logs.
    """
    raise NotImplementedError("STEP 25.7 — see README §25")


@app.get("/health", response_model=Health, tags=["ops"])
def health() -> Health:
    """Liveness probe. Used by the container healthcheck, so keep it cheap.

    STEP 13.2
    Cheap means no model call, no disk read, no authentication. This endpoint is
    hit every 30 seconds forever, and it is what the deploy's health gate reads.
    """
    raise NotImplementedError("STEP 13.2 — see README §13")


@app.get(
    "/session",
    tags=["ops"],
    dependencies=[Depends(require_token)],
    summary="Check a token without spending anything",
)
def session() -> dict[str, str | int]:
    """Validate a token. Costs no model call and no quota.

    STEP 24.1
    Note the dependency: `require_token`, not `enforce_rate_limit`. The browser
    client calls this before it will accept a question, so a mistyped token
    fails immediately rather than after a round trip to the model — and opening
    the page costs nobody their quota.

    Return the limits too, so the interface can display them without a second
    endpoint.
    """
    raise NotImplementedError("STEP 24.1 — see README §24")


@app.get("/providers", response_model=list[ProviderInfo], tags=["ops"])
def providers() -> list[ProviderInfo]:
    """Which providers this deployment can answer with, and why not the others.

    STEP 25.8
    Unauthenticated on purpose: it exposes model names and configuration state,
    not secrets, and the browser client needs it to render the provider switch
    before anyone has typed a token.
    """
    raise NotImplementedError("STEP 25.8 — see README §25")


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def ui() -> HTMLResponse:
    """The browser client. One file, no build step, no second deployment.

    STEP 24.2
    """
    raise NotImplementedError("STEP 24.2 — see README §24")


@app.post(
    "/chat",
    response_model=ChatResponse,
    tags=["chat"],
    dependencies=[Depends(enforce_rate_limit)],
)
async def chat(request: ChatRequest) -> ChatResponse:
    """Answer a guest question and return the complete reply.

    STEP 13.3
    Call agent.run. Catch ValueError — the vocabulary the agent raises in for an
    unusable provider — and turn it into the 400 from `_unconfigured`. Let
    anything else become a 500, because anything else is a real fault.
    """
    raise NotImplementedError("STEP 13.3 — see README §13")


@app.post("/chat/stream", tags=["chat"], dependencies=[Depends(enforce_rate_limit)])
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Answer a guest question, streaming the reply as server-sent events.

    Each event carries a JSON object. `{"token": "..."}` while the reply is being
    written, then `{"sources": [...]}` naming the tools it came from, then
    `{"trace": {...}}` with timings, token counts and every step, then
    `{"done": true}`. Tool calls happen before the first token, so expect a pause
    at the start rather than a stall mid-sentence.
    """
    # STEP 14.2 — and this is the step people get wrong.
    #
    # Two failures live here and they need opposite treatment, because the 200
    # and its headers leave before the first token does.
    #
    # BEFORE the response starts, you can still return a status code. Resolve
    # the provider here — `agent._agent_for(...)` — so that asking for one this
    # deployment cannot serve is a 400 like anywhere else. Discovering it
    # mid-stream would mean a 200 whose body reports a failure, which is the
    # shape nothing handles well.
    #
    # AFTER it starts, you cannot. An exception inside the generator just ends
    # the stream, and every cause — an invalid model id, a revoked credential,
    # a provider outage — reaches the browser looking identical to a dropped
    # connection. Catch it and yield {"error": "<Type>: <message>"} so the page
    # can say what happened. Then yield {"done": true} either way, so the client
    # has one place to stop listening.
    #
    # Set `Cache-Control: no-cache` and `X-Accel-Buffering: no` on the response,
    # or a proxy will buffer the whole stream and hand it over at once — which
    # looks exactly like a slow model and is impossible to tell apart from one.
    raise NotImplementedError("STEP 14.2 — see README §14")
