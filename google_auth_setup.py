"""One-time OAuth setup for the real Google Calendar/Gmail integration.

Run this yourself, once, from a terminal on a machine with a browser:

    python google_auth_setup.py

It opens a browser, asks you to log in and grant access, and saves the
resulting credentials to token.json in this directory. Nothing in this
project can complete that consent step for you -- it has to be you,
approving access to your own account.

Before running this, you need credentials.json in this same directory:

1. Go to https://console.cloud.google.com/ and create a project (or pick
   an existing one).
2. APIs & Services -> Library -> enable "Google Calendar API",
   "Gmail API", and "Google Docs API" (the last one is only used to read
   a meeting's attached "Notes by Gemini" doc, if it has one).
3. APIs & Services -> OAuth consent screen -> configure it (External is
   fine for personal testing; add your own email as a test user).
4. APIs & Services -> Credentials -> Create Credentials -> OAuth client
   ID -> Application type "Desktop app".
5. Download the resulting JSON and save it as credentials.json right
   here, next to this script.

Both credentials.json and token.json are gitignored -- never commit
either one; they grant real access to your real account.

If you already have a token.json from before the Google Docs scope was
added (see google_auth.SCOPES), delete it and re-run this script -- an
existing valid token is reused as-is and won't automatically pick up a
newly-added scope; it needs a fresh consent screen to grant it.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from calendarmate.integrations.google_auth import SCOPES

_CREDENTIALS_PATH = Path(__file__).parent / "credentials.json"
_TOKEN_PATH = Path(__file__).parent / "token.json"


def main() -> None:
    if not _CREDENTIALS_PATH.exists():
        raise SystemExit(
            f"credentials.json not found at {_CREDENTIALS_PATH}.\n"
            "See the instructions at the top of this file for how to create it "
            "in Google Cloud Console."
        )

    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(_CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        _TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    print(f"Saved credentials to {_TOKEN_PATH}. You're ready to use the real Calendar/Gmail integration.")


if __name__ == "__main__":
    main()
