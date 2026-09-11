"""A read-only calendar tool backed by a mocked JSON fixture.

Swapping this for the real Google Calendar API later is a plumbing
change: `load_events` is the only function that needs to start making a
network call instead of reading a file -- `events_in_range` and
`find_conflicts` operate on the same `Event` shape either way.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "calendar.json"


@dataclass(frozen=True)
class Event:
    title: str
    date: date
    start: time
    end: time
    attendees: tuple[str, ...]
    # Added for meeting cancellation: `event_id` identifies one specific
    # occurrence, `owner` is who can actually cancel it (only the owner
    # can -- an attendee can't cancel someone else's meeting), and
    # `series_id` groups every occurrence of the same recurring meeting
    # (None for a one-off). All three default so every pre-existing
    # fixture entry and every existing test's `Event(...)` construction
    # is unaffected.
    event_id: str = ""
    owner: str = ""
    series_id: str | None = None

    def overlaps(self, other: "Event") -> bool:
        return self.start < other.end and other.start < self.end


def _synthetic_event_id(title: str, day: date, start: time) -> str:
    # The mocked fixture has no real backing store to issue IDs from, so
    # this derives a stable one from the event's own identifying fields --
    # good enough to look a specific mocked event back up by, the same
    # way a real Calendar event ID would be used.
    seed = f"{title}|{day.isoformat()}|{start.isoformat()}"
    return hashlib.sha256(seed.encode()).hexdigest()[:12]


def load_events(fixture_path: Path = _FIXTURE_PATH) -> list[Event]:
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    return [
        Event(
            title=item["title"],
            date=date.fromisoformat(item["date"]),
            start=time.fromisoformat(item["start"]),
            end=time.fromisoformat(item["end"]),
            attendees=tuple(item["attendees"]),
            event_id=item.get("event_id") or _synthetic_event_id(
                item["title"], date.fromisoformat(item["date"]), time.fromisoformat(item["start"])
            ),
            # The mocked fixture is Alice's own calendar -- every event in
            # it defaults to being hers unless the fixture says otherwise
            # (e.g. someone else's recurring meeting she's just invited to).
            owner=item.get("owner", "Alice"),
            series_id=item.get("series_id"),
        )
        for item in raw
    ]


def events_in_range(events: list[Event], start: date, end: date) -> list[Event]:
    return sorted(
        (e for e in events if start <= e.date <= end),
        key=lambda e: (e.date, e.start),
    )


def find_conflicts(events: list[Event]) -> list[tuple[Event, Event]]:
    """Pairs of events on the same day whose times overlap."""
    by_day: dict[date, list[Event]] = {}
    for event in events:
        by_day.setdefault(event.date, []).append(event)

    conflicts: list[tuple[Event, Event]] = []
    for day_events in by_day.values():
        day_events.sort(key=lambda e: e.start)
        for i, first in enumerate(day_events):
            for second in day_events[i + 1 :]:
                if first.overlaps(second):
                    conflicts.append((first, second))
    return conflicts
