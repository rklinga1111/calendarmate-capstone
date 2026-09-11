"""Pure scheduling logic: availability checks, slot search, and booking.

Operates on the same `Event` fixture data as the Briefing Agent's
calendar tool, plus a `booked` list that `create_event` appends to --
this stands in for the real Google Calendar write until that's wired
up, so tests can assert on exactly what would have been booked.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta

from calendarmate.tools.calendar_tool import Event


def check_availability(
    events: list[Event], attendees: list[str], day: date, start: time, end: time
) -> dict:
    day_events = [e for e in events if e.date == day]
    busy = {attendee: False for attendee in attendees}
    conflicts: list[dict] = []

    for event in day_events:
        if not (event.start < end and start < event.end):
            continue
        for attendee in attendees:
            if attendee in event.attendees:
                busy[attendee] = True
                conflicts.append(
                    {
                        "attendee": attendee,
                        "title": event.title,
                        "start": event.start.isoformat(timespec="minutes"),
                        "end": event.end.isoformat(timespec="minutes"),
                    }
                )

    return {"available": not any(busy.values()), "busy": busy, "conflicts": conflicts}


def propose_alternative_slots(
    events: list[Event],
    attendees: list[str],
    start_date: date,
    duration_minutes: int,
    *,
    search_days: int = 5,
    business_start: time = time(9, 0),
    business_end: time = time(17, 0),
    slot_increment_minutes: int = 30,
    max_slots: int = 3,
    earliest_start: time | None = None,
) -> list[dict]:
    """Weekday, business-hours slots where every named attendee is free.

    `earliest_start`, when given, additionally floors the very first day
    searched (only that day -- later days still start at business_start)
    to whichever is later of business_start or earliest_start. This is
    how a same-day search avoids proposing a slot earlier than right now.
    """
    slots: list[dict] = []
    day = start_date
    days_checked = 0

    while len(slots) < max_slots and days_checked < search_days:
        if day.weekday() < 5:
            day_events = [e for e in events if e.date == day]
            day_start = business_start
            if day == start_date and earliest_start is not None and earliest_start > day_start:
                # Round up to the next slot boundary -- otherwise a
                # same-day search starting from e.g. 15:13 would suggest
                # an odd-looking "15:13-15:18" instead of a clean "15:30".
                minutes = earliest_start.hour * 60 + earliest_start.minute
                rounded = -(-minutes // slot_increment_minutes) * slot_increment_minutes
                day_start = time(min(rounded // 60, 23), rounded % 60)
            cursor = datetime.combine(day, day_start)
            business_end_dt = datetime.combine(day, business_end)

            while cursor + timedelta(minutes=duration_minutes) <= business_end_dt:
                slot_start = cursor.time()
                slot_end = (cursor + timedelta(minutes=duration_minutes)).time()
                conflict = any(
                    attendee in event.attendees and event.start < slot_end and slot_start < event.end
                    for event in day_events
                    for attendee in attendees
                )
                if not conflict:
                    slots.append(
                        {
                            "date": day.isoformat(),
                            "start": slot_start.isoformat(timespec="minutes"),
                            "end": slot_end.isoformat(timespec="minutes"),
                        }
                    )
                    if len(slots) >= max_slots:
                        break
                cursor += timedelta(minutes=slot_increment_minutes)

        day += timedelta(days=1)
        days_checked += 1

    return slots


def _generate_meet_link(title: str, day: date, start: time, attendees: list[str]) -> str:
    seed = f"{title}|{day.isoformat()}|{start.isoformat()}|{','.join(attendees)}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    return f"https://meet.google.com/{digest[:3]}-{digest[3:7]}-{digest[7:10]}"


def create_event(
    booked: list[dict],
    title: str,
    day: date,
    start: time,
    end: time,
    attendees: list[str],
    recurrence: list[str] | None = None,
) -> dict:
    record = {
        "title": title,
        "date": day.isoformat(),
        "start": start.isoformat(timespec="minutes"),
        "end": end.isoformat(timespec="minutes"),
        "attendees": list(attendees),
        "meet_link": _generate_meet_link(title, day, start, attendees),
    }
    if recurrence:
        # The mock doesn't expand a series into individual booked
        # occurrences the way a real Calendar write does -- it only
        # records the RRULE strings so tests can assert a recurring
        # request actually asked to create one, distinct from a one-off.
        record["recurrence"] = list(recurrence)
    booked.append(record)
    return record


def find_meetings_to_cancel(
    events: list[Event], day: date | None = None, title_hint: str | None = None
) -> list[dict]:
    """Meetings matching an optional day and/or title hint, for cancellation.

    Returns every match regardless of who owns it -- ownership is the
    caller's decision to check (and refuse on) once a single meeting has
    been identified, not something to silently filter out here. Silently
    filtering would turn "found it, but you don't own it" into a
    dishonest "no such meeting," which is a worse answer.
    """
    candidates = list(events)
    if day is not None:
        candidates = [e for e in candidates if e.date == day]
    if title_hint:
        needle = title_hint.lower()
        candidates = [e for e in candidates if needle in e.title.lower()]
    return [
        {
            "event_id": e.event_id,
            "title": e.title,
            "date": e.date.isoformat(),
            "start": e.start.isoformat(timespec="minutes"),
            "end": e.end.isoformat(timespec="minutes"),
            "attendees": list(e.attendees),
            "owner": e.owner,
            "series_id": e.series_id,
            "is_recurring": e.series_id is not None,
        }
        for e in candidates
    ]


def cancel_event(
    events: list[Event], cancelled: list[dict], event_id: str, scope: str
) -> dict:
    """Mocked cancellation: records what would have been cancelled.

    `scope="series"` cancels every event sharing the target's series_id
    (an error if the target isn't part of any series); `scope="instance"`
    cancels only the one matching event_id.
    """
    target = next((e for e in events if e.event_id == event_id), None)
    if target is None:
        return {"error": f"No meeting found with event_id {event_id!r}."}

    if scope == "series":
        if target.series_id is None:
            return {"error": f"'{target.title}' is not part of a recurring series."}
        affected = [e for e in events if e.series_id == target.series_id]
    else:
        affected = [target]

    for e in affected:
        cancelled.append(
            {
                "event_id": e.event_id,
                "title": e.title,
                "date": e.date.isoformat(),
                "start": e.start.isoformat(timespec="minutes"),
                "end": e.end.isoformat(timespec="minutes"),
                "scope": scope,
            }
        )

    return {
        "cancelled_count": len(affected),
        "scope": scope,
        "event_ids": [e.event_id for e in affected],
    }
