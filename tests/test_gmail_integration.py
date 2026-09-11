"""Tests for the real Gmail integration's query construction -- not its
actual API calls, which need real credentials and are exercised manually
via live_assistant.py instead (see CLAUDE.md).
"""

import base64
from datetime import date
from email import message_from_bytes
from unittest.mock import MagicMock, patch

from calendarmate.integrations.gmail import (
    _markdown_body_to_html,
    load_emails_in_range_from_gmail,
    load_unread_emails_from_gmail,
    send_email_via_gmail,
)


def _mock_service_with_empty_results() -> MagicMock:
    mock_service = MagicMock()
    mock_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": []
    }
    return mock_service


def test_date_range_query_is_scoped_to_inbox() -> None:
    # A bare after:/before: query has no folder scope at all and matches
    # Gmail's entire "All Mail" view, including messages the user sent
    # themselves -- a real reply the user had sent once surfaced as a
    # "received" email this way. `in:inbox` must always be present so the
    # query only ever matches actually-received mail.
    mock_service = _mock_service_with_empty_results()
    with patch("calendarmate.integrations.gmail.gmail_service", return_value=mock_service):
        load_emails_in_range_from_gmail(date(2025, 6, 16), date(2025, 6, 22))

    query = mock_service.users.return_value.messages.return_value.list.call_args.kwargs["q"]
    assert "in:inbox" in query
    assert "after:2025/06/16" in query
    assert "before:2025/06/23" in query


def test_unread_query_is_scoped_to_inbox() -> None:
    mock_service = _mock_service_with_empty_results()
    with patch("calendarmate.integrations.gmail.gmail_service", return_value=mock_service):
        load_unread_emails_from_gmail()

    query = mock_service.users.return_value.messages.return_value.list.call_args.kwargs["q"]
    assert "in:inbox" in query
    assert "is:unread" in query


def _raw_message(message_id: str, thread_id: str, read: bool, from_header: str = "sender@example.com") -> dict:
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": "1750000000000",
        "labelIds": [] if read else ["UNREAD"],
        "payload": {
            "headers": [{"name": "From", "value": from_header}, {"name": "Subject", "value": "Subject"}],
            "mimeType": "text/plain",
            "body": {},
        },
    }


def _service_for_one_message(message: dict, thread_messages: list[dict]) -> MagicMock:
    mock_service = MagicMock()
    mock_service.users.return_value.getProfile.return_value.execute.return_value = {
        "emailAddress": "me@example.com"
    }
    mock_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": message["id"]}]
    }
    mock_service.users.return_value.messages.return_value.get.return_value.execute.return_value = message
    mock_service.users.return_value.threads.return_value.get.return_value.execute.return_value = {
        "messages": thread_messages
    }
    return mock_service


def test_read_message_with_no_reply_is_not_marked_replied() -> None:
    message = _raw_message("msg1", "thread1", read=True)
    mock_service = _service_for_one_message(message, thread_messages=[message])
    with patch("calendarmate.integrations.gmail.gmail_service", return_value=mock_service):
        emails = load_unread_emails_from_gmail()
    assert emails[0].replied is False


def test_read_message_with_a_later_reply_from_self_is_marked_replied() -> None:
    # The user's own reply is chronologically the LAST message in the
    # thread -- Gmail returns thread messages in that order.
    original = _raw_message("msg1", "thread1", read=True, from_header="sender@example.com")
    my_reply = _raw_message("msg2", "thread1", read=True, from_header="Me <me@example.com>")
    mock_service = _service_for_one_message(original, thread_messages=[original, my_reply])
    with patch("calendarmate.integrations.gmail.gmail_service", return_value=mock_service):
        emails = load_unread_emails_from_gmail()
    assert emails[0].replied is True


def test_unread_message_never_triggers_a_thread_lookup() -> None:
    # An unread message can't possibly have a reply after it -- Gmail
    # marks a message read the moment its thread is acted on. Skipping
    # the thread lookup for unread messages keeps the extra per-message
    # API call limited to where it can actually matter.
    message = _raw_message("msg1", "thread1", read=False)
    mock_service = _service_for_one_message(message, thread_messages=[message])
    with patch("calendarmate.integrations.gmail.gmail_service", return_value=mock_service):
        emails = load_unread_emails_from_gmail()
    assert emails[0].replied is False
    mock_service.users.return_value.threads.return_value.get.assert_not_called()


def test_markdown_body_to_html_converts_bold_and_bullets() -> None:
    # A real follow-up email once went out with literal "**bold**"
    # asterisks visible in the recipient's inbox, because the agent
    # drafts in Markdown but MIMEText sends it as plain text verbatim.
    body = "Hi Team,\n\n**Action Items:**\n- Alice: send the doc\n- Bob: review PR\n\nThanks,\nMe"
    result = _markdown_body_to_html(body)
    assert "<b>Action Items:</b>" in result
    assert "**" not in result
    assert "<ul>" in result and "</ul>" in result
    assert "<li>Alice: send the doc</li>" in result
    assert "<li>Bob: review PR</li>" in result
    assert "<p>Hi Team,</p>" in result


def test_markdown_body_to_html_escapes_special_characters() -> None:
    # A plain-text body might contain a literal "<" or "&" that must not
    # be interpreted as real HTML once this is sent as an HTML email.
    result = _markdown_body_to_html("Use A < B && C > D")
    assert "&lt;" in result
    assert "&amp;&amp;" in result
    assert "&gt;" in result


def test_send_email_via_gmail_sends_html_not_raw_markdown() -> None:
    mock_service = MagicMock()
    mock_service.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": "sent-123"
    }
    with patch("calendarmate.integrations.gmail.gmail_service", return_value=mock_service):
        result = send_email_via_gmail(["bob@example.com"], "Subject", "**Bold** text\n- item one")

    send_kwargs = mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    raw_bytes = base64.urlsafe_b64decode(send_kwargs["body"]["raw"])
    sent_message = message_from_bytes(raw_bytes)
    assert sent_message.get_content_type() == "text/html"
    sent_body = sent_message.get_payload(decode=True).decode("utf-8")
    assert "<b>Bold</b>" in sent_body
    assert "**" not in sent_body
    assert "<li>item one</li>" in sent_body

    # The result dict's `body` stays the original Markdown draft --
    # matching what was already shown to the user, not the HTML actually
    # transmitted over the wire.
    assert result["body"] == "**Bold** text\n- item one"
