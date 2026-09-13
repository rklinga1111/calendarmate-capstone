import json
import os
import re
from datetime import date, datetime, time

import pytest

from calendarmate.scheduler import handle_scheduling_request
from calendarmate.tools.calendar_tool import load_events
from fakes import ScriptedChatClient, final_response, tool_call_response, tool_calls_response

# Same fixture week as the Briefing Agent tests: Mon 2025-06-16 - Sun 2025-06-22.
TODAY = date(2025, 6, 17)  # Tuesday


def test_create_event_for_an_already_passed_time_is_refused_regardless_of_model_behavior() -> None:
    # Defense-in-depth: even if the model calls create_event for a time
    # that's already passed (a real request once did, despite the prompt
    # explicitly forbidding it), _dispatch_tool itself must refuse -- the
    # same way attendee-email validation isn't left to prompt compliance
    # alone. TODAY is 2025-06-17; `now` simulates 15:00 that same day, so
    # a 10:00 booking on that date is already in the past.
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1",
                "create_event",
                {
                    "title": "Sync with Bob",
                    "date": "2025-06-17",
                    "start": "10:00",
                    "end": "10:30",
                    "attendees": ["Alice", "Bob"],
                },
            ),
            final_response("That time has already passed."),
        ]
    )
    booked: list[dict] = []
    result = handle_scheduling_request(
        "irrelevant -- the scripted tool call is what's under test",
        client,
        today=TODAY,
        now=datetime.combine(TODAY, time(15, 0)),
        events=load_events(),
        booked=booked,
    )

    assert booked == []
    tool_result = json.loads(client.calls[1]["messages"][-1]["content"])
    assert "error" in tool_result
    assert result == "That time has already passed."


def test_create_event_with_recurrence_builds_an_rrule_and_records_it() -> None:
    # The model supplies simple structured fields (frequency, weekdays,
    # an end date) -- Python builds the actual RRULE string, the same
    # "let Python compute it, don't ask the model to" pattern used for
    # date/weekday resolution elsewhere in this file.
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1",
                "create_event",
                {
                    "title": "Design Standup",
                    "date": "2025-06-19",
                    "start": "10:00",
                    "end": "10:15",
                    "attendees": ["Alice", "Bob"],
                    "recurrence": {
                        "frequency": "weekly",
                        "weekdays": ["MO", "TU", "WE", "TH", "FR"],
                        "until": "2026-12-31",
                    },
                },
            ),
            final_response("Booked a recurring daily standup on weekdays through Dec 31, 2026."),
        ]
    )
    booked: list[dict] = []
    result = handle_scheduling_request(
        "Book a recurring 15-minute standup with Bob every weekday at 10am until Dec 31, 2026.",
        client,
        today=TODAY,
        events=load_events(),
        booked=booked,
    )

    assert "recurring" in result.lower()
    assert len(booked) == 1
    assert booked[0]["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;UNTIL=20270101T000000Z"]


def test_create_event_without_recurrence_records_no_recurrence_field() -> None:
    # A plain one-off booking must be completely unaffected by the new
    # optional field -- `booked`'s record shape for a normal meeting
    # shouldn't gain a "recurrence" key it never had before.
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1",
                "create_event",
                {
                    "title": "Sync with Bob",
                    "date": "2025-06-19",
                    "start": "10:00",
                    "end": "10:30",
                    "attendees": ["Alice", "Bob"],
                },
            ),
            final_response("Booked."),
        ]
    )
    booked: list[dict] = []
    handle_scheduling_request(
        "Schedule a 30-minute meeting with Bob on Thursday at 10am.",
        client,
        today=TODAY,
        events=load_events(),
        booked=booked,
    )
    assert "recurrence" not in booked[0]


def test_happy_path_checks_then_books_then_confirms() -> None:
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1",
                "check_availability",
                {"attendees": ["Bob"], "date": "2025-06-19", "start": "10:00", "end": "10:30"},
            ),
            tool_call_response(
                "call_2",
                "create_event",
                {
                    "title": "Sync with Bob",
                    "date": "2025-06-19",
                    "start": "10:00",
                    "end": "10:30",
                    "attendees": ["Alice", "Bob"],
                },
            ),
            final_response("Booked Sync with Bob for Thu 10:00-10:30. Meet link included."),
        ]
    )
    booked: list[dict] = []
    result = handle_scheduling_request(
        "Schedule a 30-minute meeting with Bob on Thursday at 10am.",
        client,
        today=TODAY,
        events=load_events(),
        booked=booked,
    )

    assert result == "Booked Sync with Bob for Thu 10:00-10:30. Meet link included."
    assert len(booked) == 1
    assert booked[0]["attendees"] == ["Alice", "Bob"]
    assert booked[0]["meet_link"].startswith("https://meet.google.com/")

    availability_result = json.loads(client.calls[1]["messages"][-1]["content"])
    assert availability_result["available"] is True


