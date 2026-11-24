"""Restricted-area access decisions (decision D-016). An unknown identity is never reported as a
named person entering without permission."""

from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import pytest

from src.domain.entities.area_access import AccessGrant, AccessSchedule, GrantStatus
from src.domain.exceptions.domain_errors import ValidationFailed
from src.domain.services.access_policy import (
    AccessDecision,
    AccessResult,
    evaluate_access,
    may_report_unauthorized_entry,
)

# Monday 2026-11-02, 10:00 in Karachi (UTC+5) = 05:00 UTC.
MONDAY_10_KARACHI = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)
OFFICE_HOURS = AccessSchedule((1, 2, 3, 4, 5), time(8, 0), time(18, 0), "Asia/Karachi")


def grant(**changes) -> AccessGrant:  # type: ignore[no-untyped-def]
    base = {
        "id": uuid4(),
        "personnel_id": uuid4(),
        "personnel_code": "PSN-AAAAAAAAAA",
        "personnel_name": "A",
        "personnel_status": "ACTIVE",
        "category_label": "Employee",
        "department": None,
        "area_id": uuid4(),
        "area_name": "Server Room",
        "floor_name": None,
        "building_name": "IT Block",
        "status": GrantStatus.ACTIVE,
        "valid_from": MONDAY_10_KARACHI - timedelta(days=30),
        "valid_until": None,
        "schedule": None,
        "note": None,
        "granted_at": MONDAY_10_KARACHI,
        "granted_by_name": "Admin",
        "updated_at": None,
    }
    return AccessGrant(**{**base, **changes})


def decide(grants, *, status="ACTIVE", restricted=True, at=MONDAY_10_KARACHI) -> AccessDecision:  # type: ignore[no-untyped-def]
    return evaluate_access(personnel_status=status, area_restricted=restricted, grants=grants, at=at).decision


def test_unknown_identity_is_never_attributed() -> None:
    result = evaluate_access(personnel_status=None, area_restricted=True, grants=[], at=MONDAY_10_KARACHI)
    assert result.decision is AccessDecision.UNKNOWN_IDENTITY
    assert not may_report_unauthorized_entry(result, identity_verified=False)
    assert not may_report_unauthorized_entry(result, identity_verified=True)
    # Even a clear "no permission" needs a verified identity before it can name anyone.
    denied = AccessResult(AccessDecision.NO_PERMISSION)
    assert not may_report_unauthorized_entry(denied, identity_verified=False)
    assert may_report_unauthorized_entry(denied, identity_verified=True)
    assert not may_report_unauthorized_entry(AccessResult(AccessDecision.AUTHORIZED), identity_verified=True)


def test_basic_decisions() -> None:
    assert decide([grant()]) is AccessDecision.AUTHORIZED
    assert decide([]) is AccessDecision.NO_PERMISSION
    assert decide([grant(status=GrantStatus.REVOKED)]) is AccessDecision.NO_PERMISSION
    assert decide([grant()], status="INACTIVE") is AccessDecision.PERSONNEL_INACTIVE
    assert decide([grant()], restricted=False) is AccessDecision.AREA_NOT_RESTRICTED


def test_validity_period() -> None:
    assert decide([grant(valid_from=MONDAY_10_KARACHI + timedelta(minutes=1))]) is AccessDecision.NOT_YET_VALID
    assert decide([grant(valid_until=MONDAY_10_KARACHI)]) is AccessDecision.EXPIRED  # the end is exclusive
    assert decide([grant(valid_until=MONDAY_10_KARACHI + timedelta(seconds=1))]) is AccessDecision.AUTHORIZED


def test_weekly_schedule_in_the_grants_time_zone() -> None:
    assert decide([grant(schedule=OFFICE_HOURS)]) is AccessDecision.AUTHORIZED
    evening = datetime(2026, 11, 2, 13, 30, tzinfo=UTC)  # 18:30 in Karachi
    assert decide([grant(schedule=OFFICE_HOURS)], at=evening) is AccessDecision.OUTSIDE_SCHEDULE
    sunday = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
    assert decide([grant(schedule=OFFICE_HOURS)], at=sunday) is AccessDecision.OUTSIDE_SCHEDULE
    # 02:00 UTC on Monday is 07:00 in Karachi: before office hours there.
    early = datetime(2026, 11, 2, 2, 0, tzinfo=UTC)
    assert decide([grant(schedule=OFFICE_HOURS)], at=early) is AccessDecision.OUTSIDE_SCHEDULE


def test_the_most_permissive_active_grant_wins() -> None:
    assert decide([grant(valid_until=MONDAY_10_KARACHI), grant(schedule=OFFICE_HOURS)]) is AccessDecision.AUTHORIZED
    assert (
        decide(
            [grant(valid_until=MONDAY_10_KARACHI), grant(schedule=OFFICE_HOURS)],
            at=datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
        )
        is AccessDecision.OUTSIDE_SCHEDULE
    )


@pytest.mark.parametrize(
    ("days", "start", "end", "zone"),
    [
        ((), time(8), time(9), "UTC"),
        ((1, 1), time(8), time(9), "UTC"),
        ((8,), time(8), time(9), "UTC"),
        ((1,), time(9), time(8), "UTC"),
        ((1,), time(8), time(9), "Mars/Olympus"),
    ],
)
def test_invalid_schedules_are_refused(days, start, end, zone) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationFailed):
        AccessSchedule(days, start, end, zone)


def test_naive_times_are_refused() -> None:
    with pytest.raises(ValueError):
        decide([grant()], at=datetime(2026, 11, 2, 10, 0))
