"""FastAPI surface for the Hotel Aurora support agent.

Three endpoints, no state. The conversation lives in the request body, so any
instance can serve any turn and the container can be replaced mid-conversation —
which is exactly what a deploy does.
"""

import json
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import agent
from .config import settings

app = FastAPI(
    title="Hotel Aurora — Support Agent",
    version="2.0.0",
    description=(
        "A guest support assistant for a fictional hotel, built as a ReAct agent "
        "over two tools: a keyword search across the hotel's documentation, and a "
        "reservation lookup by confirmation code.\n\n"
        "The API keeps no state. Send the full conversation with every request."
    ),
)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(
        min_length=1,
        description="The conversation so far, oldest first. The last message must be from the guest.",
        examples=[
            [{"role": "user", "content": "Can I bring my dog? He's a small one."}]
        ],
    )


class ChatResponse(BaseModel):
    reply: str
    sources: list[str] = Field(
        description=(
            "Tools the agent consulted before answering. An empty list means it "
            "answered without checking the documentation."
        )
    )


class Health(BaseModel):
    status: str
    model: str


@app.get("/health", response_model=Health, tags=["ops"])
def health() -> Health:
    """Liveness probe. Used by the container healthcheck, so keep it cheap."""
    return Health(status="ok", model=settings.model)


@app.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(request: ChatRequest) -> ChatResponse:
    """Answer a guest question and return the complete reply."""
    result = await agent.run([m.model_dump() for m in request.messages])
    return ChatResponse(**result)


@app.post("/chat/stream", tags=["chat"])
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Answer a guest question, streaming the reply as server-sent events.

    Each event carries a JSON object: `{"token": "..."}` while the reply is being
    written, then a final `{"done": true}`. Tool calls happen before the first
    token, so expect a pause at the start rather than a stall mid-sentence.
    """
    messages = [m.model_dump() for m in request.messages]

    async def events():
        async for token in agent.stream(messages):
            yield f"data: {json.dumps({'token': token})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