def test_create_event_fn_override_is_used_instead_of_the_mock() -> None:
    # This is the seam a real integration (e.g. Google Calendar) plugs
    # into: passing create_event_fn must route create_event calls there
    # instead of the mocked booking store, and its result must flow
    # through as the tool result the model sees.
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1",
                "check_availability",
                {"attendees": ["Bob"], "date": "2025-06-19", "start": "10:00", "end": "10:30"},
            ),
            tool_call_response(
                "call_2",
                "create_event",
                {
                    "title": "Sync with Bob",
                    "date": "2025-06-19",
                    "start": "10:00",
                    "end": "10:30",
                    "attendees": ["Alice", "Bob"],
                },
            ),
            final_response("Booked via the real calendar."),
        ]
    )
    calls_made: list[tuple] = []

    def fake_real_create_event(title, day, start, end, attendees, recurrence=None):
        calls_made.append((title, day, start, end, attendees, recurrence))
        return {"title": title, "event_id": "real-event-123", "meet_link": "https://meet.google.com/real"}

    booked: list[dict] = []  # the mock's own store -- must stay untouched
    result = handle_scheduling_request(
        "Schedule a 30-minute meeting with Bob on Thursday at 10am.",
        client,
        today=TODAY,
        events=load_events(),
        booked=booked,
        create_event_fn=fake_real_create_event,
    )

    assert result == "Booked via the real calendar."
    assert booked == []  # the mock store was never touched
    assert len(calls_made) == 1
    assert calls_made[0][0] == "Sync with Bob"
    assert calls_made[0][4] == ["Alice", "Bob"]

    create_event_result = json.loads(client.calls[2]["messages"][-1]["content"])
    assert create_event_result["event_id"] == "real-event-123"


def test_user_name_defaults_to_alice_and_is_overridable() -> None:
    # The mocked fixture's persona ("Alice") is hardcoded nowhere in
    # scheduler.py itself anymore -- it's the default value of an
    # injectable parameter, the same way `today` is. A real integration
    # (live_assistant.py) passes the actual authenticated account's
    # identity instead, since "Alice" would be nonsensical there.
    default_client = ScriptedChatClient([final_response("...")])
    handle_scheduling_request("hi", default_client, today=TODAY, events=load_events())
    assert "Alice" in default_client.calls[0]["messages"][0]["content"]

    override_client = ScriptedChatClient([final_response("...")])
    handle_scheduling_request(
        "hi", override_client, today=TODAY, events=load_events(), user_name="taylor@example.com"
    )
    system_message = override_client.calls[0]["messages"][0]["content"]
    assert "taylor@example.com" in system_message
    assert "Alice" not in system_message


def test_system_prompt_includes_a_precomputed_upcoming_days_table() -> None:
    # A bare weekday name ("Sunday") must never be date-arithmetic the
    # model does itself -- a real live request once resolved "Sunday" to
    # today's own date (a Thursday) instead of the actual upcoming
    # Sunday. Fixed by handing the model a precomputed lookup table for
    # the next 7 days so resolving a weekday name is a lookup, not a
    # calculation.
    client = ScriptedChatClient([final_response("...")])
    handle_scheduling_request("hi", client, today=TODAY, events=load_events())
    system_message = client.calls[0]["messages"][0]["content"]

    assert "2025-06-17" in system_message  # TODAY itself (Tuesday)
    for offset in range(1, 7):
        expected_date = date(2025, 6, 17 + offset)
        assert expected_date.isoformat() in system_message


def test_ambiguous_weekday_reminder_is_injected_when_request_names_todays_weekday() -> None:
    # TODAY is itself a Tuesday. A live run showed the model skipping the
    # day-ambiguity question 3 of 4 times for exactly this request shape,
    # jumping straight into resolving whatever conflict "today" produced
    # instead of asking first (see the ambiguous-weekday test below and
    # CLAUDE.md's own note on this). This test only proves the Python-side
    # trigger fires correctly -- it can't prove the live model then obeys
    # it, since that needs a real API call this offline test doesn't make.
    client = ScriptedChatClient([final_response("Did you mean today or next Tuesday?")])
    handle_scheduling_request(
        "Schedule a meeting with Grace on Tuesday from 11:30 to 12:00.",
        client,
        today=TODAY,
        events=load_events(),
    )
    messages = client.calls[0]["messages"]
    reminder_texts = [m["content"] for m in messages if m["role"] == "system"]
    assert any("ambiguity itself must be settled before any tool runs" in text for text in reminder_texts)


