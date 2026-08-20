"""The support agent: a ReAct loop over the hotel tools, on Groq or Bedrock.

Everything the agent knows about Hotel Aurora comes from tool results. It is not
told the policies in its prompt, and that is deliberate — a prompt that carries
the knowledge base would answer confidently from memory and drift from the
documentation the moment either changes.

Every run is traced. Not because tracing is good hygiene in the abstract, but
because an agent's answer and an agent's reasoning fail independently: a
correct-sounding sentence produced without consulting anything is the failure
this project is about, and it is invisible unless each step is recorded.
"""

import time
from collections.abc import AsyncIterator
from functools import lru_cache

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from . import config
from .providers import available_providers, chat_model, estimate_cost
from .tools import TOOLS

SYSTEM_PROMPT = """\
You are the guest support assistant for Hotel Aurora, a 120-room hotel.

Answer only from what the tools return. You have two:

- `search_hotel_policies` for anything about rooms, rates, reservations, \
check-in, amenities, dining, or hotel policies.
- `get_reservation` to look up an existing booking by its confirmation code \
(format AUR-104582).

Rules you do not break:

1. Call a tool before answering any question about the hotel. Do not answer from \
memory, even when you are confident, and even for something as ordinary as \
check-out time.
2. If the tools return nothing relevant, say so plainly: you do not have that \
information, and a colleague at +1 555 0100 or stay@hotelaurora.example can \
help. Never fill a gap with a plausible-sounding invention. A wrong pet fee or \
an imagined airport shuttle is worse than admitting you do not know.
3. Never state reservation details that did not come from `get_reservation`.
4. You cannot take payments, issue refunds, or make changes to a booking. You \
can explain the policy and look the booking up, then hand the guest to a human \
for the change itself.
5. Reply in the language the guest wrote in, but always query the tools in \
English — the documentation is in English.

Be brief and concrete. Guests want the answer, not a paragraph around it.\
"""


@lru_cache(maxsize=4)
def build_agent(provider: str, model: str):
    """One compiled graph per provider. Cached — compiling is not free.

    `model` is in the cache key although it is derived from the provider:
    changing the configured model has to produce a different agent, and keying
    on the provider name alone would silently keep serving the old one.
    """
    return create_agent(
        model=chat_model(provider),
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )


def _agent_for(provider: str | None):
    name = provider or config.settings.default_provider
    chosen = next((p for p in available_providers() if p.name == name), None)
    if chosen is None:
        raise ValueError(f"Unknown provider '{name}'.")
    if not chosen.available:
        raise ValueError(f"Provider '{name}' is not configured: {chosen.detail}.")
    return name, chosen.model, build_agent(name, chosen.model)


def _trace_from(messages: list[BaseMessage], provider: str, model: str, ms: int) -> dict:
    """Rebuild what happened from the messages the run produced.

    Derived from the final message list rather than observed live, because the
    two paths that need a trace — a blocking answer and a streamed one — share
    only their output. Reconstruction gives both the same shape, at the cost of
    per-step timings: the total is measured, the split between steps is not.
    """
    steps: list[dict] = []
    pending: dict[str, dict] = {}
    input_tokens = output_tokens = 0

    for message in messages:
        if isinstance(message, AIMessage):
            usage = message.usage_metadata or {}
            input_tokens += usage.get("input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)
            for call in message.tool_calls:
                pending[call["id"]] = call["args"]
            steps.append(
                {
                    "kind": "model",
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    # No calls on the last step is the normal end of the loop.
                    # No calls on the *only* step means the model answered
                    # without looking anything up — the failure worth seeing.
                    "tool_calls": [call["name"] for call in message.tool_calls],
                }
            )
        elif isinstance(message, ToolMessage):
            steps.append(
                {
                    "kind": "tool",
                    "name": message.name,
                    "args": pending.get(message.tool_call_id, {}),
                    "result_chars": len(str(message.content)),
                }
            )

    return {
        "provider": provider,
        "model": model,
        "ms": ms,
        "model_calls": sum(step["kind"] == "model" for step in steps),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": estimate_cost(model, input_tokens, output_tokens),
        "steps": steps,
    }


def _sources(steps: list[dict]) -> list[str]:
    """Which tools the agent actually consulted, in call order.

    An answer whose `sources` list is empty is the agent talking from memory,
    which the prompt forbids — surfacing it makes that visible instead of
    invisible.
    """
    seen: list[str] = []
    for step in steps:
        if step["kind"] == "tool" and step["name"] not in seen:
            seen.append(step["name"])
    return seen


async def run(messages: list[dict], provider: str | None = None) -> dict:
    """Answer a conversation and return the reply, its sources, and the trace.

    Deliberately not streamed. Groq's tool calling is measurably less reliable
    over its streaming endpoint — identical requests fail with `tool_use_failed`
    perhaps a third of the time — so the path that does not need tokens as they
    arrive does not pay that risk.
    """
    name, model, agent = _agent_for(provider)
    started = time.perf_counter()

    result = await agent.ainvoke({"messages": messages})
    history = result["messages"]
    elapsed = int((time.perf_counter() - started) * 1000)

    reply = next(
        (
            m.text
            for m in reversed(history)
            if isinstance(m, AIMessage) and m.text.strip()
        ),
        "",
    )
    trace = _trace_from(history, name, model, elapsed)
    return {"reply": reply, "sources": _sources(trace["steps"]), "trace": trace}


async def stream(messages: list[dict], provider: str | None = None) -> AsyncIterator[dict]:
    """Yield the reply as it is written, then its sources, then the trace.

    The trace arrives last because it is not complete until the run is. Sending
    it down this stream rather than letting the caller ask afterwards matters: a
    second request would mean a second conversation with the model, doubling
    both the bill and the latency to display one panel.
    """
    name, model, agent = _agent_for(provider)
    started = time.perf_counter()
    produced: list[BaseMessage] = []

    async for mode, payload in agent.astream(
        {"messages": messages}, stream_mode=["messages", "updates"]
    ):
        if mode == "messages":
            chunk, _metadata = payload
            if isinstance(chunk, AIMessage) and not chunk.tool_calls:
                text = chunk.text
                if text:
                    yield {"token": text}
            continue
        # `updates` reports whole nodes rather than tokens, which is the right
        # granularity for a trace: one entry per model call, one per batch of
        # tool calls. Collected here and reconstructed at the end so both paths
        # produce the same shape.
        for _node, update in payload.items():
            produced.extend((update or {}).get("messages", []) or [])

    elapsed = int((time.perf_counter() - started) * 1000)
    trace = _trace_from(produced, name, model, elapsed)
    yield {"sources": _sources(trace["steps"])}
    yield {"trace": trace}
