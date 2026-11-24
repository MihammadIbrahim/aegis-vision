"""The read-only tool catalogue of the AI Investigation & Security Analytics Chatbot (decision D-027).

One source of truth for: the deterministic planner, the LLM planner prompt (JSON schemas), argument validation, the
"Tools & queries used" drawer, the audit records and the fine-tuning datasets in ``backend/ml_training/chatbot``.
Every tool only reads; the implementations live in core_api and run under the caller's Row-Level Security.

Common argument shapes:
* ``period``  ``{"since": ISO-8601, "until": ISO-8601 (exclusive), "label": str}`` (resolved by ``dates.py``)
* ``location`` ``{"level": "BUILDING"|"FLOOR"|"AREA", "id": uuid, "name": str}`` (the caller's campus only)
* ``case``    ``{"reference": "INC-000012"}`` or ``{"id": uuid, "reference": ...}``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from assistant_engine.types import Role

ALERT_TYPES = ("WEAPON", "FIGHT_VIOLENCE", "RESTRICTED_AREA")
ALERT_TYPE_LABELS = {
    "WEAPON": "Weapon",
    "FIGHT_VIOLENCE": "Fight / Violence",
    "RESTRICTED_AREA": "Restricted Area",
    "MANUAL_REPORTS": "Guard manual reports",
}
SOURCES = ("alerts", "incidents", "manual_reports", "all")
LEVELS = ("building", "floor", "area")
AUDIT_CATEGORIES = (
    "ALERTS",
    "GUARD_RESPONSE",
    "INCIDENTS",
    "INCIDENT_CHAT",
    "CAMERA_LOCATION",
    "AI_SETTINGS",
    "EVIDENCE",
    "VIDEO",
    "PERSON_SEARCH",
    "MANUAL_REPORTS",
    "RETENTION",
    "AUTHENTICATION",
    "USER_MANAGEMENT",
    "PERSONNEL",
    "ORGANIZATION",
    "AI_ASSISTANT",
    "OTHER",
)

_PERIOD = {
    "type": "object",
    "properties": {"since": {"type": "string"}, "until": {"type": "string"}, "label": {"type": "string"}},
    "required": ["since", "until", "label"],
}
_LOCATION = {
    "type": "object",
    "properties": {
        "level": {"enum": ["BUILDING", "FLOOR", "AREA"]},
        "id": {"type": "string"},
        "name": {"type": "string"},
    },
    "required": ["level", "id", "name"],
}
_CASE = {"type": "object", "properties": {"id": {"type": "string"}, "reference": {"type": "string"}}}


@dataclass(frozen=True, slots=True)
class Capability:
    key: str
    title: str
    description: str
    examples: tuple[str, ...]


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "TRENDS",
        "Time-based trends & historical comparisons",
        "Totals for any year, quarter, month, week or date range; peak hours and days; month-over-month and "
        "year-over-year changes; the most frequent type.",
        (
            "How many incidents happened last year and which type was most frequent?",
            "Did weapon alerts increase this month compared with last month?",
        ),
    ),
    Capability(
        "HOTSPOTS",
        "Location & camera hotspots",
        "Buildings, floors, gates and areas with the most events; cameras that went offline most often; blind spots.",
        (
            "What are our peak incident hours and top hotspot buildings?",
            "Which cameras went offline most often this month?",
        ),
    ),
    Capability(
        "INVESTIGATION",
        "Deep incident investigation",
        "A case's full timeline, related events within ±30 minutes nearby, and a missing-evidence / footage-gap check.",
        ("Investigate INC-000012: timeline, related events and evidence gaps.",),
    ),
    Capability(
        "GUARD_PERFORMANCE",
        "Guard response & operational performance",
        "Average acknowledge, response and resolution times; Additional Guards requests and automatic escalations; "
        "Standby vs Stationed activity.",
        ("What is our average Guard response and escalation rate?",),
    ),
    Capability(
        "SEARCH",
        "Evidence & manual report search",
        "Guards' manual reports for a place and period; Evidence Vault items by tag, camera, place, date or case.",
        (
            "Summarize manual reports at the Main Gate over the last 3 months.",
            "Show evidence tagged weapon from last month.",
        ),
    ),
    Capability(
        "FOLLOW_UP",
        "Follow-up conversational investigation",
        "Keeps the period, place and case of the conversation so you can drill down without repeating them.",
        (
            "Which building had the most incidents in October?",
            "How many of those are still unresolved and who is handling them?",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    capability: str
    description: str
    parameters: dict[str, Any]
    roles: tuple[Role, ...] = ("ADMIN", "SUPERVISOR")

    def schema(self) -> dict[str, Any]:
        """The JSON schema given to an LLM planner (function-calling format)."""
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "trend_summary",
        "TRENDS",
        "Totals of alerts (by type and priority), cases (by status) and Guard manual reports (by type) in a period, "
        "and the most frequent type.",
        _object(
            {"period": _PERIOD, "location": _LOCATION, "alert_type": {"enum": list(ALERT_TYPES)}},
            ["period"],
        ),
    ),
    ToolSpec(
        "peak_times",
        "TRENDS",
        "Counts by hour of day and by day of week (organization time zone), with the peak hours, days and "
        "2-hour window.",
        _object(
            {"period": _PERIOD, "location": _LOCATION, "source": {"enum": list(SOURCES)}},
            ["period", "source"],
        ),
    ),
    ToolSpec(
        "compare_periods",
        "TRENDS",
        "Compares two periods (e.g. this month vs last month, or year over year) by alert type, cases and reports.",
        _object(
            {
                "period": _PERIOD,
                "previous_period": _PERIOD,
                "location": _LOCATION,
                "alert_type": {"enum": list(ALERT_TYPES)},
            },
            ["period", "previous_period"],
        ),
    ),
    ToolSpec(
        "location_hotspots",
        "HOTSPOTS",
        "Buildings, floors or areas (gates, zones) ranked by the number of alerts, cases and manual reports.",
        _object(
            {"period": _PERIOD, "level": {"enum": list(LEVELS)}, "source": {"enum": list(SOURCES)}},
            ["period", "level", "source"],
        ),
    ),
    ToolSpec(
        "camera_reliability",
        "HOTSPOTS",
        "Cameras ranked by how often and how long they were offline in a period (camera status changes).",
        _object({"period": _PERIOD, "location": _LOCATION}, ["period"]),
    ),
    ToolSpec(
        "case_timeline",
        "INVESTIGATION",
        "The full chronological timeline of one case: alerts, Guard acknowledgements and assessments, escalations, "
        "chat updates, notes, status changes, linked manual reports and evidence.",
        _object({"case": _CASE}, ["case"]),
    ),
    ToolSpec(
        "related_events",
        "INVESTIGATION",
        "Alerts, manual reports and Re-ID observations within ±window minutes of a case in the same building or on "
        "nearby cameras. Re-ID observations are never identities.",
        _object(
            {"case": _CASE, "window_minutes": {"type": "integer", "minimum": 5, "maximum": 240}},
            ["case"],
        ),
    ),
    ToolSpec(
        "evidence_gaps",
        "INVESTIGATION",
        "Missing evidence for a case: cameras offline or not recording during the event, no evidence saved, "
        "unverified Re-ID candidates still pending review.",
        _object({"case": _CASE}, ["case"]),
    ),
    ToolSpec(
        "guard_response",
        "GUARD_PERFORMANCE",
        "Average acknowledge, response and resolution times; Additional Guards requests; automatic and manual "
        "escalations; places with the most unacknowledged or escalated alerts; Standby vs Stationed breakdown.",
        _object({"period": _PERIOD, "location": _LOCATION}, ["period"]),
    ),
    ToolSpec(
        "audit_activity",
        "GUARD_PERFORMANCE",
        "Audit records of the period by category and action. Supervisors only see operational categories and their "
        "own records.",
        _object({"period": _PERIOD, "category": {"enum": list(AUDIT_CATEGORIES)}}, ["period"]),
    ),
    ToolSpec(
        "manual_report_search",
        "SEARCH",
        "Guards' manual security reports for a place and period: totals by type and review state, latest reports.",
        _object(
            {"period": _PERIOD, "location": _LOCATION, "report_type": {"type": "string"}},
            ["period"],
        ),
    ),
    ToolSpec(
        "evidence_search",
        "SEARCH",
        "Evidence Vault items by tag, camera, place, date or case, with their linked cases.",
        _object(
            {
                "period": _PERIOD,
                "location": _LOCATION,
                "tags": {"type": "array", "items": {"type": "string"}},
                "camera": {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}},
                "case": _CASE,
            },
            [],
        ),
    ),
    ToolSpec(
        "case_list",
        "FOLLOW_UP",
        "Cases (or alerts) in a period and place, optionally only the unresolved ones, with who is handling them.",
        _object(
            {
                "period": _PERIOD,
                "location": _LOCATION,
                "source": {"enum": ["incidents", "alerts"]},
                "unresolved_only": {"type": "boolean"},
            },
            ["source"],
        ),
    ),
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}


class ArgumentError(ValueError):
    """A tool call whose arguments do not match the catalogue (e.g. from an LLM planner)."""


def _check(value: Any, schema: dict[str, Any], path: str) -> None:
    if "enum" in schema:
        if value not in schema["enum"]:
            raise ArgumentError(f"{path}: {value!r} is not one of {schema['enum']}")
        return
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise ArgumentError(f"{path}: expected an object")
        for key in schema.get("required", []):
            if key not in value:
                raise ArgumentError(f"{path}.{key}: required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise ArgumentError(f"{path}: unknown {sorted(extra)}")
        for key, item in value.items():
            if key in properties:
                _check(item, properties[key], f"{path}.{key}")
    elif kind == "string":
        if not isinstance(value, str) or len(value) > 300:
            raise ArgumentError(f"{path}: expected a short string")
    elif kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ArgumentError(f"{path}: expected an integer")
        if not schema.get("minimum", value) <= value <= schema.get("maximum", value):
            raise ArgumentError(f"{path}: out of range")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ArgumentError(f"{path}: expected true or false")
    elif kind == "array":
        if not isinstance(value, list) or len(value) > 10:
            raise ArgumentError(f"{path}: expected a short list")
        for i, item in enumerate(value):
            _check(item, schema.get("items", {}), f"{path}[{i}]")


def validate(name: str, arguments: dict[str, Any], role: Role) -> ToolSpec:
    """The tool's spec if ``arguments`` match its schema and ``role`` may use it; ``ArgumentError`` otherwise."""
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        raise ArgumentError(f"unknown tool {name!r}")
    if role not in spec.roles:
        raise ArgumentError(f"{name} is not available to {role}")
    _check(arguments, spec.parameters, name)
    return spec


def catalogue_for_prompt() -> list[dict[str, Any]]:
    return [t.schema() for t in TOOLS]