@pytest.mark.parametrize(
    "request_text",
    [
        "Schedule a meeting with Grace next Tuesday from 11:30 to 12:00.",
        "Schedule a meeting with Grace on Tuesday, June 24 from 11:30 to 12:00.",
        "Schedule a meeting with Grace on 2025-06-24 from 11:30 to 12:00.",
        "Schedule a meeting with Grace on Thursday from 11:30 to 12:00.",
    ],
)
def test_ambiguous_weekday_reminder_is_not_injected_when_already_resolved(request_text: str) -> None:
    # Each of these already resolves the day unambiguously (an explicit
    # "next", an explicit date, or a weekday that isn't today's own) --
    # the reminder must not fire and crowd the model with an irrelevant
    # instruction for a request that was never actually ambiguous.
    client = ScriptedChatClient([final_response("...")])
    handle_scheduling_request(request_text, client, today=TODAY, events=load_events())
    messages = client.calls[0]["messages"]
    reminder_texts = [m["content"] for m in messages if m["role"] == "system"]
    assert not any("ambiguity itself must be settled before any tool runs" in text for text in reminder_texts)


def test_system_prompt_includes_current_time_defaulting_to_midnight() -> None:
    # A real "schedule for 10am today" request once got booked at 10am even
    # though it was already 3pm -- handle_scheduling_request never told the
    # model what time it currently was, only what day. Without an explicit
    # `now`, the default must be midnight so no mocked test's same-day
    # booking (all of which predate this fix) starts looking "already past".
    client = ScriptedChatClient([final_response("...")])
    handle_scheduling_request("hi", client, today=TODAY, events=load_events())
    assert "00:00" in client.calls[0]["messages"][0]["content"]


def test_system_prompt_includes_explicit_now_when_given() -> None:
    client = ScriptedChatClient([final_response("...")])
    handle_scheduling_request(
        "hi", client, today=TODAY, events=load_events(), now=datetime.combine(TODAY, time(15, 6))
    )
    assert "15:06" in client.calls[0]["messages"][0]["content"]


def test_ambiguous_request_asks_instead_of_booking() -> None:
    client = ScriptedChatClient(
        [final_response("Which day and time works, and who should I invite?")]
    )
    booked: list[dict] = []
    result = handle_scheduling_request(
        "Schedule a meeting sometime this week.", client, today=TODAY, events=load_events(), booked=booked
    )

    assert "?" in result
    assert booked == []
    assert len(client.calls) == 1


def test_conflict_blocks_booking_and_proposes_alternatives() -> None:
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1",
                "check_availability",
                {"attendees": ["Grace"], "date": "2025-06-17", "start": "11:30", "end": "12:00"},
            ),
            tool_call_response(
                "call_2",
                "propose_alternative_slots",
                {"attendees": ["Grace"], "start_date": "2025-06-17", "duration_minutes": 30},
            ),
            final_response(
                "That conflicts with your existing Client Sync. How about 13:00 instead?"
            ),
        ]
    )
    booked: list[dict] = []
    result = handle_scheduling_request(
        "Schedule a meeting with Grace on Tuesday from 11:30 to 12:00.",
        client,
        today=TODAY,
        events=load_events(),
        booked=booked,
    )

    assert booked == []
    assert "client sync" in result.lower()

    def _last_tool_result(messages: list) -> dict:
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        return json.loads(tool_messages[-1]["content"])

    availability_result = _last_tool_result(client.calls[1]["messages"])
    assert availability_result["available"] is False

    alternatives_result = _last_tool_result(client.calls[2]["messages"])
    assert len(alternatives_result["slots"]) > 0


def test_multiple_tool_calls_in_one_round_are_all_executed() -> None:
    client = ScriptedChatClient(
        [
            tool_calls_response(
                [
                    (
                        "call_1",
                        "check_availability",
                        {"attendees": ["Bob"], "date": "2025-06-19", "start": "09:00", "end": "09:30"},
                    ),
                    (
                        "call_2",
                        "check_availability",
                        {"attendees": ["Carol"], "date": "2025-06-19", "start": "09:00", "end": "09:30"},
                    ),
                ]
            ),
            final_response("Bob is busy then, but Carol is free."),
        ]
    )
    booked: list[dict] = []
    result = handle_scheduling_request(
        "Can Bob and Carol both meet Thursday at 9am?",
        client,
        today=TODAY,
        events=load_events(),
        booked=booked,
    )

    assert result == "Bob is busy then, but Carol is free."
    tool_messages = [
        m for m in client.calls[1]["messages"] if isinstance(m, dict) and m.get("role") == "tool"
    ]
    assert len(tool_messages) == 2


