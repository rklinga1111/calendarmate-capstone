"""Tests for the real Google Calendar integration's pure validation
logic -- not its actual API calls, which need real credentials and are
exercised manually via live_assistant.py instead (see CLAUDE.md).
"""

from datetime import date, time
from unittest.mock import MagicMock, patch

from calendarmate.integrations.google_calendar import (
    create_event_via_google_calendar,
    get_meeting_record_from_google_calendar,
)


def test_create_event_rejects_an_unresolved_attendee_without_calling_the_api() -> None:
    # "mytcl" isn't a real email address -- Google Calendar has nothing
    # to invite. This must fail fast with an error, not silently drop
    # the attendee and still report a successful booking. If this
    # reached the real API call (it shouldn't, given no credentials are
    # configured in the test environment), it would raise instead of
    # returning cleanly, which would also fail this test -- so a clean
    # error-dict return here is direct proof validation short-circuited
    # before any network call.
    result = create_event_via_google_calendar(
        "Sync", date(2025, 6, 19), time(10, 0), time(10, 30), ["mytcl", "real@example.com"]
    )
    assert "error" in result
    assert "mytcl" in result["error"]
    assert "real@example.com" not in result["error"]


def test_create_event_rejects_when_every_attendee_is_unresolved() -> None:
    result = create_event_via_google_calendar(
        "Sync", date(2025, 6, 19), time(10, 0), time(10, 30), ["mytcl", "bob"]
    )
    assert "error" in result
    assert "mytcl" in result["error"]
    assert "bob" in result["error"]


def test_get_meeting_record_accepts_positional_args_like_dispatch_calls_it() -> None:
    # followup._dispatch_tool calls get_meeting_fn(meeting_id, title_hint)
    # positionally. A keyword-only version of this function raised a
    # TypeError the first time it was exercised for real against a
    # genuine request -- the offline tests never caught it because they
    # exercised the DI seam with a hand-written fake whose signature
    # happened to be compatible, not this function itself. The service is
    # mocked so this checks the call signature, not real credentials.
    mock_service = MagicMock()
    mock_service.events.return_value.get.return_value.execute.return_value = {
        "id": "evt1",
        "summary": "Interview",
        "attendees": [],
        "description": "",
    }
    with patch(
        "calendarmate.integrations.google_calendar.calendar_service", return_value=mock_service
    ):
        result = get_meeting_record_from_google_calendar("evt1", None)  # positional, not keyword
    assert result is not None
    assert result.meeting_id == "evt1"


def test_get_meeting_record_attendees_are_real_emails_not_display_names() -> None:
    # A real send once failed with Gmail's "Invalid To header" because
    # attendees came back as bare display names ("Jordan Lee") instead
    # of real addresses -- these feed send_followup_email's recipients
    # directly, so a display name there isn't just a cosmetic difference,
    # it's an address Gmail can't actually send to.
    mock_service = MagicMock()
    mock_service.events.return_value.get.return_value.execute.return_value = {
        "id": "evt1",
        "summary": "Interview",
        "attendees": [
            {"email": "jordan@example.com", "displayName": "Jordan Lee"},
            {"email": "neil@example.com"},  # no displayName at all
        ],
        "description": "",
    }
    with patch(
        "calendarmate.integrations.google_calendar.calendar_service", return_value=mock_service
    ):
        result = get_meeting_record_from_google_calendar("evt1", None)
    assert result is not None
    assert result.attendees == ("jordan@example.com", "neil@example.com")
    assert "Jordan Lee" not in result.attendees


def _google_doc_body(paragraphs: list[str]) -> dict:
    # Mirrors the real Docs API response shape closely enough to exercise
    # _extract_doc_text: each paragraph is a structural element holding a
    # list of text runs.
    return {
        "body": {
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": text}}]}} for text in paragraphs
            ]
        }
    }


def test_get_meeting_record_uses_the_attached_gemini_notes_doc() -> None:
    # A real follow-up request once got "no notes recorded" for a meeting
    # that genuinely had notes -- just in an attached "Notes by Gemini"
    # Google Doc, not the event's `description` field, which was empty.
    mock_calendar = MagicMock()
    mock_calendar.events.return_value.get.return_value.execute.return_value = {
        "id": "evt1",
        "summary": "Design Review",
        "attendees": [],
        "description": "",
        "attachments": [
            {
                "title": "Notes by Gemini",
                "fileUrl": "https://docs.google.com/document/d/abc123XYZ/edit?usp=meet_tnfm_calendar",
            }
        ],
    }
    mock_docs = MagicMock()
    mock_docs.documents.return_value.get.return_value.execute.return_value = _google_doc_body(
        ["Summary\n", "Discussed the new IA. ", "Action: Bob to send mocks by Friday.\n"]
    )
    with (
        patch("calendarmate.integrations.google_calendar.calendar_service", return_value=mock_calendar),
        patch("calendarmate.integrations.google_calendar.docs_service", return_value=mock_docs),
    ):
        result = get_meeting_record_from_google_calendar("evt1", None)

    assert result is not None
    assert "Bob to send mocks by Friday" in result.notes
    mock_docs.documents.return_value.get.assert_called_once_with(documentId="abc123XYZ")


def test_get_meeting_record_falls_back_to_description_with_no_attachment() -> None:
    mock_calendar = MagicMock()
    mock_calendar.events.return_value.get.return_value.execute.return_value = {
        "id": "evt1",
        "summary": "Design Review",
        "attendees": [],
        "description": "Manually typed notes here.",
    }
    mock_docs = MagicMock()
    with (
        patch("calendarmate.integrations.google_calendar.calendar_service", return_value=mock_calendar),
        patch("calendarmate.integrations.google_calendar.docs_service", return_value=mock_docs),
    ):
        result = get_meeting_record_from_google_calendar("evt1", None)

    assert result is not None
    assert result.notes == "Manually typed notes here."
    mock_docs.documents.assert_not_called()  # no attachment -- never even tries the Docs API


def test_get_meeting_record_falls_back_gracefully_when_the_doc_fetch_fails() -> None:
    # Missing scope, deleted doc, no permission -- whatever the reason,
    # this must fall back to `description` rather than raising and
    # breaking the whole follow-up request.
    mock_calendar = MagicMock()
    mock_calendar.events.return_value.get.return_value.execute.return_value = {
        "id": "evt1",
        "summary": "Design Review",
        "attendees": [],
        "description": "Fallback notes.",
        "attachments": [
            {"title": "Notes by Gemini", "fileUrl": "https://docs.google.com/document/d/abc123XYZ/edit"}
        ],
    }
    mock_docs = MagicMock()
    mock_docs.documents.return_value.get.return_value.execute.side_effect = Exception("insufficient scope")
    with (
        patch("calendarmate.integrations.google_calendar.calendar_service", return_value=mock_calendar),
        patch("calendarmate.integrations.google_calendar.docs_service", return_value=mock_docs),
    ):
        result = get_meeting_record_from_google_calendar("evt1", None)

    assert result is not None
    assert result.notes == "Fallback notes."
