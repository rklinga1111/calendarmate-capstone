"""Read-only meeting-record lookup, plus a mocked follow-up send tool.

Swapping the mocked lookup for the real Google Calendar (attendees) +
notes-doc/transcript service later is a plumbing change: `load_meetings`
is the only function that needs to start pulling from those APIs instead
of a file -- `find_meeting` operates on the same `Meeting` shape either
way. `send_followup_email` stands in for the real Gmail
`users.messages.send` call the same way `create_event` stands in for a
real Calendar write in the Scheduler Agent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "meetings.json"


@dataclass(frozen=True)
class Meeting:
    meeting_id: str
    title: str
    attendees: tuple[str, ...]
    notes: str


def load_meetings(fixture_path: Path = _FIXTURE_PATH) -> list[Meeting]:
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    return [
        Meeting(
            meeting_id=item["meeting_id"],
            title=item["title"],
            attendees=tuple(item["attendees"]),
            notes=item["notes"],
        )
        for item in raw
    ]


def find_meeting(
    meetings: list[Meeting],
    *,
    meeting_id: str | None = None,
    title_hint: str | None = None,
) -> Meeting | None:
    if meeting_id:
        for meeting in meetings:
            if meeting.meeting_id == meeting_id:
                return meeting
    if title_hint:
        hint = title_hint.strip().lower()
        for meeting in meetings:
            if hint in meeting.title.lower():
                return meeting
    return None


def _generate_message_id(subject: str, recipients: list[str]) -> str:
    seed = f"{subject}|{','.join(recipients)}"
    return f"mock-{hashlib.sha256(seed.encode()).hexdigest()[:12]}"


def send_followup_email(
    sent: list[dict], subject: str, body: str, recipients: list[str]
) -> dict:
    record = {
        "subject": subject,
        "body": body,
        "recipients": list(recipients),
        "message_id": _generate_message_id(subject, recipients),
        "status": "sent",
    }
    sent.append(record)
    return record
