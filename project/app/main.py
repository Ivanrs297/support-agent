"""FastAPI surface for the Hotel Aurora support agent.

Three endpoints, no state. The conversation lives in the request body, so any
instance can serve any turn and the container can be replaced mid-conversation —
which is exactly what a deploy does.
"""

import json
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI
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


@app.get("/health", response_model=Health, tags=["ops"])
def health() -> Health:
    """Liveness probe. Used by the container healthcheck, so keep it cheap."""
    return Health(
        status="ok",
        provider=config.settings.default_provider,
        model=config.settings.groq_model,
    )


@app.get(
    "/session",
    tags=["ops"],
    dependencies=[Depends(require_token)],
    summary="Check a token without spending anything",
)
def session() -> dict[str, str | int]:
    """Validate a token. Costs no model call and no quota.

    The browser client calls this before it will accept a question, so that a
    mistyped token fails immediately rather than after a round trip to Groq.
    """
    return {
        "status": "authenticated",
        "requests_per_minute": config.settings.rate_limit_per_minute,
        "daily_cap": config.settings.daily_request_cap,
    }


@app.get("/providers", response_model=list[ProviderInfo], tags=["ops"])
def providers() -> list[ProviderInfo]:
    """Which providers this deployment can answer with, and why not the others.

    Unauthenticated on purpose: it exposes model names and configuration state,
    not secrets, and the browser client needs it to render the switch before
    anyone has typed a token.
    """
    return [ProviderInfo(**p.__dict__) for p in available_providers()]


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def ui() -> HTMLResponse:
    """The browser client. One file, no build step, no second deployment."""
    return HTMLResponse(UI_PAGE)


@app.post(
    "/chat",
    response_model=ChatResponse,
    tags=["chat"],
    dependencies=[Depends(enforce_rate_limit)],
)
async def chat(request: ChatRequest) -> ChatResponse:
    """Answer a guest question and return the complete reply."""
    result = await agent.run(
        [m.model_dump() for m in request.messages if m.role != "system"],
        provider=request.provider,
    )
    return ChatResponse(**result)


@app.post("/chat/stream", tags=["chat"], dependencies=[Depends(enforce_rate_limit)])
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Answer a guest question, streaming the reply as server-sent events.

    Each event carries a JSON object. `{"token": "..."}` while the reply is being
    written, then `{"sources": [...]}` naming the tools it came from, then
    `{"trace": {...}}` with timings, token counts and every step, then
    `{"done": true}`. Tool calls happen before the first token, so expect a pause
    at the start rather than a stall mid-sentence.
    """
    messages = [m.model_dump() for m in request.messages]

    async def events():
        async for event in agent.stream(messages, provider=request.provider):
            yield f"data: {json.dumps(event)}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
