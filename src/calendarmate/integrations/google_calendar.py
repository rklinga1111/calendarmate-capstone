"""Real Google Calendar-backed versions of the calendar tools.

Same shapes as `calendarmate.tools.calendar_tool` and
`calendarmate.tools.scheduling_tool` (the `Event` dataclass, the
`create_event` result dict) so they're drop-in replacements at the call
site -- the agents' logic, prompts, and tool schemas don't change at
all; only where the data comes from does.

Not used by the pytest suite or `harness.py` -- those keep running
against the mocked fixtures via `calendarmate.tools.calendar_tool`
unchanged. This module is only ever wired in explicitly, by
`live_assistant.py`.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from calendarmate.integrations.google_auth import calendar_service, docs_service
from calendarmate.tools.calendar_tool import Event
from calendarmate.tools.followup_tool import Meeting

_CALENDAR_ID = "primary"

# Matches the document id out of a Google Docs URL, e.g.
# https://docs.google.com/document/d/<id>/edit?usp=... -- this is how a
# Google Meet call's "Notes by Gemini" doc shows up on the Calendar
# event, as a regular attachment rather than anything calendar-specific.
_DOC_URL_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")


def get_authenticated_user_email() -> str:
    """The real, authenticated account's own address -- used as the
    Scheduler Agent's `user_name` instead of the mocked fixture's
    hardcoded "Alice", which would be nonsensical against a real
    calendar."""
    service = calendar_service()
    return service.calendarList().get(calendarId=_CALENDAR_ID).execute()["id"]


def _parse_event(raw: dict) -> Event | None:
    start = raw.get("start", {})
    end = raw.get("end", {})
    start_dt_str = start.get("dateTime")
    end_dt_str = end.get("dateTime")
    if not start_dt_str or not end_dt_str:
        return None  # all-day event -- no start/end time to compare against

    start_dt = datetime.fromisoformat(start_dt_str)
    end_dt = datetime.fromisoformat(end_dt_str)
    attendees = tuple(
        a.get("displayName") or a.get("email", "")
        for a in raw.get("attendees", [])
        if a.get("email") or a.get("displayName")
    )
    return Event(
        title=raw.get("summary", "(no title)"),
        date=start_dt.date(),
        start=start_dt.time(),
        end=end_dt.time(),
        attendees=attendees,
        event_id=raw.get("id", ""),
        owner=raw.get("organizer", {}).get("email", ""),
        # When the API expands a recurring meeting into instances (which
        # `singleEvents=True` below always does), each instance carries
        # `recurringEventId` pointing at the series' master event -- that
        # master's own id is exactly what cancelling the whole series
        # needs to delete, so no extra lookup is required at cancel time.
        series_id=raw.get("recurringEventId"),
    )


def load_events_from_google_calendar(start_date: date, end_date: date) -> list[Event]:
    """Events on the real calendar within [start_date, end_date], inclusive."""
    service = calendar_service()
    time_min = datetime.combine(start_date, time.min).isoformat() + "Z"
    time_max = datetime.combine(end_date + timedelta(days=1), time.min).isoformat() + "Z"

    response = (
        service.events()
        .list(
            calendarId=_CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = [_parse_event(item) for item in response.get("items", [])]
    return [e for e in events if e is not None]


def create_event_via_google_calendar(
    title: str,
    day: date,
    start: time,
    end: time,
    attendees: list[str],
    recurrence: list[str] | None = None,
) -> dict:
    """Creates a real event with a real Google Meet link. Returns the
    same shape as the mocked `scheduling_tool.create_event`, or an
    `error` dict if an attendee isn't a real email address.

    A real Calendar invite needs actual email addresses -- silently
    dropping an unresolvable name (e.g. "mytcl" instead of
    "mytcl@example.com") from the invite while still reporting it back
    as an attendee would mean the tool's result claims someone was
    invited who never actually was. Refusing and asking for the real
    address instead is the same "ask, don't guess" discipline
    `scheduler.md` already applies to missing day/time/duration.
    """
    unresolved = [a for a in attendees if "@" not in a]
    if unresolved:
        return {
            "error": (
                f"Could not resolve {', '.join(unresolved)} to a real email "
                "address. Ask the user for the actual email address before "
                "booking -- do not create the event without it."
            )
        }

    service = calendar_service()
    # Google Calendar rejects a dateTime with no timezone information
    # ("Missing time zone definition"). Use the calendar's own configured
    # timezone rather than assuming or hardcoding one, so the event lands
    # at the wall-clock time the user actually asked for.
    calendar_timezone = service.calendars().get(calendarId=_CALENDAR_ID).execute()["timeZone"]
    body = {
        "summary": title,
        "start": {"dateTime": datetime.combine(day, start).isoformat(), "timeZone": calendar_timezone},
        "end": {"dateTime": datetime.combine(day, end).isoformat(), "timeZone": calendar_timezone},
        "attendees": [{"email": a} for a in attendees],
        "conferenceData": {
            "createRequest": {
                "requestId": f"{title}-{day.isoformat()}-{start.isoformat()}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    if recurrence:
        body["recurrence"] = list(recurrence)
    created = (
        service.events()
        .insert(calendarId=_CALENDAR_ID, body=body, conferenceDataVersion=1)
        .execute()
    )
    meet_link = created.get("hangoutLink", "")
    return {
        "title": title,
        "date": day.isoformat(),
        "start": start.isoformat(timespec="minutes"),
        "end": end.isoformat(timespec="minutes"),
        "attendees": list(attendees),
        "meet_link": meet_link,
        "event_id": created.get("id"),
    }


def _find_attached_doc_id(raw_event: dict) -> str | None:
    for attachment in raw_event.get("attachments", []):
        match = _DOC_URL_RE.search(attachment.get("fileUrl", ""))
        if match:
            return match.group(1)
    return None


def _extract_doc_text(doc: dict) -> str:
    # A Google Doc's content is a nested structure of structural elements
    # -- only `paragraph` elements carry visible text, as a list of runs.
    # This flattens it to plain text, which is all the Follow-Up Agent's
    # prompt needs (it already just reads `notes` as a block of text, the
    # same way it reads a plain `description` string).
    parts = []
    for element in doc.get("body", {}).get("content", []):
        for run in element.get("paragraph", {}).get("elements", []):
            text_run = run.get("textRun")
            if text_run:
                parts.append(text_run.get("content", ""))
    return "".join(parts)


def _fetch_attached_doc_notes(raw_event: dict) -> str:
    """The real notes source for a Google Meet call with Gemini
    note-taking enabled: Calendar has no native meeting-notes field, but
    that feature attaches a real Google Doc to the event instead. A real
    follow-up request once got "no notes recorded" for a meeting that
    genuinely had notes, just not in `description` -- this is what was
    missing. Returns "" (never raises) if there's no attached doc, or if
    fetching it fails for any reason (missing scope, deleted doc,
    permissions) -- the caller falls back to `description` either way,
    the same honest "say there's nothing" behavior as before, just with
    one more real place to actually find something.
    """
    doc_id = _find_attached_doc_id(raw_event)
    if not doc_id:
        return ""
    try:
        doc = docs_service().documents().get(documentId=doc_id).execute()
    except Exception:
        return ""
    return _extract_doc_text(doc).strip()


def get_meeting_record_from_google_calendar(
    meeting_id: str | None = None, title_hint: str | None = None
) -> Meeting | None:
    """Real equivalent of `followup_tool.find_meeting`.

    Takes `(meeting_id, title_hint)` positionally, not keyword-only --
    that matches `followup.GetMeetingFn`'s contract (established by the
    module's own default wrapper around `find_meeting`), which
    `followup._dispatch_tool` calls positionally. A keyword-only version
    of this function raised a TypeError the first time it was actually
    exercised for real; the offline tests never caught it because they
    used a fake with a compatible signature instead of this function.

    Google Calendar's own event fields have no native "meeting notes/
    transcript" field, so this first tries a real Google Meet artifact
    that comes closest -- an attached "Notes by Gemini" doc, if the
    meeting had one -- and falls back to the event's `description`
    otherwise (the original, simpler assumption this project started
    with, and still the only source for a meeting that has no such doc).
    In practice `notes` comes back empty when neither exists, which the
    agent already handles correctly (Story 6.3 -- say so rather than
    fabricating).
    """
    service = calendar_service()

    if meeting_id:
        try:
            raw = service.events().get(calendarId=_CALENDAR_ID, eventId=meeting_id).execute()
        except Exception:
            raw = None
    elif title_hint:
        response = (
            service.events()
            .list(calendarId=_CALENDAR_ID, q=title_hint, singleEvents=True, maxResults=1)
            .execute()
        )
        items = response.get("items", [])
        raw = items[0] if items else None
    else:
        raw = None

    if raw is None:
        return None

    # Always the real email address here, never displayName -- this
    # feeds send_followup_email's recipients directly. A real send once
    # failed with Gmail's "Invalid To header" because displayName (a
    # bare human name, not an address) was preferred when both were
    # present, the reverse of what `_parse_event`'s Briefing-facing
    # version does deliberately for *readability* in a read-only display
    # that never needs to actually address an email.
    attendees = tuple(a["email"] for a in raw.get("attendees", []) if a.get("email"))
    notes = _fetch_attached_doc_notes(raw) or raw.get("description", "") or ""
    return Meeting(
        meeting_id=raw.get("id", ""),
        title=raw.get("summary", "(no title)"),
        attendees=attendees,
        notes=notes,
    )


def cancel_event_via_google_calendar(event_id: str, scope: str, series_id: str | None = None) -> dict:
    """Cancels a real event. `scope="series"` deletes the recurring
    series' master event (its id is `series_id`, already read off the
    matched instance by `_parse_event` above -- no extra API call needed
    to find it); `scope="instance"` deletes just the one event_id given.

    Ownership isn't re-checked here -- `scheduler.py`'s `_dispatch_tool`
    already refuses to call this at all unless the event's `owner`
    matched the authenticated user, the same defense-in-depth pattern
    used for the already-passed-time booking check.
    """
    if scope == "series" and not series_id:
        return {"error": "This meeting is not part of a recurring series -- nothing to cancel as a series."}
    target_id = series_id if scope == "series" else event_id

    service = calendar_service()
    try:
        service.events().delete(calendarId=_CALENDAR_ID, eventId=target_id).execute()
    except Exception as exc:
        return {"error": f"Could not cancel the meeting: {exc}"}
    return {"cancelled": True, "event_id": target_id, "scope": scope}
