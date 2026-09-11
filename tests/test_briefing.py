import json
import os
import re
from datetime import date

import pytest

from calendarmate.briefing import answer_briefing
from calendarmate.tools.calendar_tool import load_events
from fakes import ScriptedChatClient, final_response, tool_call_response

# The fixture's events all fall in the week of Mon 2025-06-16 - Sun 2025-06-22.
# Pin "today" to a day in that week so tests are deterministic regardless of
# when they're actually run.
TODAY = date(2025, 6, 17)  # Tuesday


def test_answer_briefing_executes_the_tool_and_returns_final_text() -> None:
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1", "get_calendar_events", {"start_date": "2025-06-17", "end_date": "2025-06-17"}
            ),
            final_response("Design Review and Client Sync conflict today."),
        ]
    )
    result = answer_briefing("What does my day look like?", client, today=TODAY, events=load_events())

    assert result == "Design Review and Client Sync conflict today."
    assert len(client.calls) == 2

    tool_result_message = client.calls[1]["messages"][-1]
    assert tool_result_message["role"] == "tool"
    payload = json.loads(tool_result_message["content"])
    titles = {e["title"] for e in payload["events"]}
    assert titles == {"Team Standup", "Design Review", "Client Sync", "Lunch with Dana", "Bob's Weekly Sync"}
    assert len(payload["conflicts"]) == 1


def test_answer_briefing_returns_direct_text_when_model_skips_the_tool() -> None:
    client = ScriptedChatClient([final_response("Sure, what would you like to know?")])
    result = answer_briefing("hi", client, today=TODAY, events=load_events())
    assert result == "Sure, what would you like to know?"
    assert len(client.calls) == 1


def test_answer_briefing_tells_the_model_todays_date() -> None:
    client = ScriptedChatClient(
        [
            tool_call_response(
                "call_1", "get_calendar_events", {"start_date": "2025-06-17", "end_date": "2025-06-17"}
            ),
            final_response("..."),
        ]
    )
    answer_briefing("What does my day look like?", client, today=TODAY, events=load_events())
    system_message = client.calls[0]["messages"][0]
    assert "2025-06-17" in system_message["content"]


live = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="requires OPENAI_API_KEY to call the real model",
)


@pytest.fixture
def live_client():
    import openai

    return openai.OpenAI().chat.completions


@live
def test_t1_daily_briefing_lists_meetings_and_flags_conflict(live_client) -> None:
    result = answer_briefing("What does my day look like?", live_client, today=TODAY, events=load_events())
    lowered = result.lower()
    assert "design review" in lowered
    assert "client sync" in lowered
    assert "conflict" in lowered or "overlap" in lowered


@live
def test_t2_tomorrow_briefing_lists_only_real_events(live_client) -> None:
    result = answer_briefing(
        "What does tomorrow look like?", live_client, today=TODAY, events=load_events()
    )
    lowered = result.lower()
    assert "marketing sync" in lowered
    assert "board prep" in lowered
    assert "design review" not in lowered  # that's today's event, not tomorrow's
    assert "made up meeting" not in lowered


@live
def test_t10_week_conflict_check_finds_both_conflicts(live_client) -> None:
    result = answer_briefing(
        "Do I have any conflicts this week?", live_client, today=TODAY, events=load_events()
    )
    lowered = result.lower()
    assert "design review" in lowered or "client sync" in lowered
    assert "sprint retro" in lowered or "happy hour" in lowered


@live
def test_t12_specific_day_shows_only_that_day(live_client) -> None:
    result = answer_briefing(
        "What's happening on Friday, June 20th?", live_client, today=TODAY, events=load_events()
    )
    lowered = result.lower()
    assert "sprint retro" in lowered
    assert "happy hour" in lowered
    assert "1:1 with bob" not in lowered  # that's Thursday's event


@live
def test_ambiguous_weekday_matching_today_prompts_clarification(live_client) -> None:
    # TODAY is itself a Tuesday, so "Tuesday" is genuinely ambiguous
    # between today and next week -- the agent should ask, not guess.
    result = answer_briefing("What does Tuesday look like?", live_client, today=TODAY, events=load_events())
    lowered = result.lower()
    # names today's date, whether as "today" or the literal date
    assert "today" in lowered or "2025-06-17" in lowered or "june 17" in lowered
    # ...and distinguishes it from next week's Tuesday, one way or another
    assert "next" in lowered or "week" in lowered or "2025-06-24" in lowered or "june 24" in lowered
    assert "design review" not in lowered  # shouldn't have queried yet


@live
def test_explicit_date_resolves_without_asking_which_day(live_client) -> None:
    # An explicit calendar date is never ambiguous, even though it also
    # happens to fall on today's weekday.
    result = answer_briefing(
        "What's happening on June 17, 2025?", live_client, today=TODAY, events=load_events()
    )
    lowered = result.lower()
    assert "design review" in lowered
    assert "client sync" in lowered


@live
def test_last_week_means_the_previous_completed_week_not_this_week(live_client) -> None:
    # TODAY is Tuesday 2025-06-17, inside the Mon 2025-06-16 - Sun 2025-06-22
    # fixture week. "Last week" (Mon 2025-06-09 - Sun 2025-06-15) has zero
    # fixture events -- a broken agent would report "this week" instead
    # (which does have events) and wrongly call them "last week's."
    result = answer_briefing("What meetings did I have last week?", live_client, today=TODAY, events=load_events())
    lowered = result.lower()
    assert any(phrase in lowered for phrase in ("no meetings", "no events", "didn't have", "nothing"))
    # none of *this* week's real events should leak in as if they were last week's
    assert "design review" not in lowered
    assert "client sync" not in lowered
    assert "team standup" not in lowered
    assert "sprint retro" not in lowered


@live
def test_weekday_labels_use_the_tools_computed_value_not_a_guess(live_client) -> None:
    # A real request once got "Saturday, September 6, 2026" for a date
    # that's actually a Sunday -- LLMs are unreliable at manual weekday
    # arithmetic. June 20, 2025 (Sprint Retro / Happy Hour Planning) is
    # a Friday; if the agent labels it with any other day, it computed
    # the weekday itself instead of using the tool's `weekday` field.
    result = answer_briefing(
        "What's happening the week of June 16-22, 2025? Label each day with its day of the week.",
        live_client,
        today=TODAY,
        events=load_events(),
    )
    lowered = result.lower()
    assert "sprint retro" in lowered
    # Split into per-day sections at each "### <weekday>" heading (or a
    # bold/plain fallback) and find the one Sprint Retro actually falls
    # under -- a naive fixed-size text window around the phrase would
    # wrongly pick up a *neighboring* day's heading in a day-by-day list.
    day_names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    headings = [(m.start(), day) for day in day_names for m in re.finditer(rf"\b{day}\b", lowered)]
    headings.sort()
    sprint_retro_idx = lowered.index("sprint retro")
    section_day = None
    for pos, day in headings:
        if pos <= sprint_retro_idx:
            section_day = day
        else:
            break
    assert section_day == "friday", f"Sprint Retro (June 20, a real Friday) was placed under {section_day!r}"
