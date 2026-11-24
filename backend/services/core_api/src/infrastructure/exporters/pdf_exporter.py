"""Reports & Analytics as PDF and CSV (decision D-024). Built in memory with ReportLab; nothing is stored.

Charts follow the project's data-visualisation rules: one axis, thin bars with gaps, categorical colours in a
fixed order (blue, orange, aqua), a legend whenever there are two series, and every number also in a table.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape

from src.infrastructure.exporters.csv_exporter import sectioned_csv

SERIES = ("#2a78d6", "#eb6834", "#1baf7a")
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"


def _bytes(value: float | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _type(value: str) -> str:
    return {"WEAPON": "Weapon", "FIGHT_VIOLENCE": "Fight / violence", "RESTRICTED_AREA": "Restricted area"}.get(
        value, value.replace("_", " ").title()
    )


def report_sections(report: dict[str, Any]) -> list[tuple[str, list[str], list[list[Any]]]]:
    """The report as titled tables (the same content in CSV and PDF)."""
    o = report["overview"]
    r = report["response_times"]
    storage = report["storage"]
    return [
        (
            "Overview",
            ["Measure", "Value"],
            [
                ["Alerts", o["alerts_total"]],
                ["High-priority alerts", o["alerts_high"]],
                ["Alerts awaiting acknowledgement (now)", o["alerts_pending"]],
                ["Active cases (now)", o["incidents_active"]],
                ["Cases opened", o["incidents_opened"]],
                ["Cases resolved", o["incidents_resolved"]],
                ["Manual reports", o["manual_reports"]],
                [
                    "Cameras (online / offline / total)",
                    f"{o['cameras_online']} / {o['cameras_offline']} / {o['cameras_total']}",
                ],
                ["Active Guards (stationed)", f"{o['guards_active']} ({o['guards_stationed']})"],
                ["Guard acknowledgement rate", _percent(o.get("guard_response_rate_percent"))],
                ["People observed by Re-ID", report.get("person_observations", 0)],
            ],
        ),  # fmt: skip
        (
            "Alerts by type",
            ["Type", "Priority", "Alerts"],
            [[_type(x["alert_type"]), x["priority"], x["count"]] for x in report["alerts_by_type"]],
        ),  # fmt: skip
        (
            "Alerts per day",
            ["Day", "High", "Standard", "Cases opened", "Manual reports"],
            [[d["day"], d["high"], d["standard"], d["incidents"], d["manual_reports"]] for d in report["daily"]],
        ),
        (
            "Response times",
            ["Measure", "Average", "Median", "Count"],
            [
                [
                    "Alert to first acknowledgement",
                    _duration(r["acknowledge_avg_seconds"]),
                    _duration(r["acknowledge_median_seconds"]),
                    r["acknowledged"],
                ],
                [
                    "Alert to first “I'm Responding”",
                    _duration(r["respond_avg_seconds"]),
                    _duration(r["respond_median_seconds"]),
                    r["responded"],
                ],
                [
                    "Alert to resolution",
                    _duration(r["resolve_avg_seconds"]),
                    _duration(r["resolve_median_seconds"]),
                    r["resolved"],
                ],
                [
                    "Case opened to resolved",
                    _duration(r["incident_resolve_avg_seconds"]),
                    _duration(r["incident_resolve_median_seconds"]),
                    r["incidents_resolved"],
                ],
            ],
        ),  # fmt: skip
        (
            "Guard activity",
            [
                "Guard ID",
                "Name",
                "Received",
                "Acknowledged",
                "Responding",
                "Under control",
                "More Guards",
                "Manual reports",
            ],
            [
                [
                    g["guard_id"],
                    g["full_name"],
                    g["received"],
                    g["acknowledged"],
                    g["responding"],
                    g["under_control"],
                    g["additional_required"],
                    g["manual_reports"],
                ]
                for g in report["guard_activity"]
            ],
        ),  # fmt: skip
        (
            "Manual reports",
            ["Type", "Status", "Reports", "Linked to cases"],
            [
                [m["report_type"], m["status"], m["count"], m["linked_to_cases"]]
                for m in report["manual_reports"]["by_type"]
            ],
        ),  # fmt: skip
        (
            "By location",
            ["Building", "Floor", "Area", "Alerts", "Cases", "Manual reports"],
            [
                [
                    x["building"] or "No location",
                    x["floor"] or "",
                    x["area"] or "",
                    x["alerts"],
                    x["incidents"],
                    x["manual_reports"],
                ]
                for x in report["locations"]
            ],
        ),  # fmt: skip
        (
            "Camera uptime",
            ["Camera", "State now", "Uptime", "Known hours", "Went offline"],
            [
                [c["camera_name"], c["state"], _percent(c["uptime_percent"]), c["known_hours"], c["failures"]]
                for c in report["camera_uptime"]["cameras"]
            ],
        ),  # fmt: skip
        (
            "Storage",
            ["Measure", "Value"],
            [
                ["Recordings kept", f"{_bytes(storage['recordings_bytes'])} in {storage['recordings_segments']} files"],
                ["Oldest recording kept", storage["oldest_recording"] or "—"],
                ["Evidence kept", f"{_bytes(storage['evidence_bytes'])} in {storage['evidence_items']} items"],
                ["Recorded per day (average in period)", _bytes(storage["average_bytes_per_day"])],
                [
                    "Days until the disk is full at that rate",
                    "—" if storage["projected_days_until_full"] is None else storage["projected_days_until_full"],
                ],
            ]
            + [
                [
                    f"Disk {n['node']}",
                    f"{_bytes(n['used_bytes'])} of {_bytes(n['total_bytes'])} ({n['percent_used']:.1f}%, {n['level']})",
                ]
                for n in storage["nodes"]
            ],
        ),  # fmt: skip
    ]


def report_csv(report: dict[str, Any]) -> bytes:
    header = [("Reports & Analytics", ["Campus", "From", "To", "Time zone"],
               [[report["campus"], report["period"]["since"], report["period"]["until"],
                 report["period"]["time_zone"]]])]  # fmt: skip
    return sectioned_csv(header + report_sections(report))


def _bar_chart(days: list[dict[str, Any]]) -> Any:
    from reportlab.graphics.charts.barcharts import VerticalBarChart  # type: ignore[import-untyped]
    from reportlab.graphics.charts.legends import Legend  # type: ignore[import-untyped]
    from reportlab.graphics.shapes import Drawing, String  # type: ignore[import-untyped]
    from reportlab.lib import colors  # type: ignore[import-untyped]

    drawing = Drawing(500, 190)
    drawing.add(
        String(0, 178, "Alerts per day", fontName="Helvetica-Bold", fontSize=10, fillColor=colors.HexColor(INK))
    )
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 30, 30, 450, 130
    chart.data = [[d["high"] for d in days] or [0], [d["standard"] for d in days] or [0]]
    chart.categoryAxis.categoryNames = [d["day"][5:] for d in days] or [""]
    chart.categoryAxis.labels.fontSize = 6
    chart.categoryAxis.labels.angle = 45 if len(days) > 10 else 0
    chart.categoryAxis.labels.dy = -6
    chart.categoryAxis.strokeColor = colors.HexColor(GRID)
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.strokeColor = colors.HexColor(GRID)
    chart.valueAxis.gridStrokeColor = colors.HexColor(GRID)
    chart.valueAxis.visibleGrid = True
    chart.barSpacing = 1
    chart.groupSpacing = 6
    for index, color in enumerate(SERIES[:2]):
        chart.bars[index].fillColor = colors.HexColor(color)
        chart.bars[index].strokeColor = None
    drawing.add(chart)
    legend = Legend()
    legend.x, legend.y = 360, 182
    legend.fontSize = 8
    legend.columnMaximum = 1
    legend.deltax = 70
    legend.alignment = "right"
    legend.colorNamePairs = [(colors.HexColor(SERIES[0]), "High"), (colors.HexColor(SERIES[1]), "Standard")]
    drawing.add(legend)
    return drawing


def report_pdf(report: dict[str, Any], *, organization: str, generated_by: str) -> bytes:
    from reportlab.lib import colors  # type: ignore[import-untyped]
    from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
    from reportlab.lib.styles import getSampleStyleSheet  # type: ignore[import-untyped]
    from reportlab.lib.units import mm  # type: ignore[import-untyped]
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # type: ignore[import-untyped]

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title="Reports & Analytics", author=generated_by,
    )  # fmt: skip
    styles = getSampleStyleSheet()
    period = report["period"]
    story: list[Any] = [
        Paragraph("Reports &amp; Analytics", styles["Title"]),
        Paragraph(
            f"{escape(organization)} — {escape(report['campus'])}<br/>"
            f"Period: {period['since'][:16].replace('T', ' ')} to "
            f"{period['until'][:16].replace('T', ' ')} (UTC; days counted in {period['time_zone']})<br/>"
            f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by {escape(generated_by)}",
            styles["Normal"],
        ),
        Spacer(1, 6 * mm),
    ]
    if report["daily"]:
        story += [_bar_chart(report["daily"]), Spacer(1, 4 * mm)]
    for title, header, rows in report_sections(report):
        story.append(Paragraph(title, styles["Heading2"]))
        body = [header] + (
            [[str(c) for c in row] for row in rows] or [["No data in this period"] + [""] * (len(header) - 1)]
        )
        table = Table(body, repeatRows=1, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                    ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(INK)),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(MUTED)),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor(GRID)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story += [table, Spacer(1, 4 * mm)]
    document.build(story)
    return buffer.getvalue()
