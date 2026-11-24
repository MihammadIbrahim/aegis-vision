"""Grounded answers written from tool results only (decision D-027).

Every number in the text comes from a tool result (SQL under the caller's RLS); a tool that found nothing produces an
explicit "no matching records" sentence. The output is the answer of ``AI_LLM_PROVIDER=deterministic`` and the
"facts" an LLM may only rephrase (``guardrails.grounded`` checks it added no number).

Blocks for the web app: ``metrics`` {items: [{label, value}]}, ``table`` {title, columns, rows}, ``bars``
{title, items: [{label, value}]}, ``timeline`` {title, items: [{at, label, detail, entity_type?, entity_id?}]},
``notice`` {tone: info|warning, text}.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from assistant_engine.catalog import ALERT_TYPE_LABELS
from assistant_engine.types import Citation, ToolResult

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
SOURCE_LABELS = {"alerts": "alerts", "incidents": "cases", "manual_reports": "manual reports", "all": "security events"}
STATUS_LABELS = {
    "NEW": "new",
    "ACKNOWLEDGED": "acknowledged",
    "RESPONDING": "responding",
    "ESCALATED": "escalated",
    "RESOLVED": "resolved",
    "DISMISSED": "dismissed",
    "OPEN": "open",
    "UNDER_INVESTIGATION": "under investigation",
}
GAP_LABELS = {
    "CAMERA_OFFLINE": "camera offline",
    "OFFLINE_DURING_WINDOW": "camera went offline during the event",
    "NO_RECORDING": "no recording",
    "PARTIAL_RECORDING": "partial recording",
    "CAMERA_REMOVED": "camera removed",
    "NO_CAMERA": "no camera linked",
    "NO_EVIDENCE_SAVED": "no evidence saved",
    "UNVERIFIED_REID": "unverified Re-ID candidates",
}


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    if s < 86400:
        return f"{s // 3600} h {(s % 3600) // 60:02d} min"
    return f"{s // 86400} d {(s % 86400) // 3600} h"


def local_time(value: str | None, tz: ZoneInfo) -> str:
    if not value:
        return "—"
    return datetime.fromisoformat(value).astimezone(tz).strftime("%Y-%m-%d %H:%M")


def change(current: int, previous: int) -> str:
    diff = current - previous
    if previous == 0:
        return f"{diff:+d}" if diff else "no change"
    return f"{diff:+d}, {diff / previous * 100:+.0f}%"


def _where(args: dict[str, Any]) -> str:
    loc = args.get("location")
    return f" at {loc['name']}" if loc else ""


def _period(args: dict[str, Any]) -> str:
    period = args.get("period")
    return period["label"] if period else "all time"


@dataclass(slots=True)
class Section:
    text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    focus_location: dict[str, Any] | None = None


Formatter = Callable[[ToolResult, ZoneInfo], Section]


def _empty(result: ToolResult, what: str) -> Section:
    return Section(f"No {what} were found{_where(result.arguments)} in {_period(result.arguments)}.")


def trend_summary(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    total = d["alerts_total"] + d["incidents_total"] + d["manual_reports_total"]
    if total == 0:
        return _empty(r, "alerts, cases or manual reports")
    by_type = ", ".join(
        f"{x['count']} {ALERT_TYPE_LABELS.get(x['type'], x['type']).lower()}" for x in d["alerts_by_type"]
    )
    text = (
        f"In {_period(r.arguments)}{_where(r.arguments)}: {d['alerts_total']} alerts"
        + (f" ({by_type}; {d['alerts_high']} high priority)" if d["alerts_total"] else "")
        + f", {d['incidents_total']} cases opened and {d['manual_reports_total']} Guard manual reports."
    )
    top = d.get("most_frequent") or []
    if top:
        names = " and ".join(f"{ALERT_TYPE_LABELS.get(t['category'], t['category'])}" for t in top)
        text += f" The most frequent type was {names} ({top[0]['count']}{' each' if len(top) > 1 else ''})."
    rows = [[ALERT_TYPE_LABELS.get(x["type"], x["type"]), x["count"]] for x in d["alerts_by_type"]]
    rows += [[f"Manual report: {x['type']}", x["count"]] for x in d["manual_reports_by_type"]]
    blocks: list[dict[str, Any]] = [
        {
            "type": "metrics",
            "items": [
                {"label": "Alerts", "value": d["alerts_total"]},
                {"label": "High priority", "value": d["alerts_high"]},
                {"label": "Cases opened", "value": d["incidents_total"]},
                {"label": "Manual reports", "value": d["manual_reports_total"]},
            ],
        }
    ]
    if rows:
        blocks.append({"type": "table", "title": "By type", "columns": ["Type", "Count"], "rows": rows})
    return Section(text, blocks, r.citations)


def peak_times(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    if d["total"] == 0:
        return _empty(r, SOURCE_LABELS[d["source"]])
    hours = ", ".join(f"{h['hour']:02d}:00–{(h['hour'] + 1) % 24:02d}:00 ({h['count']})" for h in d["peak_hours"])
    days = ", ".join(f"{WEEKDAYS[x['day']]} ({x['count']})" for x in d["peak_days"])
    w = d["peak_window"]
    text = (
        f"Across {d['total']} {SOURCE_LABELS[d['source']]} in {_period(r.arguments)}{_where(r.arguments)} (times in "
        f"{d['time_zone']}), the busiest hours were {hours}. The busiest 2-hour window was "
        f"{w['start_hour']:02d}:00–{(w['start_hour'] + 2) % 24:02d}:00 ({w['count']}). The busiest days were {days}."
    )
    blocks: list[dict[str, Any]] = [
        {
            "type": "bars",
            "title": "By hour of day",
            "items": [{"label": f"{h:02d}:00", "value": v} for h, v in enumerate(d["by_hour"])],
        },
        {
            "type": "bars",
            "title": "By day of week",
            "items": [{"label": WEEKDAYS[i][:3], "value": v} for i, v in enumerate(d["by_weekday"])],
        },
    ]
    return Section(text, blocks, r.citations)


def compare_periods(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    cur, prev = d["current"], d["previous"]
    if not any(row["current"] or row["previous"] for row in d["rows"]):
        return Section(
            f"No alerts, cases or manual reports were found{_where(r.arguments)} in {cur['label']} "
            f"or in {prev['label']}."
        )
    lines = [
        f"{row['label']}: {row['current']} vs {row['previous']} ({change(row['current'], row['previous'])})"
        for row in d["rows"]
        if row["current"] or row["previous"]
    ]
    text = f"{cur['label'].capitalize()} compared with {prev['label']}{_where(r.arguments)}: " + "; ".join(lines) + "."
    if cur.get("partial"):
        text += f" Note: {cur['label']} is not over yet."
    blocks: list[dict[str, Any]] = [
        {
            "type": "table",
            "title": "Comparison",
            "columns": ["Measure", cur["label"], prev["label"], "Change"],
            "rows": [
                [row["label"], row["current"], row["previous"], change(row["current"], row["previous"])]
                for row in d["rows"]
            ],
        }
    ]
    return Section(text, blocks, r.citations)


def location_hotspots(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    items = d["items"]
    if not items:
        return _empty(r, f"{SOURCE_LABELS[d['source']]} with a place")
    level = d["level"]
    ranked = "; ".join(
        f"{i + 1}. {x['name']} — {x['total']} ({x['alerts']} alerts, {x['incidents']} cases, "
        f"{x['manual_reports']} manual reports)"
        for i, x in enumerate(items[:5])
    )
    text = f"Top {level}s by {SOURCE_LABELS[d['source']]} in {_period(r.arguments)}: {ranked}."
    if d.get("without_location"):
        text += f" {d['without_location']} records had no {level} and are not ranked."
    blocks: list[dict[str, Any]] = [
        {
            "type": "table",
            "title": f"Hotspots by {level}",
            "columns": [level.capitalize(), "Total", "Alerts", "Cases", "Manual reports"],
            "rows": [[x["name"], x["total"], x["alerts"], x["incidents"], x["manual_reports"]] for x in items[:10]],
        }
    ]
    top = next((x for x in items if x.get("id")), None)
    focus = {"level": level.upper(), "id": top["id"], "name": top["name"]} if top else None
    return Section(text, blocks, r.citations, focus)


def camera_reliability(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    items = [x for x in d["items"] if x["offline_events"] or x["offline_seconds"]]
    if not items:
        return Section(
            f"No camera{_where(r.arguments)} went offline in {_period(r.arguments)} "
            f"({d['cameras_total']} cameras checked)."
        )
    ranked = "; ".join(
        f"{x['name']} — offline {x['offline_events']} time{'s' if x['offline_events'] != 1 else ''}, "
        f"{duration(x['offline_seconds'])} in total"
        for x in items[:5]
    )
    text = (
        f"{len(items)} of {d['cameras_total']} cameras{_where(r.arguments)} went offline in {_period(r.arguments)}. "
        f"Most often: {ranked}."
    )
    now_off = [x["name"] for x in d["items"] if x["current_state"] == "OFFLINE"]
    if now_off:
        text += f" Offline now: {', '.join(now_off[:5])}."
    blocks: list[dict[str, Any]] = [
        {
            "type": "table",
            "title": "Camera reliability",
            "columns": ["Camera", "Place", "Offline events", "Offline time", "Now"],
            "rows": [
                [
                    x["name"],
                    x["location"] or "—",
                    x["offline_events"],
                    duration(x["offline_seconds"]),
                    x["current_state"].lower(),
                ]
                for x in items[:10]
            ],
        }
    ]
    return Section(text, blocks, r.citations)


def _case_head(case: dict[str, Any]) -> str:
    handler = case.get("assigned_name") or "nobody yet"
    return (
        f"{case['reference']} · {case['title']} — {STATUS_LABELS.get(case['status'], case['status'].lower())}, "
        f"{case['priority'].lower()} priority, handled by {handler}"
    )


def _no_case(r: ToolResult) -> Section:
    ref = (r.arguments.get("case") or {}).get("reference", "That case")
    return Section(f"{ref} was not found on this campus (or it is not visible to you).")


def case_timeline(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    if not d.get("case"):
        return _no_case(r)
    events = d["events"]
    text = f"{_case_head(d['case'])}. The timeline has {len(events)} entries"
    if events:
        first, last = events[0], events[-1]
        text += (
            f", from {local_time(first['at'], tz)} ({first['label']}) to {local_time(last['at'], tz)} ({last['label']})"
        )
    counts = d.get("counts", {})
    text += (
        f". Linked: {counts.get('alerts', 0)} alerts, {counts.get('manual_reports', 0)} manual reports, "
        f"{counts.get('evidence', 0)} evidence items, {counts.get('messages', 0)} chat messages."
    )
    blocks: list[dict[str, Any]] = [
        {
            "type": "timeline",
            "title": f"{d['case']['reference']} timeline",
            "items": [
                {
                    "at": local_time(e["at"], tz),
                    "label": e["label"],
                    "detail": e.get("detail") or "",
                    **({"entity_type": e["entity_type"], "entity_id": e["entity_id"]} if e.get("entity_type") else {}),
                }
                for e in events
            ],
        }
    ]
    return Section(text, blocks, r.citations)


def related_events(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    if not d.get("case"):
        return _no_case(r)
    window = d["window_minutes"]
    anchor = d.get("anchor") or {}
    where = anchor.get("building") or "the same place"
    if not anchor.get("at"):
        return Section(f"{d['case']['reference']} has no time to correlate other events with.")
    a, m, o = len(d["alerts"]), len(d["manual_reports"]), len(d["reid_observations"])
    if a + m + o == 0:
        return Section(
            f"No other alerts, manual reports or Re-ID observations were found within ±{window} minutes of "
            f"{d['case']['reference']} ({local_time(anchor['at'], tz)}) in {where} or on nearby cameras."
        )
    text = (
        f"Within ±{window} minutes of {d['case']['reference']} ({local_time(anchor['at'], tz)}) in {where} or on "
        f"nearby cameras: {a} other alerts, {m} manual reports and {o} Re-ID observations."
    )
    if o:
        text += " Re-ID observations are possible sightings for review, not identities."
    rows = (
        [[local_time(x["at"], tz), "Alert", x["label"], x.get("place") or "—"] for x in d["alerts"]]
        + [[local_time(x["at"], tz), "Manual report", x["label"], x.get("place") or "—"] for x in d["manual_reports"]]
        + [
            [local_time(x["at"], tz), "Re-ID observation", x["label"], x.get("place") or "—"]
            for x in d["reid_observations"]
        ]
    )
    rows.sort(key=lambda row: row[0])
    blocks: list[dict[str, Any]] = [
        {
            "type": "table",
            "title": f"Related events (±{window} min)",
            "columns": ["Time", "Kind", "What", "Where"],
            "rows": rows,
        }
    ]
    return Section(text, blocks, r.citations)


def evidence_gaps(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    if not d.get("case"):
        return _no_case(r)
    gaps = d["gaps"]
    if not gaps:
        text = (
            f"No evidence gaps found for {d['case']['reference']}: {d['evidence_count']} evidence items are saved and "
            f"the {d['checked_cameras']} cameras checked recorded the event."
        )
        return Section(text, [], r.citations)
    listed = "; ".join(
        f"{GAP_LABELS.get(g['kind'], g['kind'].lower())}{' — ' + g['camera_name'] if g.get('camera_name') else ''}"
        f"{' (' + g['detail'] + ')' if g.get('detail') else ''}"
        for g in gaps
    )
    text = (
        f"Possible evidence gaps for {d['case']['reference']} ({len(gaps)}): {listed}. "
        f"{d['evidence_count']} evidence items are saved; {d['checked_cameras']} cameras were checked."
    )
    blocks: list[dict[str, Any]] = [
        {
            "type": "table",
            "title": "Evidence and footage gaps",
            "columns": ["Gap", "Camera", "Detail"],
            "rows": [
                [GAP_LABELS.get(g["kind"], g["kind"]), g.get("camera_name") or "—", g.get("detail") or ""] for g in gaps
            ],
        }
    ]
    return Section(text, blocks, r.citations)


def guard_response(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    if d["alerts"] == 0:
        return _empty(r, "alerts (so no Guard response times)")
    th = d["thresholds"]
    text = (
        f"{d['alerts']} alerts in {_period(r.arguments)}{_where(r.arguments)}: the first acknowledgement came after "
        f"{duration(d['acknowledge_avg_seconds'])} on average ({d['acknowledged']} acknowledged), the first "
        f'"I\'m Responding" after {duration(d["respond_avg_seconds"])} ({d["responded"]} responded), and resolution '
        f"after {duration(d['resolve_avg_seconds'])} ({d['resolved']} resolved). Guards asked for Additional Guards "
        f"{d['additional_guards_requests']} times; automatic escalation fired {d['auto_escalations']} times "
        f"(after {th['high_seconds']} s for high and {th['standard_seconds']} s for standard priority"
        f"{'' if th['enabled'] else ', currently switched off'}) and staff escalated {d['manual_escalations']} times; "
        f"{d['escalated_alerts']} alerts ({d['escalation_rate_pct']}%) were escalated. "
        f"{d['unacknowledged']} alerts were never acknowledged."
    )
    places = [x for x in d["by_location"] if x["unacknowledged"] or x["escalated"]]
    if places:
        text += (
            " Most unacknowledged or escalated: "
            + "; ".join(
                f"{x['name']} ({x['unacknowledged']} unacknowledged, {x['escalated']} escalated)" for x in places[:3]
            )
            + "."
        )
    st, sb = d["by_mode"]["stationed"], d["by_mode"]["standby"]
    text += (
        f" Stationed Guards received {st['received']} alerts and acknowledged {st['acknowledged']}; Standby Guards "
        f"received {sb['received']} and acknowledged {sb['acknowledged']}."
    )
    blocks: list[dict[str, Any]] = [
        {
            "type": "metrics",
            "items": [
                {"label": "Avg acknowledge", "value": duration(d["acknowledge_avg_seconds"])},
                {"label": "Avg response", "value": duration(d["respond_avg_seconds"])},
                {"label": "Avg resolution", "value": duration(d["resolve_avg_seconds"])},
                {"label": "Escalation rate", "value": f"{d['escalation_rate_pct']}%"},
            ],
        },
        {
            "type": "table",
            "title": "Escalations and backup",
            "columns": ["Measure", "Count"],
            "rows": [
                ["Additional Guards requests", d["additional_guards_requests"]],
                ["Automatic escalations", d["auto_escalations"]],
                ["Staff escalations", d["manual_escalations"]],
                ["Never acknowledged", d["unacknowledged"]],
            ],
        },
        {
            "type": "table",
            "title": "Stationed vs Standby",
            "columns": ["Mode", "Received", "Acknowledged", "Responding"],
            "rows": [
                ["Stationed", st["received"], st["acknowledged"], st["responding"]],
                ["Standby", sb["received"], sb["acknowledged"], sb["responding"]],
            ],
        },
    ]
    if places:
        blocks.append(
            {
                "type": "table",
                "title": "Places with unacknowledged or escalated alerts",
                "columns": ["Place", "Unacknowledged", "Escalated"],
                "rows": [[x["name"], x["unacknowledged"], x["escalated"]] for x in places[:10]],
            }
        )
    return Section(text, blocks, r.citations)


def audit_activity(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    scope = " (operational records and your own only)" if d.get("role_scope") == "SUPERVISOR" else ""
    if d["total"] == 0:
        return Section(f"No audit records{scope} were found in {_period(r.arguments)}.")
    cats = ", ".join(f"{x['category'].replace('_', ' ').lower()} {x['count']}" for x in d["by_category"][:6])
    text = f"{d['total']} audit records{scope} in {_period(r.arguments)}: {cats}."
    blocks: list[dict[str, Any]] = [
        {
            "type": "table",
            "title": "Most frequent actions",
            "columns": ["Action", "Count"],
            "rows": [[x["action"], x["count"]] for x in d["by_action"][:10]],
        }
    ]
    return Section(text, blocks, r.citations)


def manual_report_search(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    if d["total"] == 0:
        kind = f'"{r.arguments["report_type"]}" manual reports' if r.arguments.get("report_type") else "manual reports"
        return _empty(r, kind)
    types = ", ".join(f"{x['type']} {x['count']}" for x in d["by_type"][:6])
    text = (
        f"{d['total']} Guard manual reports{_where(r.arguments)} in {_period(r.arguments)} ({types}); "
        f"{d['reviewed']} reviewed and {d['submitted']} awaiting review, {d['linked']} linked to cases."
    )
    blocks: list[dict[str, Any]] = [
        {
            "type": "table",
            "title": "Latest manual reports",
            "columns": ["Time", "Type", "Place", "Status", "Summary"],
            "rows": [
                [local_time(x["at"], tz), x["type"], x.get("place") or "—", x["status"].lower(), x["excerpt"]]
                for x in d["items"]
            ],
        }
    ]
    return Section(text, blocks, r.citations)


def evidence_search(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    filters = []
    if r.arguments.get("tags"):
        filters.append("tagged " + ", ".join(r.arguments["tags"]))
    if r.arguments.get("camera"):
        filters.append(f"from {r.arguments['camera']['name']}")
    if r.arguments.get("case"):
        filters.append(f"linked to {r.arguments['case'].get('reference', 'the case')}")
    described = (" " + " ".join(filters)) if filters else ""
    if d["total"] == 0:
        return Section(
            f"No evidence items{described}{_where(r.arguments)} were found"
            + (f" in {_period(r.arguments)}" if r.arguments.get("period") else "")
            + "."
        )
    linked = sum(1 for x in d["items"] if x.get("case_reference"))
    text = (
        f"{d['total']} evidence items{described}{_where(r.arguments)}"
        + (f" in {_period(r.arguments)}" if r.arguments.get("period") else "")
        + f"; {linked} of those shown are linked to cases."
    )
    blocks: list[dict[str, Any]] = [
        {
            "type": "table",
            "title": "Evidence",
            "columns": ["Saved", "Title", "Kind", "Camera", "Tags", "Case"],
            "rows": [
                [
                    local_time(x["saved_at"], tz),
                    x["title"],
                    x["kind"].lower(),
                    x.get("camera_name") or "—",
                    ", ".join(x["tags"]) or "—",
                    x.get("case_reference") or "—",
                ]
                for x in d["items"]
            ],
        }
    ]
    return Section(text, blocks, r.citations)


def case_list(r: ToolResult, tz: ZoneInfo) -> Section:
    d = r.data
    what = ("unresolved " if r.arguments.get("unresolved_only") else "") + (
        "alerts" if d["source"] == "alerts" else "cases"
    )
    if d["total"] == 0:
        return _empty(r, what)
    handlers = "; ".join(f"{x['name']} ({x['count']})" for x in d["handlers"])
    text = f"{d['total']} {what}{_where(r.arguments)} in {_period(r.arguments)}. Handled by: {handlers}."
    blocks: list[dict[str, Any]] = [
        {
            "type": "table",
            "title": what.capitalize(),
            "columns": ["Reference", "Title", "Status", "Handler", "Place"],
            "rows": [
                [
                    x["reference"],
                    x["title"],
                    STATUS_LABELS.get(x["status"], x["status"].lower()),
                    x["handler"] or "unassigned",
                    x.get("place") or "—",
                ]
                for x in d["items"]
            ],
        }
    ]
    return Section(text, blocks, r.citations)


FORMATTERS: dict[str, Formatter] = {
    "trend_summary": trend_summary,
    "peak_times": peak_times,
    "compare_periods": compare_periods,
    "location_hotspots": location_hotspots,
    "camera_reliability": camera_reliability,
    "case_timeline": case_timeline,
    "related_events": related_events,
    "evidence_gaps": evidence_gaps,
    "guard_response": guard_response,
    "audit_activity": audit_activity,
    "manual_report_search": manual_report_search,
    "evidence_search": evidence_search,
    "case_list": case_list,
}


def section(result: ToolResult, tz: ZoneInfo) -> Section:
    if result.outcome == "DENIED":
        return Section("That information is not available to your role.")
    if result.outcome == "ERROR":
        return Section(f"The {result.name.replace('_', ' ')} query failed, so that part could not be answered.")
    return FORMATTERS[result.name](result, tz)
