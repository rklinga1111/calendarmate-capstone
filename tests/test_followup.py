import json
import os

import pytest

from calendarmate.followup import run_followup_agent
from calendarmate.tools.followup_tool import load_meetings
from fakes import ScriptedChatClient, final_response, text_and_tool_call_response, tool_call_response


def test_run_followup_agent_executes_get_meeting_record_and_returns_final_text() -> None:
    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "get_meeting_record", {"meeting_id": "m_001"}),
            final_response("Priya will finalize the roadmap doc; Marcus will schedule the review."),
        ]
    )
    result = run_followup_agent(
        "What were the action items from the Q3 Roadmap Sync?", client, meetings=load_meetings()
    )
    assert result == "Priya will finalize the roadmap doc; Marcus will schedule the review."
    assert len(client.calls) == 2


def test_run_followup_agent_can_look_up_then_send() -> None:
    # The scripted tool call still names Alice (mirroring a model that
    # naively copied the meeting's full attendee list, including the
    # sender) -- the point of this test is that the CODE excludes the
    # sender regardless, not that the model always gets this right on
    # its own.
    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "get_meeting_record", {"meeting_id": "m_001"}),
            tool_call_response(
                "call_2",
                "send_followup_email",
                {
                    "subject": "Follow-up: Q3 Roadmap Sync",
                    "body": "Action items: ...",
                    "recipients": ["Priya", "Marcus", "Alice"],
                },
            ),
            final_response("Sent the follow-up to Priya and Marcus."),
        ]
    )
    sent: list[dict] = []
    result = run_followup_agent(
        "Send a follow-up for the Q3 Roadmap Sync.",
        client,
        meetings=load_meetings(),
        sent=sent,
    )
    assert result == "Sent the follow-up to Priya and Marcus."
    assert len(sent) == 1
    assert sent[0]["recipients"] == ["Priya", "Marcus"]
    assert sent[0]["message_id"].startswith("mock-")


def test_get_meeting_fn_and_send_email_fn_overrides_are_used_instead_of_mocks() -> None:
    # The seam a real integration (e.g. Google Calendar/Gmail) plugs
    # into: passing get_meeting_fn/send_email_fn must route both tool
    # calls there instead of the mocked lookup/store.
    from calendarmate.tools.followup_tool import Meeting

    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "get_meeting_record", {"meeting_id": "real-evt-1"}),
            tool_call_response(
                "call_2",
                "send_followup_email",
                {
                    "subject": "Follow-up",
                    "body": "Real action items.",
                    "recipients": ["real.person@example.com"],
                },
            ),
            final_response("Sent via the real inbox."),
        ]
    )

    lookups_made: list[tuple] = []
    sends_made: list[tuple] = []

    def fake_get_meeting(meeting_id, title_hint):
        lookups_made.append((meeting_id, title_hint))
        return Meeting(
            meeting_id="real-evt-1",
            title="Real Meeting",
            attendees=("real.person@example.com",),
            notes="Real person will do the real thing.",
        )

    def fake_send_email(recipients, subject, body):
        sends_made.append((recipients, subject, body))
        return {"subject": subject, "body": body, "recipients": recipients, "message_id": "real-msg-1", "status": "sent"}

    # No `meetings=`/`sent=` at all -- the overrides don't need the mock's
    # own storage, proving they're fully independent of it.
    result = run_followup_agent(
        "Send a follow-up for the real event.",
        client,
        get_meeting_fn=fake_get_meeting,
        send_email_fn=fake_send_email,
    )

    assert result == "Sent via the real inbox."
    assert lookups_made == [("real-evt-1", None)]
    assert sends_made == [(["real.person@example.com"], "Follow-up", "Real action items.")]


def test_send_followup_email_excludes_the_sender_from_recipients() -> None:
    # Direct regression test for the exclusion itself, independent of
    # which meeting fixture happens to list Alice as an attendee.
    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "get_meeting_record", {"meeting_id": "m_001"}),
            tool_call_response(
                "call_2",
                "send_followup_email",
                {"subject": "Follow-up", "body": "...", "recipients": ["Priya", "Marcus", "Alice"]},
            ),
            final_response("Sent."),
        ]
    )
    sent: list[dict] = []
    run_followup_agent(
        "Send a follow-up for the Q3 Roadmap Sync.", client, meetings=load_meetings(), sent=sent, user_name="Alice"
    )
    assert sent[0]["recipients"] == ["Priya", "Marcus"]


