"""The Scheduler Agent: turns a request into a booked meeting.

Like the Briefing Agent, this only trusts tool results, never its own
guesses about the calendar. Unlike the Briefing Agent it may need
several tool round-trips (check availability, then propose alternatives
or create the event) before it has a final answer for the user, or it
may skip tools entirely and just ask a clarifying question.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Protocol

from calendarmate.observability import generation_name_kwargs, trace_agent_call, trace_tool_call
from calendarmate.tools.calendar_tool import Event, load_events
from calendarmate.tools.scheduling_tool import (
    cancel_event,
    check_availability,
    create_event,
    find_meetings_to_cancel,
    propose_alternative_slots,
)

# Signature real integrations (e.g. Google Calendar) must match to be a
# drop-in replacement for the mocked `create_event`'s (booked, ...) form.
# The 6th positional argument is `recurrence` (RRULE strings, or
# None/omitted for a one-off meeting).
CreateEventFn = Callable[..., dict]

# (event_id, scope, series_id) -> dict. `series_id` is only meaningful
# (and required) when scope is "series" -- it comes straight from the
# `series_id` field find_meeting_to_cancel already returned for that
# meeting, so a real integration never has to look it up itself.
CancelEventFn = Callable[[str, str, str | None], dict]

_PROMPT_PATH = Path(__file__).parent / "prompts" / "scheduler.md"

_MODEL = "gpt-4o-mini"

_MAX_TOOL_ROUNDS = 5

_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": (
                "Check whether the given attendees are free for an exact "
                "date, start time, and end time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "date": {"type": "string", "description": "ISO date, e.g. 2025-06-17"},
                    "start": {"type": "string", "description": "24h time, e.g. 14:00"},
                    "end": {"type": "string", "description": "24h time, e.g. 14:30"},
                },
                "required": ["attendees", "date", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_alternative_slots",
            "description": (
                "Find open time slots, within weekday business hours, "
                "where every named attendee is free."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "start_date": {"type": "string", "description": "ISO date to start searching from"},
                    "duration_minutes": {"type": "integer"},
                },
                "required": ["attendees", "start_date", "duration_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": (
                "Book the meeting. Only call after availability is "
                "confirmed and every required detail is known. `date` is "
                "the FIRST occurrence's date, whether or not `recurrence` "
                "is given."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "recurrence": {
                        "type": "object",
                        "description": (
                            "Only include this for a repeating series ('every day', "
                            "'daily standup', 'every weekday') -- omit entirely for a "
                            "one-off meeting. `until` is required: never create an "
                            "open-ended series with no end date."
                        ),
                        "properties": {
                            "frequency": {"type": "string", "enum": ["daily", "weekly"]},
                            "weekdays": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]},
                                "description": "e.g. weekdays only -- omit for a plain daily/weekly series",
                            },
                            "until": {
                                "type": "string",
                                "description": "ISO date of the last day the series should still occur on (inclusive)",
                            },
                        },
                        "required": ["frequency", "until"],
                    },
                },
                "required": ["title", "date", "start", "end", "attendees"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_meeting_to_cancel",
            "description": (
                "Look up existing meeting(s) matching a day and/or a title "
                "hint. Two uses: (1) to figure out exactly which meeting "
                "the user wants cancelled before doing anything else -- "
                "returns each match's owner and, if part of a recurring "
                "series, that series' id, both needed before calling "
                "cancel_event; (2) to resolve 'the same time as my "
                "<meeting>' when booking a NEW meeting, by finding that "
                "existing meeting's real start time instead of guessing one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "ISO date to search on, if the user named a day"},
                    "title_hint": {
                        "type": "string",
                        "description": "Partial, case-insensitive title text, if the user described the meeting",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_event",
            "description": (
                "Cancel a specific meeting. Only call this after the user "
                "has explicitly confirmed, in a message after you showed "
                "them the exact meeting details, that they want it "
                "cancelled -- never on the same turn you first identified it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event_id from find_meeting_to_cancel for the one meeting to cancel",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["instance", "series"],
                        "description": (
                            "'instance' cancels only this one occurrence -- use this for a "
                            "non-recurring meeting, or when the user wants just one occurrence "
                            "of a recurring one. 'series' cancels every occurrence, past and "
                            "future, of a recurring meeting."
                        ),
                    },
                    "series_id": {
                        "type": "string",
                        "description": (
                            "Required when scope is 'series': the series_id find_meeting_to_cancel "
                            "returned for this meeting. Omit for scope 'instance'."
                        ),
                    },
                },
                "required": ["event_id", "scope"],
            },
        },
    },
]


def _upcoming_days_table(today: date) -> str:
    # A bare weekday name ("Sunday") must resolve to a real ISO date --
    # this is exactly the manual date arithmetic LLMs are unreliable at
    # (the same failure class already fixed for weekday *labels* in the
    # Briefing Agent). Handing the model a precomputed lookup table for
    # the next 7 days turns "what date is Sunday" from a calculation the
    # model does itself into a table lookup it can't get wrong.
    lines = []
    for offset in range(7):
        day = today + timedelta(days=offset)
        suffix = " (today)" if offset == 0 else ""
        lines.append(f"{day.strftime('%A')}: {day.isoformat()}{suffix}")
    return "\n".join(lines)


_BUSINESS_START = time(8, 0)
_BUSINESS_END = time(18, 0)

# Mirrors the exact confirming phrases scheduler.md's weekend/unusual-hour
# rule already tells the model count as an inline "yes, keep it there" --
# reused here (rather than invented independently) so the code-level gate
# below and the prompt's own bypass rule agree on what counts as consent,
# instead of drifting into two different definitions of "confirmed."
_OFF_HOURS_CONFIRMATION_PHRASES = (
    "i confirm",
    "yes, keep it there",
    "go ahead and book it",
    "please proceed anyway",
    "book it now",
)


def _is_off_hours(day: date, start: time) -> bool:
    return day.weekday() >= 5 or start < _BUSINESS_START or start >= _BUSINESS_END


def _build_rrule(frequency: str, weekdays: list[str] | None, until: date) -> list[str]:
    # LLMs constructing raw RRULE syntax themselves is exactly the kind
    # of manual computation this project avoids elsewhere (date/weekday
    # arithmetic) -- the model supplies simple structured fields
    # (frequency, weekdays, an end date) and Python builds the actual
    # RRULE string, so a malformed recurrence rule can't reach the API.
    # UNTIL is pushed one full day past the given date and expressed in
    # UTC so the requested last day is included regardless of which real
    # timezone the calendar is in (a same-day UTC cutoff could clip the
    # end of that day in a timezone behind UTC).
    until_utc = datetime.combine(until + timedelta(days=1), time(0, 0)).strftime("%Y%m%dT%H%M%SZ")
    if weekdays:
        return [f"RRULE:FREQ=WEEKLY;BYDAY={','.join(weekdays)};UNTIL={until_utc}"]
    return [f"RRULE:FREQ={frequency.upper()};UNTIL={until_utc}"]


def load_system_prompt(user_name: str = "Alice") -> str:
    # "Alice" is the mocked fixture's persona -- every mock test relies on
    # that default. Real usage (live_assistant.py) passes the actual
    # authenticated account's identity instead of hardcoding a name that
    # would be nonsensical on a real calendar.
    return _PROMPT_PATH.read_text(encoding="utf-8").replace("{{USER_NAME}}", user_name)


class ChatClient(Protocol):
    def create(self, **kwargs: object) -> object: ...


def _dispatch_tool(
    name: str,
    args: dict,
    events: list[Event],
    create_event_fn: CreateEventFn,
    cancel_event_fn: CancelEventFn,
    now: datetime,
    user_name: str,
    looked_up_event_ids: set[str],
    cancellation_candidates: list[dict],
    has_confirmed_unusual_time: bool,
) -> str:
    if name == "check_availability":
        result = trace_tool_call(
            "check_availability",
            args,
            lambda: check_availability(
                events,
                args["attendees"],
                date.fromisoformat(args["date"]),
                time.fromisoformat(args["start"]),
                time.fromisoformat(args["end"]),
            ),
        )
    elif name == "propose_alternative_slots":
        start_date = date.fromisoformat(args["start_date"])
        # If the search starts today, floor it to the current time so a
        # same-day suggestion is never a slot that's already passed --
        # computed here rather than trusting the model to pick a start
        # date/time that's actually still in the future.
        earliest_start = now.time() if start_date == now.date() else None
        result = {
            "slots": propose_alternative_slots(
                events,
                args["attendees"],
                start_date,
                int(args["duration_minutes"]),
                earliest_start=earliest_start,
            )
        }
    elif name == "create_event":

        def _do_create_event() -> dict:
            event_day = date.fromisoformat(args["date"])
            event_start = time.fromisoformat(args["start"])
            if datetime.combine(event_day, event_start) <= now:
                # Belt-and-suspenders: `scheduler.md` already instructs the
                # model to never book an already-passed time, but a real
                # request once got booked there anyway despite that rule and
                # an explicit "never book it anyway" -- a strongly-worded
                # confirmation ("book it now") can still outweigh a prompt
                # rule for the model. This check makes it impossible
                # regardless of what the model decides, the same way
                # attendee-email validation isn't left to the prompt alone.
                return {"error": "That time has already passed and cannot be booked."}
            if _is_off_hours(event_day, event_start) and not has_confirmed_unusual_time:
                # Belt-and-suspenders, same reasoning as the past-time check
                # above: scheduler.md already tells the model to flag a
                # weekend/outside-8:00-18:00 time and wait for confirmation
                # before booking, but a real run showed the model can still
                # call create_event anyway -- e.g. narrating "since you've
                # confirmed the weekend time" when the user never actually
                # did, or getting distracted by an unrelated concern (like a
                # cross-timezone framing) into skipping the flag entirely.
                # `has_confirmed_unusual_time` is computed once, up front, in
                # Python from the request's own wording (the same inline
                # phrases scheduler.md already documents as valid consent),
                # so this doesn't second-guess a *genuine* same-message
                # confirmation -- it only blocks the case where the model
                # proceeds without one actually being there.
                return {
                    "error": (
                        "That time falls on a weekend or outside business hours (8:00-18:00), "
                        "so it needs the user's explicit confirmation before it can be booked. "
                        "Ask them to confirm before trying again."
                    )
                }
            recurrence_arg = args.get("recurrence")
            rrule = None
            if recurrence_arg:
                rrule = _build_rrule(
                    recurrence_arg["frequency"],
                    recurrence_arg.get("weekdays"),
                    date.fromisoformat(recurrence_arg["until"]),
                )
            return create_event_fn(
                args["title"],
                event_day,
                event_start,
                time.fromisoformat(args["end"]),
                args["attendees"],
                rrule,
            )

        result = trace_tool_call("create_event", args, _do_create_event)
    elif name == "find_meeting_to_cancel":
        day = date.fromisoformat(args["date"]) if args.get("date") else None
        matches = find_meetings_to_cancel(events, day=day, title_hint=args.get("title_hint"))
        looked_up_event_ids.update(m["event_id"] for m in matches)
        # Surfaced to the caller (e.g. live_assistant.py) so a stateless
        # CLI invocation can remember "this exact meeting was just shown,
        # awaiting confirmation" across separate process runs -- only
        # when there's exactly one candidate, since anything ambiguous
        # has nothing single to remember yet.
        if len(matches) == 1:
            cancellation_candidates.append(matches[0])
        result = {"matches": matches}
    elif name == "cancel_event":
        # Grounding: cancel_event may only target an event_id this same
        # conversation actually saw via find_meeting_to_cancel -- the
        # model can't hallucinate or guess an id to cancel.
        if args["event_id"] not in looked_up_event_ids:
            result = {"error": "Call find_meeting_to_cancel first and use one of its returned event_id values."}
        else:
            # Ownership isn't left to the prompt alone -- only the meeting's
            # owner can cancel it, the same way an already-passed time can't
            # be booked no matter what the model decides to do. Looked up
            # here against the same `events` list find_meeting_to_cancel used,
            # so this works identically for the mocked fixture and the real
            # Calendar data (both populate `owner` on every Event).
            target = next((e for e in events if e.event_id == args["event_id"]), None)
            if target is None:
                result = {"error": f"No meeting found with event_id {args['event_id']!r}."}
            elif target.owner != user_name:
                result = {"error": f"You're not the owner of '{target.title}', so it can't be cancelled."}
            else:
                result = cancel_event_fn(args["event_id"], args["scope"], args.get("series_id"))
    else:
        result = {"error": f"unknown tool {name}"}
    return json.dumps(result)


def handle_scheduling_request(request: str, client: ChatClient, **kwargs: object) -> str:
    # `user_id` is popped out here rather than forwarded -- it's a
    # Langfuse tagging concern only, not something
    # `_handle_scheduling_request_impl` has (or needs) a parameter for,
    # and it must not be confused with `user_name` below, an unrelated,
    # pre-existing parameter for whose calendar this is. Everything else
    # is forwarded via **kwargs (all of `_handle_scheduling_request_impl`'s
    # parameters below are keyword-only already) rather than repeating
    # the full ten-parameter signature here too -- one source of truth
    # for the defaults, no risk of the two drifting out of sync.
    user_id = kwargs.pop("user_id", None)
    return trace_agent_call(
        "handle-scheduling",
        agent="scheduling",
        request=request,
        user_id=user_id,  # type: ignore[arg-type]
        fn=lambda: _handle_scheduling_request_impl(request, client, **kwargs),
    )


def _handle_scheduling_request_impl(
    request: str,
    client: ChatClient,
    *,
    today: date | None = None,
    now: datetime | None = None,
    events: list[Event] | None = None,
    booked: list[dict] | None = None,
    cancelled: list[dict] | None = None,
    cancellation_candidates: list[dict] | None = None,
    pending_confirmation: dict | None = None,
    create_event_fn: CreateEventFn | None = None,
    cancel_event_fn: CancelEventFn | None = None,
    user_name: str = "Alice",
) -> str:
    # A reminder injected after find_meeting_to_cancel needs to know
    # whether THIS request actually means to cancel something, or is
    # just resolving "the same time as my <meeting>" for a new booking --
    # asking the model to self-condition on "if this was for cancelling,
    # ignore the following" inside the reminder itself didn't hold: it
    # hijacked genuine cancellation requests into treating them as
    # booking-reference lookups instead. Deciding this deterministically
    # from the request's own wording, once, up front, is far more
    # reliable than hoping the model re-derives the same distinction
    # correctly every time it sees the reminder.
    is_cancel_intent = any(word in request.lower() for word in ("cancel", "delete", "remove"))

    # Same "decide it once, deterministically, from the request's own
    # wording" reasoning as is_cancel_intent above, applied to the
    # weekend/unusual-hour confirmation bypass -- see _is_off_hours'
    # call site in _dispatch_tool for why this needs to be code-enforced
    # rather than left to the model alone.
    has_confirmed_unusual_time = any(phrase in request.lower() for phrase in _OFF_HOURS_CONFIRMATION_PHRASES)

    today = today if today is not None else date.today()
    # `now` is only for a same-day "has this time already passed" check --
    # it's deliberately NOT defaulted to the real wall clock, since every
    # mocked test books against a fixed, arbitrary `today` (e.g. June 2025)
    # that's nowhere near the real current moment; defaulting to the real
    # clock would make every one of those bookings look like it's in the
    # past. Callers that care about real elapsed time within today (i.e.
    # live_assistant.py) pass `now=datetime.now()` explicitly; absent that,
    # "now" is treated as the start of `today`, so no same-day time looks
    # like it's already passed.
    now = now if now is not None else datetime.combine(today, time.min)
    events = events if events is not None else load_events()
    booked = booked if booked is not None else []
    cancelled = cancelled if cancelled is not None else []
    cancellation_candidates = cancellation_candidates if cancellation_candidates is not None else []
    if create_event_fn is None:
        # Default: the mocked booking store. A real integration (e.g.
        # Google Calendar) passes its own create_event_fn with the same
        # (title, day, start, end, attendees, recurrence) -> dict shape
        # instead -- existing callers that don't pass one keep this exact
        # behavior.
        def create_event_fn(
            title: str, day: date, start: time, end: time, attendees: list, recurrence: list[str] | None = None
        ) -> dict:
            return create_event(booked, title, day, start, end, attendees, recurrence)
    if cancel_event_fn is None:
        # Default: the mocked cancellation log. A real integration passes
        # its own cancel_event_fn with the same (event_id, scope,
        # series_id) -> dict shape instead.
        def cancel_event_fn(event_id: str, scope: str, series_id: str | None) -> dict:
            return cancel_event(events, cancelled, event_id, scope)

    messages: list[dict[str, object]] = [
        {
            "role": "system",
            "content": (
                f"{load_system_prompt(user_name)}\n\n"
                f"Today's date is {today.isoformat()} ({today.strftime('%A')}).\n\n"
                f"The current time right now is {now.strftime('%H:%M')}"
                + (" (midnight, the very start of today)" if now.time() == time.min else "")
                + ". Any later time on today's date (e.g. 09:00, 11:30, 15:00) has NOT "
                "happened yet -- do not treat a same-day time as already passed unless "
                "it is actually earlier than this current time.\n\n"
                "Dates for the next 7 days -- use this table to resolve any "
                "bare weekday name the user gives you; never compute a "
                "weekday's date yourself:\n"
                f"{_upcoming_days_table(today)}\n"
                "For a weekday further out than this table covers (e.g. "
                "explicitly \"next Friday\" when today already IS a Friday), "
                "add 7 days to the date shown here for that same weekday."
            ),
        },
        {"role": "user", "content": request},
    ]

    reply_parts: list[str] = []
    looked_up_event_ids: set[str] = set()

    if pending_confirmation:
        # Bridges the gap this exact function has no other way to close:
        # it's stateless, called fresh with no memory of any earlier
        # invocation. A real cancellation needs a genuine "show details,
        # then a LATER separate message confirms" exchange -- but without
        # this, the "later separate message" would ALSO be a from-scratch
        # call that re-finds the meeting and, per that rule, still
        # couldn't cancel it in the same reply, forever. The caller (e.g.
        # live_assistant.py) is responsible for persisting exactly what
        # was shown to the user in a PRIOR call and passing it back here
        # once a genuinely new, later message arrives -- this treats that
        # as satisfying "find_meeting_to_cancel already happened, in a
        # real earlier turn" without needing to re-run the lookup.
        looked_up_event_ids.add(pending_confirmation["event_id"])
        messages.append(
            {
                "role": "system",
                "content": (
                    "In a PREVIOUS, separate message, you already found and showed the "
                    "user this exact meeting, awaiting their confirmation to cancel it:\n"
                    f"{json.dumps(pending_confirmation)}\n"
                    "The user's current message is their reply to that. If it clearly "
                    "confirms cancelling this specific meeting, call cancel_event "
                    f"directly with event_id={pending_confirmation['event_id']!r} (and, "
                    "if they want the whole series, scope='series' with the series_id "
                    "shown above) -- you do not need to call find_meeting_to_cancel "
                    "again for this. If their current message is NOT about this "
                    "meeting at all (e.g. an unrelated new request), ignore this note "
                    "entirely and handle their actual message normally."
                ),
            }
        )

    for _ in range(_MAX_TOOL_ROUNDS):
        response = client.create(
            model=_MODEL,
            messages=messages,
            tools=_TOOL_SCHEMAS,
            temperature=0,
            **generation_name_kwargs("handle-scheduling"),
        )
        message = response.choices[0].message  # type: ignore[attr-defined]
        tool_calls = getattr(message, "tool_calls", None)

        # A message can carry both text and a tool call in the same turn
        # (e.g. it states the conflict, then calls propose_alternative_slots)
        # -- keep that text instead of discarding it just because the turn
        # wasn't the final one, otherwise information the model already
        # generated (like naming the conflicting meeting) gets silently lost.
        if message.content:  # type: ignore[union-attr]
            reply_parts.append(message.content.strip())  # type: ignore[union-attr]

        if not tool_calls:
            return "\n\n".join(reply_parts)

        messages.append(message)  # type: ignore[arg-type]
        conflict_detected = False
        looked_up_a_reference_meeting = False
        reference_lookup_had_multiple_matches = False
        for tool_call in tool_calls:
            args = json.loads(tool_call.function.arguments)
            result = _dispatch_tool(
                tool_call.function.name,
                args,
                events,
                create_event_fn,
                cancel_event_fn,
                now,
                user_name,
                looked_up_event_ids,
                cancellation_candidates,
                has_confirmed_unusual_time,
            )
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
            if tool_call.function.name == "check_availability" and not json.loads(result)["available"]:
                conflict_detected = True
            if tool_call.function.name == "find_meeting_to_cancel":
                looked_up_a_reference_meeting = True
                if len(json.loads(result).get("matches", [])) > 1:
                    reference_lookup_had_multiple_matches = True

        if looked_up_a_reference_meeting and not is_cancel_intent:
            # A real request resolved "the same time as my standup" by
            # correctly looking the standup up this way, then silently
            # carried its attendees onto the brand-new meeting anyway --
            # the "don't reuse attendees" rule stated once at the top of
            # this prompt lost to the much more reinforced "act, don't
            # ask" pull. Restating it right here, immediately after the
            # lookup that could trigger the mistake, is the same fix
            # already proven for the conflict-naming rule below.
            #
            # Gated on `is_cancel_intent` (computed once, from the
            # request's own wording, before any tool was ever called) --
            # an earlier version tried to have the model self-condition
            # on "only if this wasn't for cancelling" inside the reminder
            # text itself, and that failed: it hijacked genuine
            # cancellation requests into treating them as booking-lookups
            # instead, since the model didn't reliably ignore instructions
            # meant to be conditional. Deciding intent in Python, once, is
            # the reliable version of the same fix.
            if reference_lookup_had_multiple_matches:
                # More than one occurrence matched, meaning the day is
                # still genuinely unresolved (a lookup correctly scoped
                # to a day the user actually gave -- "on Monday" -- would
                # have returned exactly one match). "Same time as my
                # standup" only pins down a TIME; a recurring reference
                # meeting exists on many days, so a real request once got
                # this wrong by silently picking the next upcoming
                # occurrence's date without asking. Availability can't
                # even be checked yet without a real day to check it on.
                reminder = (
                    "Reminder: find_meeting_to_cancel just matched MULTIPLE occurrences "
                    "of a recurring meeting -- meaning the day for the new meeting you're "
                    "booking is still unresolved, not just its attendees. Do not pick the "
                    "next upcoming occurrence's date for them, and do not call "
                    "check_availability yet (there's no specific day to check). In THIS "
                    "reply, ask which day they want the new meeting on AND who it should "
                    "be with -- UNLESS the user's own message already answered one or "
                    "both of those (a day name, or a real attendee), in which case use "
                    "what they already gave you and only ask for whichever piece is "
                    "still actually missing. It's fine to also mention, as a heads-up, "
                    "that whichever day is used will conflict with that recurring series "
                    "at this time."
                )
            else:
                reminder = (
                    "Reminder: find_meeting_to_cancel was just used to find an existing "
                    "meeting's TIME as a reference for the NEW meeting you're booking. Do "
                    "not reuse that meeting's attendees or title for the new one -- who "
                    "it's with is separate information, not necessarily the same as what "
                    "you just found. But don't let asking about it stop you from "
                    f"surfacing the conflict this always creates: {user_name} is already "
                    f"in the meeting you just found, so {user_name} personally can't also "
                    f"attend a new one at that same time, regardless of who else it's "
                    f"with. In THIS reply: call check_availability for {user_name} alone "
                    "at that day and time (it will conflict, by construction), name that "
                    f"conflict, call propose_alternative_slots for {user_name} alone, AND "
                    "ask who the new meeting should be with -- UNLESS the user's own "
                    "message already named a real attendee for this new meeting, in "
                    "which case use that instead of asking again."
                )
            messages.append({"role": "system", "content": reminder})

        if conflict_detected:
            # A reminder injected right before the next generation is more
            # reliable than relying on the model to recall a rule from the
            # top of a long system prompt -- without this, it would
            # sometimes jump straight to alternatives without ever naming
            # what they conflict with.
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Reminder: check_availability just found a real conflict. "
                        "Your reply must name the specific conflicting meeting(s) "
                        "by title before anything else -- never present "
                        "alternatives without first saying what they conflict with. "
                        "Then actually call propose_alternative_slots (for the new "
                        "meeting's attendees, the existing meeting's attendees, or "
                        "both) and include a real result in this same reply -- do "
                        "not ask permission to look for alternatives first."
                    ),
                }
            )

    raise RuntimeError("Scheduler exceeded its tool-call budget without a final answer")
