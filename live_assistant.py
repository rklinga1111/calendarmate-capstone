"""Runs CalendarMate against your REAL Google Calendar and Gmail.

Deliberately separate from calendarmate.pipeline.run_orchestrator, which
the pytest suite and harness.py use and which always runs against the
mocked fixtures (calendar.json / inbox.json / meetings.json) -- running
tests or the eval harness never touches this file or your real account.

Setup (once):
    1. python google_auth_setup.py   -- see that file for what it needs
    2. pip install -e ".[dev,google]"   -- langfuse is a core dependency now
    3. Make sure OPENAI_API_KEY is set (.env or the environment)
    4. Make sure LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and
       LANGFUSE_BASE_URL are set (.env or the environment) -- every real
       request is traced to Langfuse (see the "Observability" section
       in CLAUDE.md for what's captured and why).

Usage:
    python live_assistant.py "What does my day look like?"
    python live_assistant.py "Schedule a 30-minute meeting with jane@example.com tomorrow at 2pm"

Real-account notes:
    - Attendees on your actual calendar are real email addresses, not
      first names like the mocked fixture's "Bob"/"Carol" -- phrase
      scheduling/follow-up requests with real email addresses or exact
      event titles.
    - create_event creates a REAL event (with a real Google Meet link)
      on your REAL calendar, and can send a real invite to whoever you
      name. send_followup_email sends a REAL email from your REAL
      account. Nothing here asks for confirmation before doing either --
      that's on you to decide when running this, the same way you'd
      think before sending any other email or calendar invite yourself.
      cancel_event is the one exception: scheduler.md always requires an
      explicit confirmation of the exact meeting (shown back to you)
      before it's called, even if your request already said "cancel it" --
      that always takes (at least) two separate commands: one that finds
      and shows the meeting, then a later one where you actually confirm
      (a small local `.pending_cancellation.json` file, gitignored,
      remembers which meeting was shown across those two commands, since
      each `python live_assistant.py ...` run has no memory of the last).
    - Only the owner (organizer) of a meeting can cancel it -- if you're
      just an invited attendee on someone else's meeting, cancellation
      is refused rather than silently doing nothing (or removing it from
      everyone else's calendar, which you have no right to do).
    - "Unread mail" requests only see the last 30 days (see
      _EMAIL_WINDOW_PAST_DAYS) -- an older unread email won't show up in
      an unread-only query, though it would if you asked about the
      specific date range it falls in.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Langfuse must be imported AFTER load_dotenv() (so LANGFUSE_PUBLIC_KEY /
# LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL are already in the environment
# when it initializes) and `langfuse.openai`'s OpenAI class must be used
# in place of the plain SDK's -- it's a drop-in replacement (same
# .chat.completions.create() shape every agent already expects via the
# ChatClient protocol), but it's also the thing that actually captures
# each call as a trace. Getting either of these two orderings backwards
# is a documented Langfuse gotcha: it silently traces nothing rather
# than raising an error, so there'd be no obvious signal anything was
# ever wrong.
from langfuse import get_client, propagate_attributes
from langfuse.openai import OpenAI

from calendarmate.briefing import answer_briefing
from calendarmate.digest import answer_digest_request
from calendarmate.email import answer_email_request
from calendarmate.followup import run_followup_agent
from calendarmate.integrations.gmail import load_emails_in_range_from_gmail, send_email_via_gmail
from calendarmate.integrations.google_calendar import (
    cancel_event_via_google_calendar,
    create_event_via_google_calendar,
    get_authenticated_user_email,
    get_meeting_record_from_google_calendar,
    load_events_from_google_calendar,
)
from calendarmate.orchestrator import CHITCHAT_REPLIES, chitchat_precheck, route_request
from calendarmate.pipeline import ChatClient, FALLBACK_MESSAGE
from calendarmate.scheduler import handle_scheduling_request

# How far back/forward to pull real events for briefing and scheduling
# requests. Wide enough to cover "today", "this week", and near-term
# scheduling without pulling someone's entire calendar history.
_WINDOW_PAST_DAYS = 7
_WINDOW_FUTURE_DAYS = 60

# How far back to pull real email for "last week"/"this week"-style
# requests. The email tool decides unread-only vs. date-range filtering
# itself (see email.py) -- this just needs to pre-fetch a real dataset
# broad enough to cover either, since (unlike the tool's arguments) this
# fetch happens before the model has said what it wants.
_EMAIL_WINDOW_PAST_DAYS = 30

# Bridges a real gap: `handle_scheduling_request` is a stateless function
# and this CLI has no session between invocations, but "always confirm
# before cancelling" (scheduler.md) means a cancellation can never
# complete in the same call that first found the meeting -- it always
# needs a genuinely later, separate message. This file persists exactly
# what was shown to the user across that gap, so the NEXT invocation can
# recognize "this is the user's reply to that" instead of starting over
# from scratch and asking again forever. Gitignored -- it's local,
# throwaway session state, not something to commit or share.
_PENDING_CANCELLATION_PATH = Path(__file__).parent / ".pending_cancellation.json"


def _load_pending_cancellation() -> dict | None:
    if not _PENDING_CANCELLATION_PATH.exists():
        return None
    try:
        return json.loads(_PENDING_CANCELLATION_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_pending_cancellation(candidate: dict) -> None:
    _PENDING_CANCELLATION_PATH.write_text(json.dumps(candidate), encoding="utf-8")


def _clear_pending_cancellation() -> None:
    _PENDING_CANCELLATION_PATH.unlink(missing_ok=True)


def _calendar_window() -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=_WINDOW_PAST_DAYS), today + timedelta(days=_WINDOW_FUTURE_DAYS)


def _briefing(request: str, client: ChatClient, *, user_id: str | None = None) -> str:
    start, end = _calendar_window()
    return answer_briefing(
        request, client, events=load_events_from_google_calendar(start, end), user_id=user_id
    )


def _scheduling(request: str, client: ChatClient, *, user_id: str | None = None) -> str:
    start, end = _calendar_window()
    cancelled: list[dict] = []
    cancellation_candidates: list[dict] = []
    result = handle_scheduling_request(
        request,
        client,
        now=datetime.now(),
        events=load_events_from_google_calendar(start, end),
        cancelled=cancelled,
        cancellation_candidates=cancellation_candidates,
        pending_confirmation=_load_pending_cancellation(),
        create_event_fn=create_event_via_google_calendar,
        cancel_event_fn=cancel_event_via_google_calendar,
        user_name=get_authenticated_user_email(),
        user_id=user_id,
    )
    if cancelled:
        # A cancellation actually completed this call -- nothing left
        # pending, whatever it was.
        _clear_pending_cancellation()
    elif len(cancellation_candidates) == 1:
        # Exactly one meeting was found and shown, awaiting confirmation
        # -- remember it so a later, separate confirming message can
        # actually act on it instead of starting the lookup over.
        _save_pending_cancellation(cancellation_candidates[0])
    else:
        # Anything else (ambiguous multiple matches, zero matches, an
        # ownership refusal, or a request unrelated to cancellation
        # entirely) leaves nothing single and pending to remember.
        _clear_pending_cancellation()
    return result


def _email(request: str, client: ChatClient, *, user_id: str | None = None) -> str:
    today = date.today()
    emails = load_emails_in_range_from_gmail(today - timedelta(days=_EMAIL_WINDOW_PAST_DAYS), today)
    return answer_email_request(
        request, client, today=today, emails=emails, own_email=get_authenticated_user_email(), user_id=user_id
    )


def _follow_up(request: str, client: ChatClient, *, user_id: str | None = None) -> str:
    return run_followup_agent(
        request,
        client,
        get_meeting_fn=get_meeting_record_from_google_calendar,
        send_email_fn=send_email_via_gmail,
        user_name=get_authenticated_user_email(),
        user_id=user_id,
    )


def _digest(request: str, client: ChatClient, *, user_id: str | None = None) -> str:
    today = date.today()
    cal_start, cal_end = _calendar_window()
    events = load_events_from_google_calendar(cal_start, cal_end)
    emails = load_emails_in_range_from_gmail(today - timedelta(days=_EMAIL_WINDOW_PAST_DAYS), today)
    return answer_digest_request(
        request,
        client,
        today=today,
        events=events,
        emails=emails,
        own_email=get_authenticated_user_email(),
        user_id=user_id,
    )


_LIVE_DISPATCH = {
    "briefing": _briefing,
    "scheduling": _scheduling,
    "email": _email,
    "follow_up": _follow_up,
    "digest": _digest,
}


def handle_request(request: str) -> str:
    """Runs one request through the real, live-account pipeline --
    classification, then whichever agent's own tool loop -- and returns
    the final answer as a string. This is the reusable core behind both
    the CLI (`main`, below) and any other real caller (e.g.
    demo_server.py) that needs the same real-account behavior without
    re-implementing it."""
    client = OpenAI().chat.completions
    langfuse = get_client()
    # The real authenticated Google account email -- computed once here
    # and passed explicitly into route_request/_LIVE_DISPATCH below,
    # rather than only relying on the ambient propagate_attributes
    # wrapping further down. Both matter: the ambient wrapping is what
    # tags the ROOT span itself (a nested call's own propagate_attributes
    # can't retroactively reach a span created before it), while the
    # explicit parameter is what guarantees route_request/each agent's
    # OWN trace_agent_call wrapper still tags user_id correctly even if
    # some future caller (e.g. a voice_wrapper.py entry point) forgets to
    # set up an equivalent outer wrapping of its own.
    user_id = get_authenticated_user_email()

    # One root span per call, so every LLM call made while answering this
    # one request -- the Orchestrator's classification, then whichever
    # agent's own (possibly multi-round) tool loop -- nests under a single
    # trace instead of each becoming its own disconnected top-level trace.
    # Named verb-first ("handle-request") per Langfuse's own naming
    # convention (active language, no dynamic values -- names are a
    # stable API that dashboards/evaluators key on). user_id and
    # environment are propagated from the very start (per Langfuse's own
    # guidance: propagate_attributes as early as possible, since spans
    # created before it don't get it retroactively) -- `environment` is
    # always "production" here since live_assistant.py is this app's one
    # real-account entry point (pytest/harness.py never touch it, so
    # there's no separate staging/dev path to distinguish). `feature` is
    # added as a tag once the category is known, matching this app's
    # several distinct routes (briefing/scheduling/email/follow_up/
    # digest) to Langfuse's documented "feature tag" pattern for
    # per-route filtering.
    with langfuse.start_as_current_observation(
        as_type="span", name="handle-request", input=request
    ) as root_span:
        with propagate_attributes(user_id=user_id, trace_name="handle-request", environment="production"):
            precheck_reply = chitchat_precheck(request)
            if precheck_reply is not None:
                # Pure conversational input ("hi", "thanks") -- no model
                # call, no agent dispatch. Same fast path pipeline.py
                # uses; kept in sync here since this is a separate real-
                # account entry point with its own dispatch table.
                result = precheck_reply
            else:
                try:
                    category = route_request(request, client, user_id=user_id)
                except ValueError:
                    result = FALLBACK_MESSAGE
                else:
                    with propagate_attributes(tags=[category]):
                        if category == "chitchat":
                            result = CHITCHAT_REPLIES["other"]
                        else:
                            result = _LIVE_DISPATCH[category](request, client, user_id=user_id)
        # The root span's own input/output IS the trace's input/output in
        # the current SDK (a separate `set_trace_io` call exists but is
        # deprecated) -- explicitly the request/response text, not
        # whatever a nested call's own raw function args happen to be.
        root_span.update(output=result)

    # Each call flushes explicitly rather than relying on a single
    # end-of-process flush -- this function is now called repeatedly from
    # a long-running server (demo_server.py), not just once per CLI
    # invocation, so there's no single "end" to flush at otherwise.
    langfuse.flush()
    return result


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python live_assistant.py "your request here"')
    request = " ".join(sys.argv[1:])
    print(handle_request(request))


if __name__ == "__main__":
    main()
