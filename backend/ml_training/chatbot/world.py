"""A synthetic campus used to generate training and evaluation questions (no real data).

The planner targets refer to these names; evaluation resolves them with the runtime's own vocabulary matching, so a
place, camera or date must resolve to exactly the same record and range as in production.
"""

from __future__ import annotations

from datetime import datetime

from assistant_engine.types import CameraRef, LocationRef, Vocabulary

BUILDINGS = ("Admin Block", "Library", "Hostel A", "Sports Complex", "North Fence", "Science Wing")
AREAS = (
    ("North Fence", None, "Main Gate"),
    ("North Fence", None, "Back Gate"),
    ("Admin Block", "Ground", "Lobby"),
    ("Library", "First", "Reading Hall"),
    ("Sports Complex", None, "Parking"),
    ("Hostel A", "Ground", "Common Room"),
)
CAMERAS = ("Gate Camera", "Lobby Cam", "Library Entrance", "Parking Cam 2", "Hostel Corridor", "Back Gate Cam")
REPORT_TYPES = ("Suspicious Person", "Property Damage", "Unattended Bag", "Fire Hazard", "Trespassing")
TAGS = ("weapon", "gate-breach", "fight", "theft", "vandalism")
CASE_NUMBERS = (3, 7, 12, 25, 41, 108)

PERIODS = (
    "last month",
    "this month",
    "last year",
    "this year",
    "last week",
    "this week",
    "in October",
    "in September",
    "in October 2025",
    "the last 7 days",
    "the last 3 months",
    "the last 30 days",
    "Q3 2025",
    "Q1",
    "yesterday",
    "today",
    "between 2026-10-01 and 2026-10-15",
    "in 2025",
    "since September",
)
COMPARISONS = (
    ("this month", "last month"),
    ("October", "September"),
    ("October", "October 2025"),
    ("Q3 2025", "Q2 2025"),
    ("this year", "last year"),
)


def vocabulary(now: datetime, time_zone: str = "Asia/Karachi", role: str = "ADMIN") -> Vocabulary:
    locations: list[LocationRef] = [LocationRef("BUILDING", f"b-{i}", name) for i, name in enumerate(BUILDINGS)]
    floors = sorted({(b, f) for b, f, _ in AREAS if f})
    locations += [LocationRef("FLOOR", f"f-{i}", f"{b} › {f}") for i, (b, f) in enumerate(floors)]
    locations += [
        LocationRef("AREA", f"a-{i}", " › ".join(p for p in (b, f, a) if p)) for i, (b, f, a) in enumerate(AREAS)
    ]
    return Vocabulary(
        now=now,
        time_zone=time_zone,
        role=role,  # type: ignore[arg-type]
        locations=tuple(locations),
        cameras=tuple(CameraRef(f"c-{i}", name) for i, name in enumerate(CAMERAS)),
        report_types=REPORT_TYPES,
        evidence_tags=TAGS,
    )


def place_names() -> list[str]:
    return [*BUILDINGS, *(a for _, _, a in AREAS)]
