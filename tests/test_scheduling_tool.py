from datetime import date, time

from calendarmate.tools.calendar_tool import Event
from calendarmate.tools.scheduling_tool import check_availability, create_event, propose_alternative_slots


def _event(title: str, day: str, start: str, end: str, attendees: tuple[str, ...]) -> Event:
    return Event(
        title=title,
        date=date.fromisoformat(day),
        start=time.fromisoformat(start),
        end=time.fromisoformat(end),
        attendees=attendees,
    )


THURSDAY = date(2025, 6, 19)

EVENTS = [
    _event("1:1 with Bob", "2025-06-19", "09:00", "09:30", ("Alice", "Bob")),
    _event("Design Review", "2025-06-17", "11:00", "12:00", ("Alice", "Eve")),
    _event("Client Sync", "2025-06-17", "11:30", "12:00", ("Alice", "Grace")),
]


def test_check_availability_reports_free_when_no_overlap() -> None:
    result = check_availability(EVENTS, ["Bob"], THURSDAY, time(10, 0), time(10, 30))
    assert result["available"] is True
    assert result["busy"] == {"Bob": False}
    assert result["conflicts"] == []


def test_check_availability_reports_conflict() -> None:
    result = check_availability(EVENTS, ["Bob"], THURSDAY, time(9, 0), time(9, 30))
    assert result["available"] is False
    assert result["busy"] == {"Bob": True}
    assert result["conflicts"][0]["title"] == "1:1 with Bob"


def test_check_availability_detects_a_full_overlap() -> None:
    # requested slot is exactly the existing meeting's slot
    result = check_availability(EVENTS, ["Eve"], date(2025, 6, 17), time(11, 0), time(12, 0))
    assert result["available"] is False
    assert result["conflicts"][0]["title"] == "Design Review"


def test_check_availability_detects_a_partial_overlap_at_the_start() -> None:
    # requested slot starts before the existing meeting and ends inside it
    result = check_availability(EVENTS, ["Eve"], date(2025, 6, 17), time(10, 30), time(11, 15))
    assert result["available"] is False
    assert result["conflicts"][0]["title"] == "Design Review"


def test_check_availability_detects_a_partial_overlap_at_the_end() -> None:
    # requested slot starts inside the existing meeting and ends after it
    result = check_availability(EVENTS, ["Eve"], date(2025, 6, 17), time(11, 45), time(12, 30))
    assert result["available"] is False
    assert result["conflicts"][0]["title"] == "Design Review"


def test_check_availability_does_not_flag_back_to_back_as_a_conflict() -> None:
    # ends exactly when the existing meeting starts -- not an overlap
    result = check_availability(EVENTS, ["Eve"], date(2025, 6, 17), time(10, 0), time(11, 0))
    assert result["available"] is True


def test_check_availability_checks_every_named_attendee() -> None:
    result = check_availability(
        EVENTS, ["Alice", "Grace"], date(2025, 6, 17), time(11, 30), time(12, 0)
    )
    assert result["available"] is False
    assert result["busy"] == {"Alice": True, "Grace": True}
    conflict_titles = {c["title"] for c in result["conflicts"]}
    assert "Client Sync" in conflict_titles


def test_propose_alternative_slots_skips_the_busy_slot() -> None:
    slots = propose_alternative_slots(EVENTS, ["Bob"], THURSDAY, duration_minutes=30, max_slots=1)
    assert len(slots) == 1
    assert slots[0]["date"] == "2025-06-19"
    assert slots[0]["start"] != "09:00"  # that slot is taken


def test_propose_alternative_slots_returns_multiple_candidates() -> None:
    slots = propose_alternative_slots(EVENTS, ["Bob"], THURSDAY, duration_minutes=30, max_slots=3)
    assert len(slots) == 3
    starts = {s["start"] for s in slots}
    assert len(starts) == 3  # all distinct


def test_propose_alternative_slots_skips_weekends() -> None:
    friday = date(2025, 6, 20)
    slots = propose_alternative_slots(EVENTS, ["Bob"], friday, duration_minutes=30, search_days=3, max_slots=50)
    assert all(date.fromisoformat(s["date"]).weekday() < 5 for s in slots)


def test_create_event_books_and_returns_a_meet_link() -> None:
    booked: list[dict] = []
    record = create_event(
        booked, "Sync with Bob", THURSDAY, time(10, 0), time(10, 30), ["Alice", "Bob"]
    )
    assert booked == [record]
    assert record["title"] == "Sync with Bob"
    assert record["attendees"] == ["Alice", "Bob"]
    assert record["meet_link"].startswith("https://meet.google.com/")
