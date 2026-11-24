import json
from typing import Any

import httpx
import pytest

from assistant_engine import AssistantEngine, Citation, ToolCall, ToolResult, Vocabulary, build_provider
from assistant_engine.providers import is_local_url


class FakeExecutor:
    """Answers tool calls with fixed SQL-like results and records what was asked."""

    def __init__(self, data: dict[str, dict[str, Any]], outcomes: dict[str, str] | None = None) -> None:
        self.data = data
        self.outcomes = outcomes or {}
        self.calls: list[ToolCall] = []

    async def run(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        outcome = self.outcomes.get(call.name, "OK")
        citations = [Citation.of("INCIDENT", "i-9", "INC-000009 · Fight at the gate")] if outcome == "OK" else []
        return ToolResult(call.name, call.arguments, outcome, self.data.get(call.name, {}), citations, rows=3)  # type: ignore[arg-type]


TREND = {
    "alerts_total": 12,
    "alerts_high": 9,
    "alerts_by_type": [
        {"type": "WEAPON", "count": 5},
        {"type": "FIGHT_VIOLENCE", "count": 4},
        {"type": "RESTRICTED_AREA", "count": 3},
    ],
    "incidents_total": 4,
    "incidents_by_status": [],
    "manual_reports_total": 2,
    "manual_reports_by_type": [{"type": "Suspicious Person", "count": 2}],
    "most_frequent": [{"category": "WEAPON", "count": 5}],
}
HOTSPOTS = {
    "level": "building",
    "source": "incidents",
    "without_location": 0,
    "items": [
        {"id": "b-lib", "name": "Library", "total": 3, "alerts": 0, "incidents": 3, "manual_reports": 0},
        {"id": "b-admin", "name": "Admin Block", "total": 1, "alerts": 0, "incidents": 1, "manual_reports": 0},
    ],
}
CASES = {
    "source": "incidents",
    "total": 2,
    "handlers": [{"name": "Sana Malik", "count": 1}, {"name": "Unassigned", "count": 1}],
    "items": [
        {
            "id": "i-9",
            "reference": "INC-000009",
            "title": "Fight at the gate",
            "status": "OPEN",
            "handler": "Sana Malik",
            "place": "Library",
        }
    ],
}


async def test_the_deterministic_answer_is_grounded_cited_and_keeps_context(vocab: Vocabulary) -> None:
    engine = AssistantEngine()
    executor = FakeExecutor({"trend_summary": TREND, "location_hotspots": HOTSPOTS, "case_list": CASES})
    answer = await engine.answer(
        "How many alerts happened last year and which type was most frequent?",
        context=None,
        vocabulary=vocab,
        executor=executor,
    )
    assert answer.provider == "deterministic" and answer.phrasing == "deterministic"
    assert "12 alerts" in answer.content and "The most frequent type was Weapon (5)" in answer.content
    assert "last year (2025)" in answer.content
    assert answer.blocks[0]["type"] == "metrics" and not answer.insufficient_evidence
    assert answer.citations[0].route == "/incidents/i-9"
    assert answer.tool_calls()[0] == {
        "tool": "trend_summary",
        "arguments": executor.calls[0].arguments,
        "outcome": "OK",
        "rows": 3,
        "latency_ms": 0.0,
    }

    first = await engine.answer(
        "Which building had the most incidents in October?", context=None, vocabulary=vocab, executor=executor
    )
    assert first.context["focus_location"]["id"] == "b-lib" and "1. Library — 3" in first.content
    second = await engine.answer(
        "How many of those are still unresolved and who is handling them?",
        context=first.context,
        vocabulary=vocab,
        executor=executor,
    )
    call = executor.calls[-1]
    assert call.name == "case_list" and call.arguments["location"]["id"] == "b-lib"
    assert call.arguments["period"]["label"] == "October 2026"
    assert "Sana Malik (1)" in second.content


async def test_missing_data_is_said_explicitly(vocab: Vocabulary) -> None:
    empty_trend = {
        **TREND,
        "alerts_total": 0,
        "incidents_total": 0,
        "manual_reports_total": 0,
        "alerts_by_type": [],
        "manual_reports_by_type": [],
        "most_frequent": [],
    }
    answer = await AssistantEngine().answer(
        "How many alerts in 2019?",
        context=None,
        vocabulary=vocab,
        executor=FakeExecutor({"trend_summary": empty_trend}, {"trend_summary": "EMPTY"}),
    )
    assert answer.insufficient_evidence
    assert answer.content == "No alerts, cases or manual reports were found in 2019."


async def test_refusals_run_no_tool_and_accusations_never_name_anyone(vocab: Vocabulary) -> None:
    executor = FakeExecutor({})
    answer = await AssistantEngine().answer("Resolve INC-000009", context=None, vocabulary=vocab, executor=executor)
    assert answer.refusal == "READ_ONLY" and executor.calls == [] and "read-only" in answer.content
    timeline = {
        "case": {
            "reference": "INC-000009",
            "title": "Fight",
            "status": "OPEN",
            "priority": "HIGH",
            "assigned_name": None,
        },
        "events": [],
        "counts": {},
    }
    accused = await AssistantEngine().answer(
        "Who is guilty in INC-000009?",
        context=None,
        vocabulary=vocab,
        executor=FakeExecutor({"case_timeline": timeline}),
    )
    assert accused.refusal == "ACCUSATION" and accused.content.startswith("I can't say who is responsible")


async def test_help_for_questions_outside_the_six_areas(vocab: Vocabulary) -> None:
    answer = await AssistantEngine().answer("Tell me a joke", context=None, vocabulary=vocab, executor=FakeExecutor({}))
    assert "I can help with" in answer.content and not answer.insufficient_evidence and answer.results == []


def _ollama(handler) -> AssistantEngine:  # type: ignore[no-untyped-def]
    provider = build_provider("ollama", model_name="qwen2.5:7b", transport=httpx.MockTransport(handler))
    return AssistantEngine(provider)


def _chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


async def test_an_llm_plan_is_normalised_and_its_rephrasing_used_only_when_grounded(vocab: Vocabulary) -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body.get("format") == "json":
            return _chat(json.dumps({"tool_calls": [{"name": "trend_summary", "arguments": {"period": "last year"}}]}))
        return _chat("Last year (2025) there were 12 alerts; weapon alerts were the most frequent (5).")

    executor = FakeExecutor({"trend_summary": TREND})
    answer = await _ollama(handler).answer(
        "alerts last year? mail me at sana@lakeside.example.com", context=None, vocabulary=vocab, executor=executor
    )
    assert answer.provider == "ollama" and answer.model_name == "qwen2.5:7b" and answer.phrasing == "llm"
    assert executor.calls[0].arguments["period"]["label"] == "last year (2025)"
    assert answer.content.startswith("Last year (2025) there were 12 alerts")
    assert "lakeside.example.com" not in json.dumps(seen)  # redacted before leaving the engine


async def test_ungrounded_or_unsafe_rephrasing_is_discarded(vocab: Vocabulary) -> None:
    replies = iter([json.dumps({"tool_calls": []}), "There were 15 alerts last year."])
    answer = await _ollama(lambda r: _chat(next(replies))).answer(
        "How many alerts last year?", context=None, vocabulary=vocab, executor=FakeExecutor({"trend_summary": TREND})
    )
    assert answer.phrasing == "deterministic_fallback" and "12 alerts" in answer.content
    replies2 = iter([json.dumps({"tool_calls": []}), "There were 12 alerts; the guard is the attacker."])
    unsafe = await _ollama(lambda r: _chat(next(replies2))).answer(
        "How many alerts last year?", context=None, vocabulary=vocab, executor=FakeExecutor({"trend_summary": TREND})
    )
    assert unsafe.phrasing == "deterministic_fallback"


async def test_invalid_llm_plans_fall_back_to_the_deterministic_plan(vocab: Vocabulary) -> None:
    replies = iter([json.dumps({"tool_calls": [{"name": "delete_everything", "arguments": {}}]}), "x"])
    executor = FakeExecutor({"trend_summary": TREND})
    await _ollama(lambda r: _chat(next(replies))).answer(
        "How many alerts last year?", context=None, vocabulary=vocab, executor=executor
    )
    assert [c.name for c in executor.calls] == ["trend_summary"]


async def test_an_unreachable_model_is_reported_and_the_answer_still_comes(vocab: Vocabulary) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    engine = _ollama(down)
    status = await engine.provider.status()
    assert not status.available and "unreachable" in status.detail
    answer = await engine.answer(
        "How many alerts last year?", context=None, vocabulary=vocab, executor=FakeExecutor({"trend_summary": TREND})
    )
    assert answer.blocks[0]["type"] == "notice" and "could not be used" in answer.blocks[0]["text"]
    assert "12 alerts" in answer.content


async def test_ollama_status_requires_the_model() -> None:
    def tags(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3:8b"}]})

    provider = build_provider("ollama", transport=httpx.MockTransport(tags))
    status = await provider.status()
    assert not status.available and "not pulled" in status.detail


async def test_external_endpoints_need_explicit_permission() -> None:
    assert is_local_url("http://127.0.0.1:11434") and is_local_url("http://gpu-box.local:8000/v1")
    provider = build_provider("openai_compatible", base_url="https://api.example.com/v1", model_name="m")
    assert not (await provider.status()).available
    allowed = build_provider(
        "openai_compatible",
        base_url="https://api.example.com/v1",
        model_name="m",
        allow_external=True,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
    )
    assert (await allowed.status()).available
    with pytest.raises(ValueError):
        build_provider("openai_compatible")
