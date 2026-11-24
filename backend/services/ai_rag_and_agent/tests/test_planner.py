from dataclasses import replace

from assistant_engine import planner
from assistant_engine.types import Vocabulary


def names(question: str, vocab: Vocabulary, **kw) -> list[str]:  # type: ignore[no-untyped-def]
    return [c.name for c in planner.plan(question, kw.pop("context", None), vocab, **kw).calls]


def test_each_capability_is_recognised(vocab: Vocabulary) -> None:
    assert names("How many incidents happened last year and which type was most frequent?", vocab) == ["trend_summary"]
    assert names("What are our peak incident hours and top hotspot buildings?", vocab) == [
        "peak_times",
        "location_hotspots",
    ]
    assert names("Which cameras went offline most often this month?", vocab) == ["camera_reliability"]
    assert names("What is our average Guard response and escalation rate?", vocab) == ["guard_response"]
    assert names("Did weapon alerts increase this month compared with last month?", vocab) == ["compare_periods"]
    assert names("Summarize manual reports at the Main Gate over the last 3 months", vocab) == ["manual_report_search"]
    assert names("Show evidence tagged weapon from last month", vocab) == ["evidence_search"]
    assert names("Investigate INC-000012", vocab) == ["case_timeline", "related_events", "evidence_gaps"]
    assert names("Show the audit activity this week", vocab) == ["audit_activity"]


def test_arguments_come_from_the_question_and_the_vocabulary(vocab: Vocabulary) -> None:
    [call] = planner.plan("Summarize manual reports at the Main Gate over the last 3 months", None, vocab).calls
    assert call.arguments["location"] == {"level": "AREA", "id": "a-gate", "name": "North Fence › Main Gate"}
    assert call.arguments["period"]["label"] == "the last 3 months"
    [cmp] = planner.plan("Weapon alerts October vs September", None, vocab).calls
    assert cmp.arguments["alert_type"] == "WEAPON"
    assert cmp.arguments["period"]["label"] == "October 2026"
    assert cmp.arguments["previous_period"]["label"] == "September 2026"
    [yoy] = planner.plan("Fights in October year over year", None, vocab).calls
    assert yoy.arguments["previous_period"]["label"] == "October 2025"
    [ev] = planner.plan("Evidence tagged gate-breach from Gate Camera", None, vocab).calls
    assert ev.arguments["tags"] == ["gate-breach"] and ev.arguments["camera"] == {"id": "c-gate", "name": "Gate Camera"}
    [rel] = planner.plan("Anything related within 45 minutes of case 7?", None, vocab).calls
    assert rel.name == "related_events" and rel.arguments == {"case": {"reference": "INC-000007"}, "window_minutes": 45}


def test_which_gate_is_a_question_not_a_place(vocab: Vocabulary) -> None:
    [call] = planner.plan("Which gate had the most alerts in October?", None, vocab).calls
    assert call.name == "location_hotspots" and call.arguments["level"] == "area" and "location" not in call.arguments
    assert call.arguments["source"] == "alerts"


def test_a_missing_period_defaults_to_30_days_and_says_so(vocab: Vocabulary) -> None:
    result = planner.plan("How many alerts?", None, vocab)
    assert result.calls[0].arguments["period"]["label"] == "the last 30 days"
    assert result.notes == ["No period was given, so I used the last 30 days."]


def test_follow_up_questions_reuse_the_period_and_the_top_place(vocab: Vocabulary) -> None:
    first = planner.plan("Which building had the most incidents in October?", None, vocab)
    assert [c.name for c in first.calls] == ["location_hotspots"]
    context = {**first.context, "focus_location": {"level": "BUILDING", "id": "b-lib", "name": "Library"}}
    second = planner.plan("How many of those are still unresolved and who is handling them?", context, vocab)
    [call] = second.calls
    assert call.name == "case_list"
    assert call.arguments["unresolved_only"] is True and call.arguments["source"] == "incidents"
    assert call.arguments["period"]["label"] == "October 2026"
    assert call.arguments["location"]["id"] == "b-lib"
    third = planner.plan("What about September?", second.context, vocab)
    assert [c.name for c in third.calls] == ["case_list"]
    assert third.calls[0].arguments["period"]["label"] == "September 2026"


def test_case_sessions_answer_general_questions_generally(vocab: Vocabulary) -> None:
    case = {"id": "i-1", "reference": "INC-000003"}
    assert names("How many incidents last year?", vocab, context_case=case) == ["trend_summary"]
    assert names("What happened?", vocab, context_case=case) == ["case_timeline"]
    assert names("Investigate this case", vocab, context_case=case) == list(planner.CASE_TOOLS)


def test_refusals_come_before_any_tool(vocab: Vocabulary) -> None:
    for question, kind in [
        ("Close INC-000012 now", "READ_ONLY"),
        ("Please escalate the alert to all guards", "READ_ONLY"),
        ("Upload our SOP PDF", "NO_UPLOADS"),
        ("Show the personnel records of the library staff", "EXCLUDED"),
    ]:
        result = planner.plan(question, None, vocab)
        assert result.refusal is not None and result.refusal.kind == kind and result.calls == [], question
    accusation = planner.plan("Who is guilty in INC-000012?", None, vocab)
    assert accusation.refusal is not None and accusation.refusal.kind == "ACCUSATION"
    assert [c.name for c in accusation.calls] == ["case_timeline"]  # records yes, a culprit never


def test_supervisors_are_told_excluded_data_is_not_for_their_role(vocab: Vocabulary) -> None:
    supervisor = replace(vocab, role="SUPERVISOR")
    result = planner.plan("Who is authorized for the restricted area?", None, supervisor)
    assert result.refusal is not None and "not available to Supervisors" in result.refusal.text


def test_unknown_questions_get_help(vocab: Vocabulary) -> None:
    result = planner.plan("Hello there", None, vocab)
    assert result.help and result.calls == []


def test_llm_plans_are_normalised_and_validated(vocab: Vocabulary) -> None:
    calls = planner.normalise_llm_calls(
        [{"name": "manual_report_search", "arguments": {"period": "last month", "location": "Main Gate"}}],
        "reports at the gate last month",
        {},
        vocab,
    )
    assert calls[0].arguments["location"]["id"] == "a-gate"
    assert calls[0].arguments["period"]["label"].startswith("last month")
    import pytest

    from assistant_engine.catalog import ArgumentError

    with pytest.raises(ArgumentError):
        planner.normalise_llm_calls([{"name": "drop_table", "arguments": {}}], "x", {}, vocab)
    with pytest.raises(ArgumentError):
        planner.normalise_llm_calls([{"name": "peak_times", "arguments": {"source": "everything"}}], "x", {}, vocab)
    with pytest.raises(ArgumentError):
        planner.normalise_llm_calls([{"name": "case_timeline", "arguments": {}}], "x", {}, vocab)
