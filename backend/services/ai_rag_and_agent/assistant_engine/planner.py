"""The deterministic planner: a question (+ the conversation's context) → read-only tool calls (decision D-027).

It is the planner of ``AI_LLM_PROVIDER=deterministic`` and the fallback of the LLM providers. It recognises the six
capability areas by keywords, extracts periods (``dates.py``), places, cameras, case references, alert types, report
types and evidence tags from the caller's own campus vocabulary, and carries the context of earlier turns forward:
"How many of those are still unresolved?" reuses the period and the top place of the previous answer.

The same normalisation (``normalise_llm_calls``) turns an LLM planner's simple arguments ("period": "last month",
"location": "Admin Block", "case": "INC-000012") into the exact arguments the tools need, so an LLM never invents a
date range or an ID.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from assistant_engine import dates
from assistant_engine.catalog import ArgumentError, validate
from assistant_engine.guardrails import Refusal, classify_request
from assistant_engine.types import LocationRef, ToolCall, Vocabulary

CASE_REF = re.compile(r"\b(?:inc[-\s#]?0*(\d{1,9})|case\s*(?:no\.?|number|#)?\s*0*(\d{1,9}))\b", re.IGNORECASE)
WINDOW = re.compile(r"(\d{1,3})\s*(?:min|mins|minutes)\b", re.IGNORECASE)
FOLLOW_UP = re.compile(
    r"\b(those|these|them|that\s+(?:one|building|floor|area|place|camera|case|period|month)|there|same\s+(?:period|"
    r"time|place|building)|of\s+(?:those|them|these)|what\s+about|how\s+about|and\s+(?:in|for|at|during))\b"
    r"|^\s*(?:and|also|what about|how about)\b",
    re.IGNORECASE,
)

_INTENTS: list[tuple[str, re.Pattern[str]]] = [
    (
        "case_timeline",
        re.compile(
            r"\b(timeline|what\s+happened|chronolog\w*|sequence|summar\w*|walk\s+me\s+through|full\s+story|history|"
            r"investigat\w*)\b"
        ),
    ),
    (
        "related_events",
        re.compile(
            r"\b(related|nearby|near\s+by|around\s+the\s+same\s+time|at\s+the\s+same\s+time|same\s+time|correlat\w*|"
            r"other\s+events|anything\s+else|within\s+\d+\s*min\w*)\b|±|\+/-"
        ),
    ),
    (
        "evidence_gaps",
        re.compile(
            r"\b(gaps?|missing|blind\s?spots?|offline|footage|unsaved|unverified|pending\s+review|coverage|"
            r"no\s+(?:clip|footage|evidence|recording))\b"
        ),
    ),
    (
        "peak_times",
        re.compile(
            r"\b(peak|busiest|which\s+hours?|what\s+time|times?\s+of\s+(?:the\s+)?day|day\s+of\s+(?:the\s+)?week|"
            r"which\s+days?|hours?\s+of\s+the\s+day|weekdays?|weekends?|hour\s+by\s+hour)\b"
        ),
    ),
    (
        "compare_periods",
        re.compile(
            r"\b(compar\w*|vs\.?|versus|increase\w*|decrease\w*|went\s+(?:up|down)|rise|rose|drop\w*|fell|"
            r"month[-\s]over[-\s]month|year[-\s]over[-\s]year|mom|yoy|than\s+last|than\s+the\s+previous|"
            r"change\s+(?:from|since|compared))\b"
        ),
    ),
    (
        "location_hotspots",
        re.compile(
            r"\b(hot\s?spots?|which\s+(?:building|floor|area|zone|gate|location|place)s?|where\s+(?:do|did|are|were|is|"
            r"was|have)|top\s+(?:buildings?|floors?|areas?|locations?|zones?|gates?|places?)|by\s+(?:building|floor|area|"
            r"location|zone)|most\s+(?:incidents|alerts|events|reports)\s+(?:in|at|by))\b"
        ),
    ),
    (
        "camera_reliability",
        re.compile(
            r"\bcameras?\b.*\b(offline|down|reliab\w*|uptime|downtime|disconnect\w*|fail\w*|outages?)\b"
            r"|\b(offline|downtime|uptime|reliab\w*|outages?)\b.*\bcameras?\b"
        ),
    ),
    (
        "guard_response",
        re.compile(
            r"\b(response\s+times?|respond\w*|acknowledg\w*|resolution\s+times?|resolve\s+times?|how\s+(?:fast|quick\w*)|"
            r"average\s+(?:\w+\s+)?times?|escalat\w*|additional\s+guards|backup|standby|stationed|unacknowledged|"
            r"guard\s+performance|performance)\b"
        ),
    ),
    (
        "audit_activity",
        re.compile(
            r"\b(audit\w*|activity\s+logs?|sign[-\s]?ins?|log[-\s]?ins?|who\s+changed|actions?\s+(?:taken|performed))\b"
        ),
    ),
    (
        "manual_report_search",
        re.compile(
            r"\b(manual\s+(?:security\s+)?reports?|guard(?:s'?|'s)?\s+reports?|reports?\s+(?:submitted|filed|by\s+guards))\b"
        ),
    ),
    ("evidence_search", re.compile(r"\b(evidence|vault|saved\s+clips?|snapshots?|tagged|tags?)\b")),
    (
        "case_list",
        re.compile(
            r"\b(unresolved|still\s+open|open\s+(?:cases|incidents|alerts)|who\s+is\s+handling|who's\s+handling|"
            r"handling\s+(?:them|those|it)|assigned\s+to|in\s+progress|not\s+(?:yet\s+)?resolved|pending\s+cases|"
            r"which\s+cases)\b"
        ),
    ),
    (
        "trend_summary",
        re.compile(
            r"\b(how\s+many|total|count|number\s+of|most\s+(?:frequent|common)|which\s+type|what\s+type|breakdown|"
            r"statistics|stats|trends?|overview)\b"
        ),
    ),
]
CASE_TOOLS = ("case_timeline", "related_events", "evidence_gaps")
ORDER = (
    "trend_summary",
    "compare_periods",
    "peak_times",
    "location_hotspots",
    "camera_reliability",
    "guard_response",
    "audit_activity",
    "case_list",
    "case_timeline",
    "related_events",
    "evidence_gaps",
    "manual_report_search",
    "evidence_search",
)

_ALERT_TYPES = [
    ("WEAPON", re.compile(r"\b(weapons?|guns?|knife|knives|firearms?|armed|pistols?|rifles?)\b", re.IGNORECASE)),
    ("FIGHT_VIOLENCE", re.compile(r"\b(fights?|fighting|violence|violent|brawls?|assaults?)\b", re.IGNORECASE)),
    (
        "RESTRICTED_AREA",
        re.compile(r"\b(restricted[-\s]area|intrusions?|trespass\w*|unauthori[sz]ed\s+entry)\b", re.IGNORECASE),
    ),
]
_SOURCE_WORDS = [
    ("manual_reports", re.compile(r"\b(manual\s+reports?|guard(?:s'?|'s)?\s+reports?)\b", re.IGNORECASE)),
    ("incidents", re.compile(r"\b(incidents?|cases?|investigations?)\b", re.IGNORECASE)),
    ("alerts", re.compile(r"\b(alerts?|detections?)\b", re.IGNORECASE)),
]
_LEVELS = [
    ("area", re.compile(r"\b(areas?|zones?|gates?|rooms?|spots?|entrances?|exits?)\b", re.IGNORECASE)),
    ("floor", re.compile(r"\bfloors?\b", re.IGNORECASE)),
    ("building", re.compile(r"\b(buildings?|blocks?|perimeters?)\b", re.IGNORECASE)),
]
_YOY = re.compile(
    r"\b(year[-\s]over[-\s]year|yoy|same\s+(?:month|period|quarter)\s+last\s+year|a\s+year\s+ago)\b", re.IGNORECASE
)
_UNRESOLVED = re.compile(
    r"\b(unresolved|still\s+open|open|not\s+(?:yet\s+)?resolved|pending|in\s+progress|handling)\b", re.IGNORECASE
)
_QUESTION_WORD_BEFORE = re.compile(r"\b(which|what|each|per|by|every|top|most|all)\s+$", re.IGNORECASE)


@dataclass(slots=True)
class Plan:
    calls: list[ToolCall]
    context: dict[str, Any]
    refusal: Refusal | None = None
    notes: list[str] = field(default_factory=list)
    help: bool = False


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _find_phrase(question: str, phrase: str) -> int:
    """Start index of ``phrase`` as whole words in ``question`` (case-insensitive), or -1."""
    m = re.search(rf"(?<![a-z0-9-]){re.escape(phrase.lower())}(?![a-z0-9-])", question.lower())
    return m.start() if m else -1


def match_location(question: str, vocab: Vocabulary) -> LocationRef | None:
    """The campus place a question names. Longest name wins; "which gate" is a question word, not a place."""
    best: tuple[int, LocationRef] | None = None
    for loc in vocab.locations:
        parts = [p.strip() for p in loc.name.split("›")]
        own = parts[-1]
        keys = [own]
        if loc.level == "FLOOR":
            keys = [f"{own} floor", f"floor {own}"] + ([f"{parts[0]} {own}"] if len(parts) > 1 else [])
        for key in keys:
            if len(key) < 3:
                continue
            at = _find_phrase(question, key)
            if at < 0 or _QUESTION_WORD_BEFORE.search(question[:at]):
                continue
            if best is None or len(key) > best[0]:
                best = (len(key), loc)
    return best[1] if best else None


def match_camera(question: str, vocab: Vocabulary) -> dict[str, str] | None:
    hits = [(len(c.name), c) for c in vocab.cameras if len(c.name) >= 3 and _find_phrase(question, c.name) >= 0]
    if not hits:
        return None
    camera = max(hits, key=lambda h: h[0])[1]
    return {"id": camera.id, "name": camera.name}


def match_case(question: str) -> dict[str, str] | None:
    m = CASE_REF.search(question)
    if not m:
        return None
    number = int(m.group(1) or m.group(2))
    return {"reference": f"INC-{number:06d}"}


def _alert_type(question: str) -> str | None:
    for value, pattern in _ALERT_TYPES:
        if pattern.search(question):
            return value
    return None


def _source(question: str) -> str | None:
    found = [value for value, pattern in _SOURCE_WORDS if pattern.search(question)]
    if _alert_type(question) and "alerts" not in found:
        found.append("alerts")
    if not found:
        return None
    return found[0] if len(found) == 1 else "all"


def _level(question: str) -> str | None:
    for value, pattern in _LEVELS:
        if pattern.search(question):
            return value
    return None


def _tags(question: str, vocab: Vocabulary) -> list[str]:
    tags = [t for t in vocab.evidence_tags if _find_phrase(question, t) >= 0]
    for m in re.finditer(r"\btag(?:ged)?\s+(?:as\s+|with\s+)?[\"']?([a-z0-9][a-z0-9_-]{1,40})", question.lower()):
        if m.group(1) not in tags and m.group(1) not in ("with", "as", "from", "in"):
            tags.append(m.group(1))
    return tags[:5]


def _report_type(question: str, vocab: Vocabulary) -> str | None:
    hits = [t for t in vocab.report_types if len(t) >= 3 and _find_phrase(question, t) >= 0]
    return max(hits, key=len) if hits else None


def intents(question: str) -> list[str]:
    lowered = question.lower()
    found = [name for name, pattern in _INTENTS if pattern.search(lowered)]
    if "compare_periods" in found and "trend_summary" in found:
        found.remove("trend_summary")
    if "manual_report_search" in found and "trend_summary" in found:
        found.remove("trend_summary")
    if "camera_reliability" in found and "evidence_gaps" in found and not match_case(lowered):
        found.remove("evidence_gaps")
    if "case_list" in found and "trend_summary" in found:
        found.remove("trend_summary")
    if "evidence_gaps" in found and "evidence_search" in found:
        found.remove("evidence_search")
    return found


def plan(
    question: str,
    context: dict[str, Any] | None,
    vocab: Vocabulary,
    *,
    context_case: dict[str, str] | None = None,
) -> Plan:
    """Tool calls for ``question``. ``context`` is the previous turn's context; ``context_case`` the case of an
    incident-scoped session ("Investigate with AI")."""
    previous = dict(context or {})
    refusal = classify_request(question, vocab.role)
    if refusal is not None and refusal.kind != "ACCUSATION":
        return Plan([], previous, refusal)

    follow_up = bool(FOLLOW_UP.search(question))
    explicit_case = match_case(question)
    case = explicit_case or (previous.get("case") if follow_up else None) or context_case
    if refusal is not None:  # an accusation: no person is named, but the case's records may still be shown
        shown = [ToolCall("case_timeline", {"case": case})] if case else []
        return Plan(shown, {**previous, **({"case": case} if case else {})}, refusal)

    found = intents(question)
    if explicit_case and not [t for t in found if t != "trend_summary"]:
        found = list(CASE_TOOLS)  # "Tell me about INC-000012"
    if "case_timeline" in found and re.search(r"\binvestigat\w*\b", question, re.IGNORECASE):
        found = [*found, *[t for t in CASE_TOOLS if t not in found]]
    if case is None:
        found = [t for t in found if t not in CASE_TOOLS]
    if not found:
        if case is not None and (explicit_case or context_case):
            found = list(CASE_TOOLS)
        elif follow_up and previous.get("tools"):
            found = [t for t in previous["tools"] if t not in CASE_TOOLS or case]
    if not found:
        return Plan([], previous, help=True)

    notes: list[str] = []
    periods = dates.parse_periods(question, vocab.now, vocab.time_zone)
    inherited = previous.get("period") if follow_up else None
    if periods:
        period = periods[0]
    elif inherited:
        period = dates.Period.from_dict(inherited)
    elif "compare_periods" in found:
        period = dates.parse_periods("this month", vocab.now, vocab.time_zone)[0]
    else:
        period = dates.default_period(vocab.now)
        if any(t not in (*CASE_TOOLS, "evidence_search") for t in found):
            notes.append(f"No period was given, so I used {period.label}.")
    previous_period: dates.Period | None = None
    if "compare_periods" in found:
        if len(periods) >= 2:
            previous_period = periods[1]
        else:
            previous_period = dates.previous_period(
                period, vocab.time_zone, vocab.now, year_over_year=bool(_YOY.search(question))
            )

    location = match_location(question, vocab)
    if location is None and follow_up:
        # "those" / "there": the place of the previous answer (its top hotspot first).
        location = LocationRef.from_dict(previous.get("focus_location") or previous.get("location"))
    source = _source(question) or (previous.get("source") if follow_up else None)
    alert_type = _alert_type(question) or (previous.get("alert_type") if follow_up else None)
    level = _level(question) or "building"
    camera = match_camera(question, vocab)
    window = WINDOW.search(question)

    calls: list[ToolCall] = []
    for name in sorted(dict.fromkeys(found), key=ORDER.index):
        args: dict[str, Any] = {}
        if name in CASE_TOOLS:
            args["case"] = case
            if name == "related_events":
                args["window_minutes"] = max(5, min(240, int(window.group(1)))) if window else 30
        elif name == "evidence_search":
            if periods or inherited:
                args["period"] = period.to_dict()
            tags = _tags(question, vocab)
            if tags:
                args["tags"] = tags
            if camera:
                args["camera"] = camera
            if case:
                args["case"] = case
        else:
            args["period"] = period.to_dict()
        if location is not None and name not in (*CASE_TOOLS, "location_hotspots", "audit_activity"):
            args["location"] = location.to_dict()
        if name == "compare_periods" and previous_period is not None:
            args["previous_period"] = previous_period.to_dict()
        if name in ("trend_summary", "compare_periods") and alert_type:
            args["alert_type"] = alert_type
        if name == "peak_times":
            args["source"] = source or "all"
        if name == "location_hotspots":
            args["level"] = level
            args["source"] = source or "all"
        if name == "case_list":
            args["source"] = "alerts" if source == "alerts" else "incidents"
            args["unresolved_only"] = bool(_UNRESOLVED.search(question))
        if name == "manual_report_search":
            report_type = _report_type(question, vocab)
            if report_type:
                args["report_type"] = report_type
        try:
            validate(name, args, vocab.role)
        except ArgumentError:
            continue
        calls.append(ToolCall(name, args))

    new_context: dict[str, Any] = {
        **previous,
        "period": period.to_dict(),
        "previous_period": previous_period.to_dict() if previous_period else None,
        "location": location.to_dict() if location else None,
        "source": source or previous.get("source"),
        "alert_type": alert_type,
        "tools": [c.name for c in calls],
    }
    if case:
        new_context["case"] = case
    return Plan(calls, new_context, notes=notes)


# --- LLM planner output → exact tool arguments ----------------------------------------------------------------------


def normalise_llm_calls(
    raw: list[dict[str, Any]],
    question: str,
    context: dict[str, Any],
    vocab: Vocabulary,
    *,
    context_case: dict[str, str] | None = None,
) -> list[ToolCall]:
    """Validated tool calls from an LLM planner's simple arguments; raises ``ArgumentError`` on anything invalid.
    Periods are parsed with ``dates.py`` and places matched against the campus vocabulary — never taken verbatim."""
    calls: list[ToolCall] = []
    for item in raw[:6]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ArgumentError("each tool call needs a name")
        name = item["name"]
        given = item.get("arguments") or {}
        if not isinstance(given, dict):
            raise ArgumentError("arguments must be an object")
        args: dict[str, Any] = {}
        for key, value in given.items():
            if key in ("period", "previous_period"):
                found = dates.parse_periods(str(value), vocab.now, vocab.time_zone) if value else []
                if found:
                    args[key] = found[0].to_dict()
                elif context.get(key):
                    args[key] = context[key]
            elif key == "location":
                loc = match_location(str(value), vocab) if value else None
                if loc is not None:
                    args["location"] = loc.to_dict()
            elif key == "case":
                ref = match_case(str(value)) if value else None
                args["case"] = ref or context.get("case") or context_case
            elif key == "camera":
                cam = match_camera(str(value), vocab) if value else None
                if cam:
                    args["camera"] = cam
            elif key == "tags":
                args["tags"] = [str(t)[:40] for t in (value if isinstance(value, list) else [value])][:5]
            else:
                args[key] = value
        if name not in CASE_TOOLS and name != "evidence_search" and "period" not in args:
            args["period"] = (context.get("period") if FOLLOW_UP.search(question) else None) or dates.default_period(
                vocab.now
            ).to_dict()
        if name == "compare_periods" and "previous_period" not in args:
            current = dates.Period.from_dict(args["period"])
            args["previous_period"] = dates.previous_period(current, vocab.time_zone, vocab.now).to_dict()
        if name in CASE_TOOLS and not args.get("case"):
            args["case"] = context.get("case") or context_case
            if not args["case"]:
                raise ArgumentError(f"{name} needs a case")
        validate(name, args, vocab.role)
        calls.append(ToolCall(name, args))
    return calls
