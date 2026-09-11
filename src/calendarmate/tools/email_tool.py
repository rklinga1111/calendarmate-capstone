"""A read-only inbox tool backed by a mocked JSON fixture.

Swapping this for the real Gmail API later is a plumbing change:
`load_emails` is the only function that needs to start making a network
call instead of reading a file -- `unread_emails` and `emails_in_range`
operate on the same `Email` shape either way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "inbox.json"


@dataclass(frozen=True)
class Email:
    sender: str
    subject: str
    body: str
    read: bool
    received_at: date
    # Whether the user has since sent a reply in this email's thread --
    # distinct from `read`, since opening an email doesn't mean it got a
    # reply. Defaults to False so every existing fixture entry and
    # `Email(...)` construction is unaffected; the mocked fixture has no
    # way to represent a real reply having been sent, so it's always
    # False there. Only the real Gmail integration ever sets this True.
    replied: bool = False


def load_emails(fixture_path: Path = _FIXTURE_PATH) -> list[Email]:
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    return [
        Email(
            sender=item["sender"],
            subject=item["subject"],
            body=item["body"],
            read=item["read"],
            received_at=date.fromisoformat(item["received_at"]),
        )
        for item in raw
    ]


def unread_emails(emails: list[Email]) -> list[Email]:
    return [e for e in emails if not e.read]


def needs_attention_pool(emails: list[Email], today: date, recent_days: int = 5) -> list[Email]:
    """Unread mail, plus mail read within the last `recent_days` days.

    "Needs attention" and "unread" aren't quite the same thing -- a real
    recruiter email asking specific questions doesn't stop needing a
    reply just because it was opened. Reading it doesn't resolve
    whether a specific person is still waiting on the user, so this
    only widens the POOL of candidates the existing action-required
    judgment considers; it still has to actually look like something a
    named person is waiting on, the same as it always did for unread
    mail. A read email from a week ago is left out on purpose -- if it
    still genuinely needed a reply, it would already have surfaced in
    an earlier "needs attention" check within these last few days.
    An email the user has already replied to (`replied`) is excluded
    outright, read or not -- a real reply already sent is the clearest
    possible sign nothing further is pending.
    """
    cutoff = today - timedelta(days=recent_days)
    return [e for e in emails if not e.replied and (not e.read or cutoff <= e.received_at <= today)]


def emails_in_range(emails: list[Email], start: date, end: date) -> list[Email]:
    """Every email received in [start, end], inclusive -- read or unread.

    A time-scoped question ("what came in last week") is about what
    actually happened in that period, not just what's still unread, so
    this deliberately doesn't filter by read status the way
    `unread_emails` does.
    """
    return sorted((e for e in emails if start <= e.received_at <= end), key=lambda e: e.received_at)