def test_find_meeting_to_cancel_returns_all_matches_for_disambiguation() -> None:
    # Deliberately does NOT filter by ownership -- "Client Sync" (Alice's
    # own) and "Bob's Weekly Sync" (Bob's, Alice just attends) both match
    # title_hint "sync" on this day. Returning both lets the agent ask
    # which one instead of the tool silently hiding the one Alice
    # couldn't cancel, which would be a dishonest "not found" instead.
    events = load_events()
    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "find_meeting_to_cancel", {"date": "2025-06-17", "title_hint": "sync"}),
            final_response("Which one -- Client Sync or Bob's Weekly Sync?"),
        ]
    )
    handle_scheduling_request("Cancel my sync meeting today.", client, today=TODAY, events=events)
    # The reminder injected right after find_meeting_to_cancel (to stop a
    # different bug -- reusing a looked-up meeting's attendees for a new
    # booking) adds a system message after the tool result, so it's no
    # longer necessarily the very last message -- find it by role instead.
    tool_messages = [m for m in client.calls[1]["messages"] if isinstance(m, dict) and m.get("role") == "tool"]
    tool_result = json.loads(tool_messages[-1]["content"])
    titles = {m["title"] for m in tool_result["matches"]}
    assert titles == {"Client Sync", "Bob's Weekly Sync"}


def test_cancel_single_instance_of_a_recurring_meeting() -> None:
    events = load_events()
    standup_today = next(e for e in events if e.title == "Team Standup" and e.date == TODAY)
    other_occurrences = [e for e in events if e.series_id == "series_team_standup" and e.date != TODAY]
    assert other_occurrences  # sanity: the fixture really has other occurrences

    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1", "find_meeting_to_cancel", {"date": "2025-06-17", "title_hint": "Team Standup"}
            ),
            tool_call_response(
                "call_2", "cancel_event", {"event_id": standup_today.event_id, "scope": "instance"}
            ),
            final_response("Cancelled today's Team Standup."),
        ]
    )
    cancelled: list[dict] = []
    result = handle_scheduling_request(
        "Cancel today's Team Standup, I confirm.",
        client,
        today=TODAY,
        events=events,
        cancelled=cancelled,
    )

    assert result == "Cancelled today's Team Standup."
    assert len(cancelled) == 1
    assert cancelled[0]["event_id"] == standup_today.event_id
    assert cancelled[0]["scope"] == "instance"
    # The rest of the series must be untouched -- an "instance" cancel
    # never affects past or future occurrences.
    assert {c["event_id"] for c in cancelled}.isdisjoint({e.event_id for e in other_occurrences})


def test_cancel_whole_recurring_series() -> None:
    events = load_events()
    standup_today = next(e for e in events if e.title == "Team Standup" and e.date == TODAY)
    series_occurrences = [e for e in events if e.series_id == "series_team_standup"]
    assert len(series_occurrences) == 3  # sanity check on the fixture: past, today, future

    client = ScriptedChatClient(
        [
            tool_call_response("call_1", "find_meeting_to_cancel", {"title_hint": "Team Standup"}),
            tool_call_response(
                "call_2",
                "cancel_event",
                {"event_id": standup_today.event_id, "scope": "series", "series_id": "series_team_standup"},
            ),
            final_response("Cancelled the entire Team Standup series."),
        ]
    )
    cancelled: list[dict] = []
    handle_scheduling_request(
        "Cancel the whole Team Standup recurring series, I confirm.",
        client,
        today=TODAY,
        events=events,
        cancelled=cancelled,
    )

    assert len(cancelled) == 3
    assert {c["event_id"] for c in cancelled} == {e.event_id for e in series_occurrences}


def test_cancel_refused_when_user_is_not_the_owner() -> None:
    # Alice is only an attendee on "Bob's Weekly Sync", not its owner --
    # she can't cancel it, the same way you can't cancel someone else's
    # meeting in real life just by deciding to. Enforced in _dispatch_tool
    # itself, not left to the prompt, so this holds regardless of what the
    # model decides to do.
    events = load_events()
    bobs_sync = next(e for e in events if e.title == "Bob's Weekly Sync" and e.date == TODAY)
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1", "find_meeting_to_cancel", {"date": "2025-06-17", "title_hint": "Bob's Weekly Sync"}
            ),
            tool_call_response("call_2", "cancel_event", {"event_id": bobs_sync.event_id, "scope": "instance"}),
            final_response("You're not the owner of that meeting, so I can't cancel it."),
        ]
    )
    cancelled: list[dict] = []
    handle_scheduling_request(
        "Cancel Bob's Weekly Sync today, I confirm.",
        client,
        today=TODAY,
        events=events,
        cancelled=cancelled,
    )

    assert cancelled == []
    tool_result = json.loads(client.calls[2]["messages"][-1]["content"])
    assert "error" in tool_result


