from datetime import date, time

from calendarmate.tools.calendar_tool import Event, events_in_range, find_conflicts, load_events


def _event(title: str, day: str, start: str, end: str, attendees: tuple[str, ...] = ("Alice",)) -> Event:
    return Event(
        title=title,
        date=date.fromisoformat(day),
        start=time.fromisoformat(start),
        end=time.fromisoformat(end),
        attendees=attendees,
    )


def test_load_events_reads_the_fixture() -> None:
    events = load_events()
    # 11 original one-off events + 1 extra "Team Standup" occurrence
    # (2025-06-23, for recurring-series cancellation tests) + 2 "Bob's
    # Weekly Sync" occurrences (owned by Bob, for ownership-rejection tests).
    assert len(events) == 14
    assert any(e.title == "Design Review" for e in events)


def test_events_in_range_is_inclusive_and_sorted() -> None:
    events = [
        _event("B", "2025-06-17", "11:00", "12:00"),
        _event("A", "2025-06-17", "09:00", "09:30"),
        _event("Out of range", "2025-06-19", "09:00", "09:30"),
    ]
    result = events_in_range(events, date(2025, 6, 17), date(2025, 6, 17))
    assert [e.title for e in result] == ["A", "B"]


def test_events_in_range_spans_a_week() -> None:
    events = load_events()
    result = events_in_range(events, date(2025, 6, 16), date(2025, 6, 22))
    # The 11 original events, plus "Bob's Weekly Sync" on 2025-06-17 --
    # its other occurrence (06-24) and the extra "Team Standup" occurrence
    # (06-23) fall outside this week.
    assert len(result) == 12


def test_find_conflicts_detects_partial_overlap() -> None:
    events = [
        _event("Design Review", "2025-06-17", "11:00", "12:00"),
        _event("Client Sync", "2025-06-17", "11:30", "12:00"),
    ]
    conflicts = find_conflicts(events)
    assert len(conflicts) == 1
    titles = {conflicts[0][0].title, conflicts[0][1].title}
    assert titles == {"Design Review", "Client Sync"}


def test_find_conflicts_ignores_back_to_back_meetings() -> None:
    events = [
        _event("Standup", "2025-06-17", "09:00", "09:30"),
        _event("Design Review", "2025-06-17", "09:30", "10:00"),
    ]
    assert find_conflicts(events) == []


def test_find_conflicts_only_compares_same_day() -> None:
    events = [
        _event("Standup", "2025-06-17", "09:00", "10:00"),
        _event("Standup", "2025-06-18", "09:00", "10:00"),
    ]
    assert find_conflicts(events) == []


def test_fixture_has_a_conflict_today_and_a_conflict_later_this_week() -> None:
    events = load_events()
    conflicts = find_conflicts(events)
    conflict_dates = {a.date for a, b in conflicts}
    assert date(2025, 6, 17) in conflict_dates  # Design Review vs Client Sync
    assert date(2025, 6, 20) in conflict_dates  # Sprint Retro vs Happy Hour Planning
