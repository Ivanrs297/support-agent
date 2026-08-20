"""The support agent: a ReAct loop over the hotel tools, on Groq or Bedrock.

STEPS 11, 12 and 26 — see README §11, §12 and §26.

Everything the agent knows about Hotel Aurora has to come from tool results. It
is not told the policies in its prompt, and that is deliberate — a prompt that
carries the knowledge base answers confidently from memory and drifts from the
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

# STEP 11 — the system prompt.
#
# Nothing about the hotel goes in here. No opening hours, no pet fee, no phone
# number that the documentation does not already carry. The moment a fact lives
# in the prompt, the agent will answer from it without calling anything, and
# your retrieval becomes decoration.
#
# What does go in here, and README §11 argues each one:
#
#   1. Which tool covers what, so the model does not have to guess.
#   2. An instruction to call a tool before answering ANY question about the
#      hotel — including the ones it is sure about, like check-out time.
#   3. What to do when the tools return nothing: say so, offer a human, and
#      never fill the gap with something plausible.
#   4. The limits of its authority. It cannot take payment or change a booking.
#   5. Reply in the guest's language, but query the tools in English — the
#      documentation is in English and the retrieval is monolingual.
#
# Write it as instructions, not as description. "Do not answer from memory" is
# a rule; "you are a helpful assistant" is a mood.
SYSTEM_PROMPT = """\
TODO: write the system prompt — see STEP 11 above.
"""


@lru_cache(maxsize=4)
def build_agent(provider: str, model: str):
    """One compiled graph per provider. Cached — compiling is not free.

    STEP 12.1
    Build a ReAct agent from `create_agent`, the model for this provider, TOOLS,
    and SYSTEM_PROMPT.

    Note what `model` is doing in the signature. It is derived from the provider,
    so it looks redundant — but it is part of the cache key, and without it
    changing the configured model silently keeps serving the agent built from
    the old one.
    """
    raise NotImplementedError("STEP 12.1 — see README §12")


def _agent_for(provider: str | None):
    """Resolve a provider name to (name, model, agent), or refuse with a reason.

    STEP 12.2
    A missing provider name falls back to config.settings.default_provider.

    Raise ValueError — not a bare Exception, and not an HTTP error — for both an
    unknown provider and a known one this deployment cannot serve. The API layer
    turns that into a 400 with the reason in it. Keeping the HTTP vocabulary out
    of this module is what lets it be called from a test, a script, or a stream.
    """
    raise NotImplementedError("STEP 12.2 — see README §12")


def _trace_from(messages: list[BaseMessage], provider: str, model: str, ms: int) -> dict:
    """Rebuild what happened from the messages the run produced.

    STEP 26.1
    Derived from the final message list rather than observed live, because the
    two paths that need a trace — a blocking answer and a streamed one — share
    only their output. Reconstruction gives both the same shape, at the cost of
    per-step timings: the total is measured, the split between steps is not.
    That trade is worth stating out loud rather than discovering later.

    Walk the messages and emit one step per thing that happened:

      {"kind": "model", "input_tokens": …, "output_tokens": …,
       "tool_calls": [names]}
      {"kind": "tool", "name": …, "args": …, "result_chars": …}

    A ToolMessage does not carry the arguments the tool was called with — those
    were on the AIMessage that requested it. Hold them by tool_call_id as you
    pass the AIMessage and attach them when the result arrives. Without the
    arguments a trace shows that a tool ran but not what was asked of it, which
    is most of what you need to explain a bad answer.

    Return the totals too: provider, model, ms, model_calls, input_tokens,
    output_tokens, cost_usd (from estimate_cost), and steps.
    """
    raise NotImplementedError("STEP 26.1 — see README §26")


def _sources(steps: list[dict]) -> list[str]:
    """Which tools the agent actually consulted, in call order, deduplicated.

    STEP 26.2
    An answer whose `sources` list is empty is the agent talking from memory,
    which the prompt forbids. Surfacing it is what makes that visible instead of
    invisible.
    """
    raise NotImplementedError("STEP 26.2 — see README §26")


async def run(messages: list[dict], provider: str | None = None) -> dict:
    """Answer a conversation and return the reply, its sources, and the trace.

    STEP 13.1
    Deliberately not streamed, even though a streaming implementation exists
    below. Some models call tools measurably less reliably over their streaming
    endpoint — identical requests fail with `tool_use_failed` a third of the
    time — so the path that does not need tokens as they arrive should not pay
    that risk. README §14 has the measurement.

    Time the run with perf_counter, take the last AIMessage with actual text as
    the reply (the last message may be a tool result), and return
    {"reply", "sources", "trace"}.
    """
    raise NotImplementedError("STEP 13.1 — see README §13")


async def stream(messages: list[dict], provider: str | None = None) -> AsyncIterator[dict]:
    """Yield the reply as it is written, then its sources, then the trace.

    STEP 14.1
    Use `agent.astream(..., stream_mode=["messages", "updates"])`. The two modes
    answer different questions and you need both:

    - "messages" gives token chunks. Yield {"token": text} for AIMessage chunks
      that carry text and no tool calls — a chunk carrying a tool call is the
      model deciding, not the model answering, and must not reach the guest.
    - "updates" gives whole graph nodes. That is the right granularity for a
      trace: one entry per model call, one per batch of tool calls. Collect the
      messages and reconstruct at the end, so this path and `run` produce the
      same shape.

    Then yield {"sources": [...]} and {"trace": {...}}, in that order. The trace
    goes last because it is not complete until the run is.

    Sending both down this stream rather than letting the caller ask afterwards
    matters more than it looks: a second request means a second conversation
    with the model, which doubles both the bill and the latency to display one
    panel.
    """
    raise NotImplementedError("STEP 14.1 — see README §14")
    yield  # noqa: unreachable — keeps this an async generator
