"""Shared credential loading for the real Google Calendar/Gmail integration.

Completely separate from everything else in `calendarmate` -- nothing
under `src/calendarmate/tools/` or the pytest suite imports this module,
so running the tests or the eval harness never touches this, and never
needs a real token to exist.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

# Kept in sync with google_auth_setup.py's SCOPES by hand -- duplicated
# rather than imported from that root-level script, since a package
# module importing a script would invert the normal dependency direction.
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    # Read-only access to a meeting's attached "Notes by Gemini" doc --
    # Google Calendar itself has no native meeting-notes field, but a
    # Google Meet call with Gemini note-taking enabled attaches a real
    # Google Doc to the event instead. Added after a real follow-up
    # request found actual notes existed for a meeting, just not where
    # `get_meeting_record_from_google_calendar` was looking.
    "https://www.googleapis.com/auth/documents.readonly",
]

_TOKEN_PATH = Path(__file__).parent.parent.parent.parent / "token.json"


def get_credentials() -> Credentials:
    if not _TOKEN_PATH.exists():
        raise RuntimeError(
            f"No token.json found at {_TOKEN_PATH}. Run `python google_auth_setup.py` "
            "from the project root first -- see that file for the one-time setup steps."
        )
    creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def calendar_service() -> Resource:
    return build("calendar", "v3", credentials=get_credentials())


def gmail_service() -> Resource:
    return build("gmail", "v1", credentials=get_credentials())


def docs_service() -> Resource:
    return build("docs", "v1", credentials=get_credentials())
