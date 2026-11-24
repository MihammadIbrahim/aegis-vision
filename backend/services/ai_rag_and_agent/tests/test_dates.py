from datetime import UTC, date, datetime

from assistant_engine import dates

NOW = datetime(2026, 11, 19, 10, 30, tzinfo=UTC)
TZ = "Asia/Karachi"  # UTC+5


def one(text: str) -> dates.Period:
    found = dates.parse_periods(text, NOW, TZ)
    assert len(found) == 1, (text, found)
    return found[0]


def test_calendar_periods_start_at_local_midnight() -> None:
    october = one("incidents in October")
    assert october.since == datetime(2026, 9, 30, 19, 0, tzinfo=UTC)  # 1 Oct 00:00 in Karachi
    assert october.until == datetime(2026, 10, 31, 19, 0, tzinfo=UTC)
    assert october.label == "October 2026" and october.kind == "month"
    assert one("last year").label == "last year (2025)"
    assert one("in 2025").anchor == date(2025, 1, 1)
    assert one("Q3 2025").label == "Q3 2025"
    assert one("the third quarter").label == "Q3 2026"


def test_periods_that_include_today_end_now() -> None:
    this_month = one("this month")
    assert this_month.until <= NOW.replace(second=31) and this_month.since.day == 31  # 1 Nov local = 31 Oct UTC
    assert one("today").label == "today"
    last7 = one("last 7 days")
    assert (last7.until - last7.since).days == 7


def test_month_names_in_the_future_mean_last_year_and_may_needs_context() -> None:
    assert one("in December").label == "December 2025"
    assert dates.parse_periods("you may see", NOW, TZ) == []
    assert one("during May").label == "May 2026"


def test_explicit_ranges_and_multiple_periods_in_order() -> None:
    period = one("between 2026-10-01 and 2026-10-15")
    assert period.label == "2026-10-01 to 2026-10-15"
    both = dates.parse_periods("October vs September", NOW, TZ)
    assert [p.label for p in both] == ["October 2026", "September 2026"]
    assert one("since September").label == "since September 2026"


def test_previous_periods_for_comparisons() -> None:
    october = one("October")
    assert dates.previous_period(october, TZ, NOW).label == "September 2026"
    assert dates.previous_period(october, TZ, NOW, year_over_year=True).label == "October 2025"
    q1 = one("Q1 2026")
    assert dates.previous_period(q1, TZ, NOW).label == "Q4 2025"
    rolling = one("last 30 days")
    before = dates.previous_period(rolling, TZ, NOW)
    assert before.until == rolling.since


def test_round_trip_through_json() -> None:
    period = one("last month")
    assert dates.Period.from_dict(period.to_dict()) == period
