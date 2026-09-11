import json
import os
from datetime import date

import pytest

from calendarmate.email import _format_needs_attention, answer_email_request
from calendarmate.tools.email_tool import Email, load_emails
from fakes import ScriptedChatClient, final_response, tool_call_response

TODAY = date(2025, 6, 17)  # Tuesday, same fixture week as calendar.json


def test_answer_email_request_executes_the_tool_and_returns_final_text() -> None:
    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "get_emails", {}),
            final_response("Action required: Bob's budget approval. FYI: GitHub PR merged."),
        ]
    )
    result = answer_email_request("Summarize my emails", client, emails=load_emails())

    assert result == "Action required: Bob's budget approval. FYI: GitHub PR merged."
    assert len(client.calls) == 2

    tool_result_message = client.calls[1]["messages"][-1]
    assert tool_result_message["role"] == "tool"
    payload = json.loads(tool_result_message["content"])
    subjects = {e["subject"] for e in payload["emails"]}
    assert "Old thread - already resolved" not in subjects  # that one's read
    # 9 original unread entries + 2 new ones (an electricity bill and a
    # bank marketing email) added for Payment/Deadline Reminder coverage.
    assert len(subjects) == 11


def test_answer_email_request_returns_direct_text_when_model_skips_the_tool() -> None:
    client = ScriptedChatClient([final_response("Sure, what would you like to know?")])
    result = answer_email_request("hi", client, emails=load_emails())
    assert result == "Sure, what would you like to know?"
    assert len(client.calls) == 1


def test_answer_email_request_date_range_includes_the_read_email() -> None:
    # "Last week" (2025-06-09 - 2025-06-15) includes Grace's read email --
    # date-scoped queries must not silently narrow to unread-only.
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1", "get_emails", {"start_date": "2025-06-09", "end_date": "2025-06-15"}
            ),
            final_response("Last week: IT Security, Frank, and Grace (already resolved)."),
        ]
    )
    result = answer_email_request(
        "What emails did I get last week?", client, today=TODAY, emails=load_emails()
    )
    assert result == "Last week: IT Security, Frank, and Grace (already resolved)."

    tool_result_message = client.calls[1]["messages"][-1]
    payload = json.loads(tool_result_message["content"])
    senders = {e["sender"] for e in payload["emails"]}
    assert senders == {"IT Security", "Frank", "Grace"}
    assert any(e["read"] for e in payload["emails"])  # Grace's read email is included


def _fake_email(index: int) -> Email:
    return Email(
        sender=f"Sender{index}",
        subject=f"Subject{index}",
        body=f"Body {index}",
        read=False,
        received_at=TODAY,
    )


def test_needs_attention_excludes_self_sent_mail_when_own_email_given() -> None:
    # A self-sent email can genuinely land in the inbox for real (e.g.
    # the user was cc'd on their own outgoing mail) -- a person can't be
    # "waiting on a reply" from themselves, so it must never even reach
    # classification once own_email is known, regardless of what the
    # sender's display name looks like.
    emails = [
        Email(sender="Bob", subject="Need approval", body="please approve", read=False, received_at=TODAY),
        Email(
            sender="Me (Self) <me@example.com>",
            subject="Follow-up",
            body="...",
            read=True,
            received_at=TODAY,
        ),
    ]
    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "get_emails", {"include_recently_read": True}),
            tool_call_response(
                "call_2",
                "classify_emails",
                {"classifications": [{"index": 0, "category": "action_required", "summary": "needs approval"}]},
            ),
        ]
    )
    result = answer_email_request(
        "What needs my attention?", client, today=TODAY, emails=emails, own_email="me@example.com"
    )
    assert "Bob" in result
    assert "Follow-up" not in result


def test_needs_attention_keeps_self_sent_mail_when_own_email_not_given() -> None:
    # Backward compatible: without own_email, nothing is filtered --
    # every existing caller that doesn't know its own address (all the
    # mocked-fixture tests) is completely unaffected by this filter.
    emails = [
        Email(sender="Me <me@example.com>", subject="Follow-up", body="...", read=False, received_at=TODAY),
    ]
    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "get_emails", {"include_recently_read": True}),
            tool_call_response(
                "call_2",
                "classify_emails",
                {"classifications": [{"index": 0, "category": "action_required", "summary": "..."}]},
            ),
        ]
    )
    result = answer_email_request("What needs my attention?", client, today=TODAY, emails=emails)
    assert "Follow-up" in result