def test_cancel_event_without_a_prior_lookup_is_refused() -> None:
    # Grounding: cancel_event may only target an event_id this same
    # conversation actually saw via find_meeting_to_cancel -- the model
    # can't invent or guess one.
    events = load_events()
    standup_today = next(e for e in events if e.title == "Team Standup" and e.date == TODAY)
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1", "cancel_event", {"event_id": standup_today.event_id, "scope": "instance"}
            ),
            final_response("..."),
        ]
    )
    cancelled: list[dict] = []
    handle_scheduling_request(
        "Cancel event_id made-up-value.", client, today=TODAY, events=events, cancelled=cancelled
    )

    assert cancelled == []
    tool_result = json.loads(client.calls[1]["messages"][-1]["content"])
    assert "error" in tool_result


def test_pending_confirmation_lets_a_fresh_call_complete_a_cancellation() -> None:
    # Bridges the real gap live_assistant.py has: two separate stateless
    # CLI invocations can't otherwise ever complete a cancellation,
    # because the second one has no memory of the first one's lookup and
    # would just re-ask forever. `pending_confirmation` simulates the
    # caller (live_assistant.py) having persisted exactly what a PRIOR
    # call found and showed -- this call should be able to go straight
    # to cancel_event without calling find_meeting_to_cancel again.
    events = load_events()
    standup_today = next(e for e in events if e.title == "Team Standup" and e.date == TODAY)
    pending = {
        "event_id": standup_today.event_id,
        "title": "Team Standup",
        "date": "2025-06-17",
        "start": "09:00",
        "end": "09:30",
        "attendees": ["Alice", "Bob", "Carol"],
        "owner": "Alice",
        "series_id": "series_team_standup",
        "is_recurring": True,
    }
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1", "cancel_event", {"event_id": standup_today.event_id, "scope": "instance"}
            ),
            final_response("Cancelled today's Team Standup."),
        ]
    )
    cancelled: list[dict] = []
    result = handle_scheduling_request(
        "Yes, cancel it -- just today's occurrence.",
        client,
        today=TODAY,
        events=events,
        cancelled=cancelled,
        pending_confirmation=pending,
    )

    assert result == "Cancelled today's Team Standup."
    assert len(cancelled) == 1
    assert cancelled[0]["event_id"] == standup_today.event_id


def test_cancel_event_fn_override_is_used_instead_of_the_mock() -> None:
    # Same seam as create_event_fn: a real integration (Google Calendar)
    # passes its own cancel_event_fn instead of the mocked cancellation
    # log, and its result must flow through unchanged.
    events = load_events()
    standup_today = next(e for e in events if e.title == "Team Standup" and e.date == TODAY)
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1", "find_meeting_to_cancel", {"date": "2025-06-17", "title_hint": "Team Standup"}
            ),
            tool_call_response(
                "call_2", "cancel_event", {"event_id": standup_today.event_id, "scope": "instance"}
            ),
            final_response("Cancelled via the real calendar."),
        ]
    )
    calls_made: list[tuple] = []

    def fake_real_cancel_event(event_id: str, scope: str, series_id: str | None) -> dict:
        calls_made.append((event_id, scope, series_id))
        return {"cancelled": True, "event_id": event_id, "scope": scope}

    cancelled: list[dict] = []  # the mock's own log -- must stay untouched
    result = handle_scheduling_request(
        "Cancel today's Team Standup, I confirm.",
        client,
        today=TODAY,
        events=events,
        cancelled=cancelled,
        cancel_event_fn=fake_real_cancel_event,
    )

    assert result == "Cancelled via the real calendar."
    assert cancelled == []
    assert len(calls_made) == 1
    assert calls_made[0][0] == standup_today.event_id
    assert calls_made[0][1] == "instance"


live = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="requires OPENAI_API_KEY to call the real model",
)


@pytest.fixture
def live_client():
    import openai

    return openai.OpenAI().chat.completions


def _run(request: str, live_client, now: datetime | None = None) -> tuple[str, list[dict]]:
    booked: list[dict] = []
    result = handle_scheduling_request(
        request, live_client, today=TODAY, now=now, events=load_events(), booked=booked
    )
    return result, booked


def _run_cancel(request: str, live_client) -> tuple[str, list[dict]]:
    cancelled: list[dict] = []
    result = handle_scheduling_request(
        request, live_client, today=TODAY, events=load_events(), cancelled=cancelled
    )
    return result, cancelled


@live
def test_cancel_asks_for_confirmation_before_cancelling(live_client) -> None:
    result, cancelled = _run_cancel("Cancel my Client Sync meeting today.", live_client)
    assert cancelled == []
    lowered = result.lower()
    assert "client sync" in lowered
    assert "?" in result or "confirm" in lowered


@live
def test_cancel_never_skips_confirmation_even_when_pre_confirmed(live_client) -> None:
    # Unlike booking, cancellation confirmation is never assumed from the
    # request's own wording alone -- even an upfront "I confirm, cancel it
    # now" must still be met with the exact meeting details and a genuine
    # confirmation step before anything is actually cancelled.
    result, cancelled = _run_cancel(
        "Cancel my Client Sync meeting today. I confirm, please cancel it now.", live_client
    )
    assert cancelled == []
    assert "client sync" in result.lower()