def test_send_followup_email_refuses_when_sender_is_the_only_recipient() -> None:
    # If excluding the sender would leave nobody to actually send to,
    # this must refuse rather than silently sending an email to no one.
    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "get_meeting_record", {"meeting_id": "m_003"}),
            tool_call_response(
                "call_2",
                "send_followup_email",
                {"subject": "Follow-up", "body": "...", "recipients": ["Alice"]},
            ),
            final_response("Could not send -- no one else to notify."),
        ]
    )
    sent: list[dict] = []
    run_followup_agent(
        "Send a follow-up for the Quick Sync.", client, meetings=load_meetings(), sent=sent, user_name="Alice"
    )
    assert sent == []
    tool_result = json.loads(client.calls[2]["messages"][-1]["content"])
    assert "error" in tool_result


def test_run_followup_agent_returns_direct_text_when_model_skips_tools() -> None:
    client = ScriptedChatClient([final_response("Which meeting did you mean?")])
    sent: list[dict] = []
    result = run_followup_agent("send a follow-up", client, meetings=load_meetings(), sent=sent)
    assert result == "Which meeting did you mean?"
    assert sent == []


def test_run_followup_agent_preserves_intermediate_text_alongside_a_tool_call() -> None:
    # Same class of bug the Scheduler Agent hit: a message can carry both
    # text and a tool call in one turn. Verify that text isn't discarded.
    client = ScriptedChatClient(
        [
            text_and_tool_call_response(
                "Looking up the meeting now.", "call_1", "get_meeting_record", {"meeting_id": "m_003"}
            ),
            final_response("No action items were found for this meeting."),
        ]
    )
    result = run_followup_agent(
        "What were the action items from the Quick Sync?", client, meetings=load_meetings()
    )
    assert "Looking up the meeting now." in result
    assert "No action items were found for this meeting." in result


live = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="requires OPENAI_API_KEY to call the real model",
)


@pytest.fixture
def live_client():
    import openai

    return openai.OpenAI().chat.completions


@live
def test_6_1_m001_happy_path_grounded_action_items(live_client) -> None:
    result = run_followup_agent(
        "What were the action items from the Q3 Roadmap Sync?", live_client, meetings=load_meetings()
    )
    lowered = result.lower()
    assert "priya" in lowered and ("roadmap" in lowered)
    assert "marcus" in lowered and ("stakeholder" in lowered or "review" in lowered)
    assert "alice" in lowered and ("headcount" in lowered)


@live
def test_6_3_m003_empty_notes_says_nothing_to_report(live_client) -> None:
    sent: list[dict] = []
    result = run_followup_agent(
        "What were the action items from the Quick Sync?", live_client, meetings=load_meetings(), sent=sent
    )
    lowered = result.lower()
    assert any(phrase in lowered for phrase in ("no action item", "nothing", "no specific"))
    assert sent == []  # nothing was asked to be sent, and nothing was fabricated to send


@live
def test_6_3_m002_vague_notes_does_not_invent_a_commitment(live_client) -> None:
    result = run_followup_agent(
        "What were the action items from my 1:1 with Jordan?", live_client, meetings=load_meetings()
    )
    lowered = result.lower()
    assert any(
        phrase in lowered
        for phrase in ("no action item", "no concrete", "no specific", "nothing was decided", "no commitment")
    )
    # never turns the general career-growth discussion into an assigned task
    assert "jordan will" not in lowered
    assert "jordan to" not in lowered
    assert "jordan agreed" not in lowered


@live
def test_6_3_m004_excludes_the_non_action_statement(live_client) -> None:
    result = run_followup_agent(
        "What were the action items from the Design Review?", live_client, meetings=load_meetings()
    )
    lowered = result.lower()
    assert "eve" in lowered and ("api" in lowered or "documentation" in lowered)
    assert "frank" in lowered and ("security" in lowered or "audit" in lowered)
    # "approved, no further action needed" must not become a fabricated task
    assert "team will implement" not in lowered
    assert "need to implement the caching" not in lowered


@live
def test_6_2_send_uses_the_meetings_exact_attendees(live_client) -> None:
    # Recipients are the meeting's real attendees EXCEPT the sender --
    # Alice (the mocked persona, and default `user_name`) is one of the
    # Q3 Roadmap Sync's attendees, but a real send once needlessly
    # included the sender as a recipient of their own follow-up email.
    sent: list[dict] = []
    result = run_followup_agent(
        "Send a follow-up email for the Q3 Roadmap Sync recapping the action items.",
        live_client,
        meetings=load_meetings(),
        sent=sent,
    )
    assert len(sent) == 1
    assert set(sent[0]["recipients"]) == {"Priya", "Marcus"}
    assert "Alice" not in sent[0]["recipients"]
    assert sent[0]["subject"]
    assert sent[0]["body"]
    # the draft is shown in the reply too, not just sent silently
    lowered = result.lower()
    assert "priya" in lowered or "marcus" in lowered or "alice" in lowered