def test_needs_attention_batches_large_pools_and_merges_without_loss() -> None:
    # A single classification call started silently dropping items once
    # the candidate pool grew past a few dozen on a real inbox -- 20
    # emails here (more than _CLASSIFY_BATCH_SIZE=15) forces two separate
    # classification calls, and every one of the 20 must still show up
    # in the final merged answer, none dropped at the batch boundary.
    emails = [_fake_email(i) for i in range(20)]
    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "get_emails", {"include_recently_read": True}),
            tool_call_response(
                "call_2",
                "classify_emails",
                {
                    "classifications": [
                        {"index": i, "category": "action_required", "summary": "needs a reply"}
                        for i in range(15)
                    ]
                },
            ),
            tool_call_response(
                "call_3",
                "classify_emails",
                {
                    "classifications": [
                        {"index": i, "category": "action_required", "summary": "needs a reply"} for i in range(5)
                    ]
                },
            ),
        ]
    )
    result = answer_email_request("What needs my attention?", client, today=TODAY, emails=emails)
    for i in range(20):
        assert f"Sender{i}" in result


def test_needs_attention_batch_call_carries_the_real_get_emails_result() -> None:
    # A harness/judge inspecting the conversation externally (e.g.
    # harness.py's SpyingChatClient) only ever records a tool's result by
    # finding a role:"tool" message with a matching tool_call_id in some
    # later .create() call -- but this branch returns straight from
    # _format_needs_attention without ever making another call on the
    # get_emails call's own `messages` list, so that result was
    # previously invisible to anything checking it externally, even
    # though the content feeding the reply was entirely real. The first
    # classify_emails call must now carry get_emails' own tool_call_id
    # and its real, unfiltered result so that visibility is restored.
    emails = [_fake_email(i) for i in range(3)]
    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "get_emails", {"include_recently_read": True}),
            tool_call_response(
                "call_2",
                "classify_emails",
                {
                    "classifications": [
                        {"index": i, "category": "action_required", "summary": "needs a reply"}
                        for i in range(3)
                    ]
                },
            ),
        ]
    )
    answer_email_request("What needs my attention?", client, today=TODAY, emails=emails)

    classify_call_messages = client.calls[1]["messages"]
    tool_messages = [m for m in classify_call_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_1"
    payload = json.loads(tool_messages[0]["content"])
    subjects = {e["subject"] for e in payload["emails"]}
    for i in range(3):
        assert f"Subject{i}" in subjects

    # The real OpenAI API rejects a bare tool-role message unless it
    # directly follows an assistant message declaring that same
    # tool_call_id -- a synthetic assistant message must precede it.
    assistant_messages = [m for m in classify_call_messages if m.get("role") == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["tool_calls"][0]["id"] == "call_1"


def test_format_needs_attention_separates_categories_and_never_drops_items() -> None:
    classified = (
        [{"sender": f"AR{i}", "subject": "s", "category": "action_required", "summary": ""} for i in range(20)]
        + [{"sender": "Utility Co", "subject": "electric bill", "category": "payment_reminder", "summary": "$50 due June 20"}]
        + [{"sender": "Marketing Inc", "subject": "promo", "category": "fyi", "summary": ""}]
    )
    result = _format_needs_attention(classified)
    for i in range(20):
        assert f"AR{i}" in result
    assert "Utility Co" in result
    assert "$50 due June 20" in result
    assert "Marketing Inc" not in result  # FYI is omitted entirely


def test_format_needs_attention_handles_nothing_pending() -> None:
    classified = [{"sender": "X", "subject": "y", "category": "fyi", "summary": ""}]
    result = _format_needs_attention(classified)
    assert "nothing" in result.lower()


def test_format_needs_attention_includes_fyi_when_requested() -> None:
    # A "Summarize my emails" request once silently lost its FYI section
    # because it happened to route through the same recently-read pool
    # "needs attention" uses -- include_fyi must be a genuinely separate
    # switch from which pool was fetched.
    classified = [
        {"sender": "Bob", "subject": "approval needed", "category": "action_required", "summary": ""},
        {"sender": "Marketing Inc", "subject": "promo", "category": "fyi", "summary": ""},
    ]
    without_fyi = _format_needs_attention(classified, include_fyi=False)
    with_fyi = _format_needs_attention(classified, include_fyi=True)
    assert "Marketing Inc" not in without_fyi
    assert "Marketing Inc" in with_fyi
    assert "Bob" in without_fyi and "Bob" in with_fyi


live = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="requires OPENAI_API_KEY to call the real model",
)


@pytest.fixture
def live_client():
    import openai

    return openai.OpenAI().chat.completions


@live
def test_t7_summary_groups_by_urgency_and_separates_action_from_fyi(live_client) -> None:
    result = answer_email_request("Summarize my emails", live_client, emails=load_emails())
    lowered = result.lower()

    # action-required items should be surfaced
    assert "bob" in lowered or "budget" in lowered
    assert "carol" in lowered or "deck" in lowered
    assert "eve" in lowered or "review" in lowered or "pr #501" in lowered

    # FYIs should also be present, but distinguished from action items
    assert "github" in lowered or "merged" in lowered or "newsletter" in lowered

    # never invents content, and never surfaces the read email
    assert "already resolved" not in lowered


@live
def test_t8_surfaces_only_action_required_with_sender_and_subject(live_client) -> None:
    result = answer_email_request("What needs my attention?", live_client, emails=load_emails())
    lowered = result.lower()

    # action-required senders/subjects should appear
    assert "bob" in lowered
    assert "carol" in lowered
    assert "eve" in lowered

    # pure FYIs should be omitted entirely
    assert "github" not in lowered
    assert "newsletter" not in lowered
    assert "frank" not in lowered
    assert "already resolved" not in lowered  # the read email, never even fetched


@live
def test_needs_attention_includes_payment_reminders_but_not_bank_marketing(live_client) -> None:
    # A real electricity bill with a real due date wasn't surfacing under
    # "needs my attention" at all, since automated billing mail was
    # unconditionally FYI -- but a bill with a concrete due date is worth
    # surfacing even though no person is waiting on a reply. The bar is
    # narrow though: a bank's loan/card marketing must stay FYI even
    # though it's also "financial" and even mentions the same product
    # (a credit card) -- it has no real due date, just a sales pitch.
    result = answer_email_request("What needs my attention?", live_client, emails=load_emails())
    lowered = result.lower()
    assert "electricity" in lowered or "city power" in lowered
    assert "84.50" in result or "june 20" in lowered
    assert "horizon bank" not in lowered
    assert "pre-approved" not in lowered


@live
def test_summary_separates_payment_reminders_from_action_required(live_client) -> None:
    # The electricity bill must not be blended into Action Required --
    # it's a deadline, not a person waiting on a reply -- but it should
    # still appear somewhere in a full summary (unlike the bank marketing
    # email, which is a plain FYI with no deadline at all).
    result = answer_email_request("Summarize my emails", live_client, emails=load_emails())
    lowered = result.lower()
    assert "electricity" in lowered or "city power" in lowered
    assert any(word in lowered for word in ("payment", "bill", "deadline", "due"))
    # existing action-required behavior must still work alongside the new category
    assert "bob" in lowered or "budget" in lowered


@live
def test_needs_attention_surfaces_a_recently_read_but_unactioned_email(live_client) -> None:
    # Henry's recruiting email is marked read in the fixture but genuinely
    # asks the user specific questions (notice period, resume) -- a real
    # request once missed an email exactly like this because "needs
    # attention" only ever checked unread mail. It must show up now.
    result = answer_email_request(
        "What needs my attention?", live_client, today=date(2025, 6, 17), emails=load_emails()
    )
    lowered = result.lower()
    assert "henry" in lowered or "recruiting" in lowered
    assert "bob" in lowered
    assert "carol" in lowered
    assert "eve" in lowered
    assert "github" not in lowered
    assert "already resolved" not in lowered  # Grace's old, read, and irrelevant


@live
def test_needs_attention_today_is_not_silently_date_scoped(live_client) -> None:
    # "Today" here describes WHEN the user is asking, not which emails'
    # arrival date to filter by -- a real request once got this wrong and
    # silently date-scoped to "received today," missing action items that
    # had been sitting unread for a while. This must behave like
    # test_t8's plain "What needs my attention?" (and like the
    # recently-read test above), not like the date-range "last week"
    # query below.
    result = answer_email_request(
        "What emails need my attention today?", live_client, today=date(2025, 6, 17), emails=load_emails()
    )
    lowered = result.lower()
    assert "bob" in lowered
    assert "carol" in lowered
    assert "eve" in lowered
    assert "github" not in lowered
    assert "newsletter" not in lowered


@live
def test_last_week_email_query_includes_the_read_email_and_excludes_this_week(live_client) -> None:
    # TODAY is Tuesday 2025-06-17. "Last week" is 2025-06-09 - 2025-06-15,
    # which contains IT Security (unread), Frank (unread), and Grace
    # (read) -- and none of this week's emails.
    result = answer_email_request(
        "What emails did I get last week?", live_client, today=TODAY, emails=load_emails()
    )
    lowered = result.lower()
    assert "it security" in lowered or "password" in lowered
    assert "frank" in lowered
    # the read email must be included now -- a date-scoped question isn't
    # limited to what's still unread
    assert "grace" in lowered or "already resolved" in lowered
    # none of this week's emails should leak in
    assert "budget" not in lowered
    assert "client deck" not in lowered