@live
def test_cancel_refused_for_a_meeting_the_user_does_not_own(live_client) -> None:
    result, cancelled = _run_cancel("Cancel Bob's Weekly Sync meeting today.", live_client)
    assert cancelled == []
    lowered = result.lower()
    assert "bob" in lowered
    assert any(word in lowered for word in ("owner", "own", "can't cancel", "cannot cancel"))


@live
def test_cancel_ambiguous_match_asks_which_one(live_client) -> None:
    result, cancelled = _run_cancel("Cancel my sync meeting today.", live_client)
    assert cancelled == []
    lowered = result.lower()
    assert "client sync" in lowered and "bob" in lowered


@live
def test_t3_straightforward_booking(live_client) -> None:
    result, booked = _run("Schedule a 30-minute meeting with Bob this Thursday at 10am.", live_client)
    assert len(booked) == 1
    assert "Bob" in booked[0]["attendees"]
    lowered = result.lower()
    assert "bob" in lowered
    assert "meet.google.com" in lowered or "meet link" in lowered or booked[0]["meet_link"].split("/")[-1] in result


@live
def test_t4_ambiguous_request_asks_clarifying_questions(live_client) -> None:
    result, booked = _run("Schedule a meeting sometime this week.", live_client)
    assert booked == []
    lowered = result.lower()
    # The agent must ask for the missing day, time, and attendee(s) --
    # whether phrased as a question mark or a numbered list of what's needed.
    assert any(word in lowered for word in ("day", "date", "when"))
    assert any(word in lowered for word in ("time",))
    assert any(word in lowered for word in ("who", "attendee", "invite", "person", "meet with"))


@live
def test_t5_multi_attendee_availability_proposes_multiple_slots(live_client) -> None:
    result, booked = _run(
        "Schedule a 30-minute meeting with Bob, Carol, and Dana sometime Thursday. "
        "Please suggest a few time options rather than picking one.",
        live_client,
    )
    assert booked == []
    time_mentions = re.findall(r"\d{1,2}:\d{2}", result)
    assert len(set(time_mentions)) >= 2


class _ToolCallSpy:
    """Wraps a real client and records every tool call's name and parsed
    arguments, so a test can verify WHICH attendees an availability check
    actually included -- T5 above only checks that multiple times came
    back, which could happen even if an attendee were silently dropped
    from the check itself."""

    def __init__(self, real_client) -> None:
        self._real = real_client
        self.tool_calls: list[tuple[str, dict]] = []

    def create(self, **kwargs: object):
        response = self._real.create(**kwargs)
        message = response.choices[0].message  # type: ignore[attr-defined]
        for tool_call in getattr(message, "tool_calls", None) or []:
            self.tool_calls.append((tool_call.function.name, json.loads(tool_call.function.arguments)))
        return response


@live
def test_multi_attendee_availability_check_actually_includes_everyone(live_client) -> None:
    # Regression coverage for "must check availability for all
    # participants": inspects the actual check_availability/
    # propose_alternative_slots call arguments the live model made, not
    # just the final text -- multiple time options in the reply alone
    # wouldn't catch one named attendee being silently dropped from the
    # underlying check.
    spy = _ToolCallSpy(live_client)
    result, booked = _run(
        "Schedule a 30-minute meeting with Bob, Carol, and Dana sometime Thursday. "
        "Please suggest a few time options rather than picking one.",
        spy,
    )
    assert booked == []
    availability_calls = [
        args for name, args in spy.tool_calls if name in ("check_availability", "propose_alternative_slots")
    ]
    assert availability_calls, f"expected an availability-checking tool call, got: {spy.tool_calls}"
    for args in availability_calls:
        attendees_lower = {a.lower() for a in args.get("attendees", [])}
        assert "bob" in attendees_lower, f"Bob missing from availability check: {args}"
        assert "carol" in attendees_lower, f"Carol missing from availability check: {args}"
        assert "dana" in attendees_lower, f"Dana missing from availability check: {args}"


@live
def test_t6_unusual_time_is_flagged_before_booking(live_client) -> None:
    result, booked = _run("Schedule a call with Bob this Saturday at 9am for 30 minutes.", live_client)
    assert booked == []
    lowered = result.lower()
    assert "weekend" in lowered or "saturday" in lowered
    assert "?" in result or "confirm" in lowered


