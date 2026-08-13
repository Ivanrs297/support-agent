"""The support agent: a ReAct loop over the hotel tools, backed by Groq.

Everything the agent knows about Hotel Aurora comes from tool results. It is not
told the policies in its prompt, and that is deliberate — a prompt that carries
the knowledge base would answer confidently from memory and drift from the
documentation the moment either changes.
"""

from collections.abc import AsyncIterator

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_groq import ChatGroq

from .config import settings
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


def build_agent():
    model = ChatGroq(
        model=settings.model,
        api_key=settings.groq_api_key,
        temperature=settings.temperature,
        timeout=settings.request_timeout,
    )
    return create_agent(model=model, tools=TOOLS, system_prompt=SYSTEM_PROMPT)


agent = build_agent()


def _sources(messages: list[BaseMessage]) -> list[str]:
    """Which tools the agent actually consulted, in call order.

    Returned to the caller so an answer can be traced back to the tools behind
    it. An answer with an empty `sources` list is the agent talking from memory,
    which the prompt forbids — surfacing it makes that visible instead of
    invisible.
    """
    seen: list[str] = []
    for message in messages:
        if isinstance(message, ToolMessage) and message.name not in seen:
            seen.append(message.name)
    return seen


async def run(messages: list[dict]) -> dict:
    """Answer a conversation and return the reply plus the tools used."""
    result = await agent.ainvoke({"messages": messages})
    history = result["messages"]
    reply = next(
        (
            m.text()
            for m in reversed(history)
            if isinstance(m, AIMessage) and m.text().strip()
        ),
        "",
    )
    return {"reply": reply, "sources": _sources(history)}


async def stream(messages: list[dict]) -> AsyncIterator[str]:
    """Yield the reply token by token as the model produces it.

    Only tokens from the final answer are emitted. The intermediate turns that
    decide which tool to call are part of the machinery, not the answer, and
    streaming them would show the guest the agent thinking out loud.
    """
    async for chunk, _metadata in agent.astream(
        {"messages": messages}, stream_mode="messages"
    ):
        if isinstance(chunk, AIMessage) and not chunk.tool_calls:
            text = chunk.text()
            if text:
                yield text
