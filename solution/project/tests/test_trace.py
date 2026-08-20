"""What the trace says happened, and what it costs.

The trace is reconstructed from the messages a run produced, so it can be tested
without a model: hand it a message list and check the ledger it builds. The one
case that matters most is the last — an answer with no tool call in it, which is
the agent talking from memory.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent import _sources, _trace_from
from app.providers import available_providers, estimate_cost


def ai(text: str = "", *, calls: list[dict] | None = None, tokens: tuple[int, int] = (0, 0)):
    return AIMessage(
        content=text,
        tool_calls=calls or [],
        usage_metadata={
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
            "total_tokens": sum(tokens),
        },
    )


class TestTrace:
    def test_records_one_step_per_thing_that_happened(self):
        history = [
            HumanMessage("What are the pool hours?"),
            ai(calls=[{"name": "search_hotel_policies", "args": {"query": "pool"}, "id": "c1"}],
               tokens=(780, 19)),
            ToolMessage(content="[amenities — Pool]\nOpen 07:00 to 21:00.", name="search_hotel_policies",
                        tool_call_id="c1"),
            ai("The pool is open 07:00 to 21:00.", tokens=(1020, 12)),
        ]
        trace = _trace_from(history, "groq", "llama-3.1-8b-instant", ms=730)

        assert [step["kind"] for step in trace["steps"]] == ["model", "tool", "model"]
        assert trace["model_calls"] == 2
        assert trace["input_tokens"] == 1800
        assert trace["output_tokens"] == 31

    def test_carries_the_arguments_each_tool_was_given(self):
        # Without the arguments, a trace shows that a tool ran but not what was
        # asked of it — which is most of what you need to explain a bad answer.
        history = [
            ai(calls=[{"name": "get_reservation", "args": {"confirmation_code": "AUR-104582"}, "id": "c1"}]),
            ToolMessage(content="{...}", name="get_reservation", tool_call_id="c1"),
        ]
        trace = _trace_from(history, "groq", "m", ms=1)

        tool_step = trace["steps"][1]
        assert tool_step["args"] == {"confirmation_code": "AUR-104582"}
        assert tool_step["result_chars"] == 5

    def test_an_answer_with_no_tool_call_has_no_sources(self):
        # The failure this whole project is about: a confident sentence with
        # nothing behind it. The trace has to make it visible rather than let it
        # look like every other answer.
        history = [ai("Check-out is at 11:00.", tokens=(700, 8))]
        trace = _trace_from(history, "groq", "m", ms=200)

        assert _sources(trace["steps"]) == []
        assert trace["model_calls"] == 1
        assert all(step["kind"] != "tool" for step in trace["steps"])

    def test_sources_are_deduplicated_in_call_order(self):
        history = [
            ai(calls=[{"name": "search_hotel_policies", "args": {}, "id": "a"}]),
            ToolMessage(content="x", name="search_hotel_policies", tool_call_id="a"),
            ai(calls=[{"name": "get_reservation", "args": {}, "id": "b"},
                      {"name": "search_hotel_policies", "args": {}, "id": "c"}]),
            ToolMessage(content="y", name="get_reservation", tool_call_id="b"),
            ToolMessage(content="z", name="search_hotel_policies", tool_call_id="c"),
        ]
        trace = _trace_from(history, "groq", "m", ms=1)
        assert _sources(trace["steps"]) == ["search_hotel_policies", "get_reservation"]


class TestCost:
    def test_priced_model(self):
        # 1M in + 1M out on a model at $0.05 / $0.08
        assert estimate_cost("llama-3.1-8b-instant", 1_000_000, 1_000_000) == 0.13

    def test_unknown_model_returns_none_not_zero(self):
        # A model with no published price shown as $0.00 reads as free, which is
        # the one wrong answer a cost display must never give.
        assert estimate_cost("some-new-model", 1000, 1000) is None
        assert _trace_from([], "bedrock", "some-new-model", ms=1)["cost_usd"] is None


class TestProviders:
    def test_groq_is_always_available(self):
        # Its key is required at import, so if the app started, Groq works.
        groq = next(p for p in available_providers() if p.name == "groq")
        assert groq.available

    def test_bedrock_explains_itself_when_unusable(self):
        # Two independent things can be missing — the package and the model id —
        # and the fixes are unrelated, so the reason has to say which.
        bedrock = next(p for p in available_providers() if p.name == "bedrock")
        if not bedrock.available:
            assert "BEDROCK_MODEL_ID" in bedrock.detail or "langchain-aws" in bedrock.detail