@live
def test_t9_missing_attendee_is_asked_for(live_client) -> None:
    result, booked = _run("Schedule a 30-minute meeting on Thursday at 2pm.", live_client)
    assert booked == []
    lowered = result.lower()
    # the hard requirement is never booking with a missing attendee (above);
    # asking for it can be phrased many ways, so accept a question mark as
    # well as the common keywords.
    assert "?" in result or any(
        word in lowered for word in ("who", "attendee", "invite", "with", "specify", "name")
    )


@live
def test_t11_conflict_is_detected_and_alternative_offered(live_client) -> None:
    result, booked = _run(
        "Schedule a meeting with Grace today from 11:30 to 12:00.", live_client
    )
    assert booked == []
    lowered = result.lower()
    assert "client sync" in lowered
    assert re.search(r"\d{1,2}:\d{2}|\d{1,2}\s?(am|pm)", lowered)


@live
def test_5_1_conflict_gets_a_concrete_tradeoff_not_just_detection(live_client) -> None:
    # Epic 5: detection alone ("there's a conflict") isn't enough -- the
    # reply must include a real, grounded trade-off.
    result, booked = _run(
        "Schedule a meeting with Grace today from 11:30 to 12:00.", live_client
    )
    assert booked == []
    lowered = result.lower()
    assert "client sync" in lowered
    has_concrete_slot = re.search(r"\d{1,2}:\d{2}|\d{1,2}\s?(am|pm)", lowered)
    names_a_tradeoff_kind = any(w in lowered for w in ("mov", "shorten", "async", "instead"))
    assert has_concrete_slot or names_a_tradeoff_kind


@live
def test_5_1_can_propose_moving_the_existing_meeting_when_asked(live_client) -> None:
    result, booked = _run(
        "Schedule a meeting with Grace today from 11:30 to 12:00. If that "
        "conflicts with something, look into moving the other meeting "
        "instead of finding me a different time.",
        live_client,
    )
    assert booked == []
    lowered = result.lower()
    assert "client sync" in lowered
    assert "mov" in lowered or "reschedul" in lowered  # "move"/"moving"/"moved"
    # framed as a proposal awaiting the user's choice, not a completed
    # action -- the strong version of this check (never claims to have
    # already moved anything) lives in test_5_1_never_claims_to_have_moved...
    assert any(
        w in lowered
        for w in ("could", "would", "can ", "propose", "suggest", "option", "if", "please", "let me know")
    )


@live
def test_5_1_never_claims_to_have_moved_the_existing_meeting(live_client) -> None:
    # There's no tool that can actually change an existing event, so this
    # asserts the agent never claims to have done so -- a move/shorten is
    # always surfaced as a suggestion, never a fait accompli.
    result, booked = _run(
        "Schedule a meeting with Grace today from 11:30 to 12:00.", live_client
    )
    assert booked == []
    lowered = result.lower()
    assert "i moved" not in lowered
    assert "i've rescheduled" not in lowered
    assert "i rescheduled" not in lowered
    assert "i shortened" not in lowered


@live
def test_ambiguous_weekday_matching_today_prompts_clarification(live_client) -> None:
    # TODAY is itself a Tuesday, so "on Tuesday" is genuinely ambiguous
    # between today and next week -- the agent should ask, not guess.
    result, booked = _run(
        "Schedule a meeting with Grace on Tuesday from 11:30 to 12:00.", live_client
    )
    assert booked == []
    lowered = result.lower()
    # names today's date, whether as "today" or the literal date
    assert "today" in lowered or "2025-06-17" in lowered or "june 17" in lowered
    # ...and distinguishes it from next week's Tuesday, one way or another
    assert "next" in lowered or "week" in lowered or "2025-06-24" in lowered or "june 24" in lowered


@live
def test_same_day_past_time_is_refused_not_booked(live_client) -> None:
    # A real request ("meeting with X at 10am today", asked at 3pm) got
    # booked at the already-elapsed 10am slot, since the model was never
    # told what time it currently was -- only what day "today" was. `now`
    # simulates it being 3pm on TODAY (a Tuesday); 10am that same day has
    # already happened and must be refused, confirmation or not.
    result, booked = _run(
        "Schedule a 30-minute meeting with Bob today at 10am. I confirm this is fine -- please book it now.",
        live_client,
        now=datetime.combine(TODAY, time(15, 0)),
    )
    assert booked == []
    lowered = result.lower()
    assert "already" in lowered or "past" in lowered or "elapsed" in lowered


@live
def test_recurring_request_without_an_end_date_is_asked_for_not_booked(live_client) -> None:
    # An open-ended recurring series (no end date) must never be created
    # -- this is missing information the same way a missing attendee is,
    # not something to default to "forever" or omit silently.
    result, booked = _run(
        "Schedule a recurring 15-minute standup with Bob every weekday at 10am.", live_client
    )
    assert booked == []
    lowered = result.lower()
    assert any(word in lowered for word in ("end date", "until", "how long", "when should", "last day"))


