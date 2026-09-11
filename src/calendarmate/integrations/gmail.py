"""Real Gmail-backed versions of the email tools.

Same `Email` shape as `calendarmate.tools.email_tool` and the same
`send_followup_email` result shape as
`calendarmate.tools.followup_tool`, so they're drop-in replacements at
the call site. Not used by the pytest suite or `harness.py` -- see the
module docstring in `google_calendar.py` for why.
"""

from __future__ import annotations

import base64
import html
import re
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText

from calendarmate.integrations.google_auth import gmail_service
from calendarmate.tools.email_tool import Email

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _inline_markdown_to_html(text: str) -> str:
    # Escape first (neither `**` nor the surrounding text are HTML-special
    # except for stray <, >, & a sender might type) -- html.escape doesn't
    # touch asterisks, so the bold pattern is still there to match afterward.
    return _BOLD_RE.sub(r"<b>\1</b>", html.escape(text))


def _markdown_body_to_html(body: str) -> str:
    """A minimal, targeted Markdown-to-HTML conversion for outgoing
    follow-up emails -- just enough for what these emails actually
    contain (bold, bullet lists, paragraphs), not a general-purpose
    parser. `send_email_via_gmail` sends MIMEText literally: a real
    follow-up once went out with visible "**bold**" asterisks in the
    recipient's inbox instead of actual bold text, because the agent
    drafts in Markdown but nothing converted it before sending.
    """
    lines = body.split("\n")
    html_parts: list[str] = []
    in_list = False
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("- "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_inline_markdown_to_html(line[2:])}</li>")
            continue
        if in_list:
            html_parts.append("</ul>")
            in_list = False
        if line:
            html_parts.append(f"<p>{_inline_markdown_to_html(line)}</p>")
    if in_list:
        html_parts.append("</ul>")
    return "\n".join(html_parts)


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _decode_body(part)
        if text:
            return text
    return ""


def _own_email_address(service) -> str:
    return service.users().getProfile(userId="me").execute()["emailAddress"]


def _thread_already_replied(service, thread_id: str, message_id: str, own_address: str) -> bool:
    """True if the user has since sent a later message in this thread.

    Read status alone can't tell "opened it" apart from "opened it AND
    replied" -- a real recruiter email stayed flagged as needing
    attention even after the user had actually replied, since Gmail only
    ever marks the original message read, not "answered". Gmail returns
    a thread's messages in chronological order, so the thread has a
    reply if its LAST message is (a) not this same message and (b) from
    the user's own address.
    """
    thread = (
        service.users()
        .threads()
        .get(userId="me", id=thread_id, format="metadata", metadataHeaders=["From"])
        .execute()
    )
    messages = thread.get("messages", [])
    if not messages:
        return False
    last = messages[-1]
    if last.get("id") == message_id:
        return False  # this message IS the latest -- nothing sent since
    sender = _header(last.get("payload", {}).get("headers", []), "From")
    return own_address.lower() in sender.lower()


def _message_to_email(service, own_address: str, message: dict) -> Email:
    headers = message.get("payload", {}).get("headers", [])
    received_at = datetime.fromtimestamp(int(message["internalDate"]) / 1000).date()
    read = "UNREAD" not in message.get("labelIds", [])
    # Only bother checking the thread for messages that are already read
    # -- an unread message can't possibly have a reply after it, since
    # Gmail marks a message read the moment the user acts on its thread.
    # This keeps the extra per-message API call limited to the (usually
    # much smaller) set of messages where it can actually matter.
    replied = read and _thread_already_replied(service, message["threadId"], message["id"], own_address)
    return Email(
        sender=_header(headers, "From"),
        subject=_header(headers, "Subject"),
        body=_decode_body(message.get("payload", {})),
        read=read,
        received_at=received_at,
        replied=replied,
    )


def _fetch_messages(service, query: str, max_results: int) -> list[Email]:
    own_address = _own_email_address(service)
    listing = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    emails = []
    for item in listing.get("messages", []):
        message = service.users().messages().get(userId="me", id=item["id"], format="full").execute()
        emails.append(_message_to_email(service, own_address, message))
    return emails


def load_unread_emails_from_gmail(max_results: int = 20) -> list[Email]:
    # `in:inbox` matters here for the same reason it matters below: without
    # it, Gmail's search isn't scoped to received mail at all. A self-sent
    # message is never "unread" (Gmail marks your own sent mail as read
    # automatically), so this particular query happened not to be affected
    # in practice -- but scoping it explicitly is the correct, robust thing
    # regardless of which queries currently happen to dodge the bug.
    return _fetch_messages(gmail_service(), "in:inbox is:unread", max_results)


def load_emails_in_range_from_gmail(
    start_date: date, end_date: date, max_results: int = 50
) -> list[Email]:
    """Every email received in [start_date, end_date], inclusive -- read
    or unread. Gmail's `before:` search operator is exclusive of that
    calendar day, so it's given end_date + 1 day to make the range
    inclusive on both ends.

    `in:inbox` is required, not cosmetic: a bare `after:`/`before:` query
    has no folder scope at all, and matches Gmail's entire "All Mail"
    view -- including messages the user sent themselves. A real reply the
    user had sent ("Re: Product Owner", from their own address) surfaced
    this: it showed up as a "received" email, and being read didn't stop
    it from being pulled into the "needs attention" pool once that pool
    started considering recently-read mail. `in:inbox` restricts results
    to actually-received mail the same way "every email received in this
    range" already claims to mean.
    """
    query = (
        f"in:inbox after:{start_date.strftime('%Y/%m/%d')} "
        f"before:{(end_date + timedelta(days=1)).strftime('%Y/%m/%d')}"
    )
    return _fetch_messages(gmail_service(), query, max_results)


def send_email_via_gmail(to: list[str], subject: str, body: str) -> dict:
    """Sends a real email. Returns the same shape as the mocked
    `followup_tool.send_followup_email`.

    `body` is drafted in Markdown by the agent (see `followup.md`) and
    converted to real HTML here before sending -- a plain-text `MIMEText`
    renders `**bold**` as literal asterisks in the recipient's inbox, not
    as bold, which a real sent follow-up did before this was fixed. The
    returned `body` stays the original Markdown, matching what the draft
    already showed the user, not the HTML actually transmitted.
    """
    service = gmail_service()
    message = MIMEText(_markdown_body_to_html(body), "html")
    message["to"] = ", ".join(to)
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {
        "subject": subject,
        "body": body,
        "recipients": list(to),
        "message_id": sent.get("id", ""),
        "status": "sent",
    }