@live
def test_same_time_as_a_recurring_meeting_also_asks_which_day(live_client) -> None:
    # "Same time as my standup" only pins down a TIME -- Team Standup is
    # a recurring series (2025-06-16, -17, -23 in the fixture), so WHICH
    # day the new meeting should be on is genuinely still unresolved too.
    # A real request once got this wrong by silently picking the next
    # upcoming occurrence's date without asking -- the day must be asked
    # for, the same way a missing attendee is, not assumed.
    result, booked = _run("Schedule a meeting at the same time as my Team Standup.", live_client)
    assert booked == []
    lowered = result.lower()
    assert any(word in lowered for word in ("who", "attendee", "invite", "with", "person"))
    assert any(word in lowered for word in ("day", "date", "when", "which"))


@live
def test_same_time_as_existing_meeting_still_asks_for_attendees(live_client) -> None:
    # A real request resolved "the same time as my standup" correctly
    # (by looking the real meeting up) but then silently copied that
    # meeting's own attendees onto the brand-new one, even though the
    # user never said who the new meeting was with -- a "never guess
    # attendee" violation. The new meeting's attendees are unrelated
    # information and must still be asked for. Naming "today" resolves
    # the day, so this is testing the attendee question specifically,
    # not the day-ambiguity handled by the test above.
    result, booked = _run("Schedule a meeting at the same time as my Team Standup today.", live_client)
    assert booked == []
    lowered = result.lower()
    assert any(word in lowered for word in ("who", "attendee", "invite", "with", "person"))


@live
def test_same_time_as_existing_meeting_also_surfaces_the_self_conflict(live_client) -> None:
    # Booking "at the same time" as an existing meeting always creates a
    # real conflict for {{USER_NAME}} personally, even before the new
    # meeting's attendees are known -- Alice is already in Team Standup at
    # that time. The agent must not let the missing-attendee question
    # crowd this out: it should name the conflict, offer real
    # alternatives, AND ask for attendees, all in the same reply. Naming
    # "today" resolves which occurrence, so a specific availability check
    # is actually possible here (unlike the ambiguous-day test above).
    result, booked = _run("Schedule a meeting at the same time as my Team Standup today.", live_client)
    assert booked == []
    lowered = result.lower()
    assert "team standup" in lowered
    assert re.search(r"\d{1,2}:\d{2}", result)  # a real alternative time, not just a vague offer
    assert any(word in lowered for word in ("who", "attendee", "invite", "with", "person"))


@live
def test_named_timezone_is_never_converted(live_client) -> None:
    # A real booking once mislanded because the model "helpfully" converted
    # "3pm IST" to "9:30am UTC" and passed that as the wall-clock time --
    # but every tool here already treats the given hour as the calendar's
    # own local time, so any such conversion silently corrupts the booking.
    # A timezone name in the request must be treated as informational only.
    result, booked = _run(
        "Schedule a 30-minute meeting with Bob this Thursday at 3pm IST. "
        "I confirm this is fine -- please book it now.",
        live_client,
    )
    assert len(booked) == 1
    assert booked[0]["start"] == "15:00"
    assert "9:30" not in result
    assert "utc" not in result.lower()


@live
def test_weekend_time_with_confirmation_already_given_books_immediately(live_client) -> None:
    # Same weekend/unusual-hour case as test_t6, but the user's own message
    # already answers the "do you want to keep it there?" question -- the
    # agent must not ask it again (or re-ask for details already stated in
    # the same message) and should check availability and book in one pass.
    result, booked = _run(
        "Schedule a 30-minute meeting with Bob this Saturday at 9am. "
        "I confirm this weekend time is intentional -- please book it now.",
        live_client,
    )
    assert len(booked) == 1
    assert "Bob" in booked[0]["attendees"]


@live
def test_missing_title_is_auto_generated_not_asked_for(live_client) -> None:
    # Title is deliberately excluded from the "ask for missing info" rule
    # -- unlike day/time/duration/attendee, the agent should silently
    # generate a neutral one (e.g. "Meeting with Bob") and proceed to
    # book, rather than pausing to ask what to call it.
    result, booked = _run(
        "Schedule a 30-minute meeting with Bob this Thursday at 10am.", live_client
    )
    assert len(booked) == 1
    assert booked[0]["title"]
    assert "what" not in result.lower() or "title" not in result.lower()


@live
def test_explicit_date_resolves_without_asking_which_day(live_client) -> None:
    # An explicit calendar date is never ambiguous, even though it also
    # happens to fall on today's weekday -- the agent should go straight
    # to checking availability and surface the real conflict.
    result, booked = _run(
        "Schedule a meeting with Grace on June 17, 2025 from 11:30 to 12:00.", live_client
    )
    assert booked == []
    assert "client sync" in result.lower()
