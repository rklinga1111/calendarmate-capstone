# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CalendarMate: a multi-agent assistant. An Orchestrator classifies each
natural-language user request and routes it to one specialist agent
(briefing, scheduling, email, follow-up, or digest) rather than
answering it directly. The full story backlog and acceptance criteria (tests T1-T12)
live in [calendarmate-backlog.md](calendarmate-backlog.md) — work
through it in order (Epic 1 → 6) and don't start an epic until the
previous one's acceptance criteria pass, except Epic 6 (Follow-Up
Agent), which is independent of Epics 2-5 and only depends on Epic 1.
Epic 6's own detailed build spec (tool schemas, system prompt, data
contract, test fixtures) is in
[calendarmate-followup-agent-spec.md](calendarmate-followup-agent-spec.md)
— note that spec was written assuming a different repo layout
(`evals/`-based, dict-returning functions); it was adapted to this
project's actual conventions below rather than followed layout-for-layout.

Current status: all six epics are implemented -- Epic 1 (Orchestrator
routing), Epic 2 (Briefing Agent), Epic 3 (Scheduler Agent), Epic 4
(Email Agent), Epic 5 (Conflict Resolver, an extension of the Scheduler
Agent rather than a separate module), and Epic 6 (Follow-Up Agent) --
and they're wired into one end-to-end pipeline (`run_orchestrator()` in
`src/calendarmate/pipeline.py`). Before this, every epic had only ever
been exercised in isolation (each agent tested on its own, the
Orchestrator's classification tested on its own) -- nothing had called
them together as a single working assistant until this was built.

## Commands

```bash
pip install -e ".[dev]"   # install project + dev deps
pytest                     # run the full test suite
pytest tests/test_orchestrator.py::test_route_request_rejects_invalid_label  # run a single test
```

Copy `.env.example` to `.env` and set `OPENAI_API_KEY` there to also run
the live tests (`test_route_request_live` in `test_orchestrator.py`, the
T1/T2/T10/T12 tests in `test_briefing.py`, the T3/T4/T5/T6/T9/T11 tests
in `test_scheduler.py`, the T7/T8 tests in `test_email.py`, the
6.1/6.2/6.3 tests in `test_followup.py`, and the end-to-end tests in
`test_pipeline.py`), which call the real OpenAI API. `.env` is
gitignored and loaded automatically by `tests/conftest.py`.
Without a key, those tests are skipped automatically and only the
offline parsing/validation tests run. Never paste a real API key into
chat — set it in `.env` directly.

```bash
python harness.py   # the graded baseline eval -- see "Evaluation harness" below
```

```bash
python google_auth_setup.py                       # one-time real-account OAuth consent (run by the user, not by Claude)
python live_assistant.py "What does my day look like?"   # real Calendar/Gmail -- see "Real Google integration" below
```

`langfuse` is a core dependency (`pip install -e ".[dev]"` already
includes it) since `calendarmate.observability` is now imported by
`pipeline.py`, `orchestrator.py`, and all four agent modules, not just
`live_assistant.py`. Actually producing traces additionally needs
`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL` set
(`.env` or the environment) -- without them, Langfuse's client silently
no-ops (see "Observability (Langfuse)" below for why this is safe for
`pytest`, which never sets them). `harness.py` also traces every case
it runs, tagged with that case's id, when these are set.

## Evaluation harness

`harness.py` + `judge_openai.py` + `baseline_cases.json` (project root,
not under `src/calendarmate/`, since this is a dev/eval tool rather than
application code the way `tests/` also isn't shipped) run all 18 cases
(T1-T12 from the backlog, plus T13-T18 edge cases) through
`run_orchestrator()` and grade each response with an LLM judge against
that case's specific criteria. This is the assignment's graded
Evaluation Requirement, and it's distinct from `pytest`: the test suite
makes deterministic keyword assertions per agent (calling
`answer_briefing` etc. directly); this harness judges natural-language
output against the backlog's actual prose criteria, through the full
classify-then-dispatch pipeline, the way a real request would flow.

- **T13-T15 are edge cases**, added after the initial T1-T12 pass, to
  probe territory the backlog's own criteria don't cover: T13 asks about
  a day with zero fixture events (an empty-calendar case -- does it
  honestly say "nothing scheduled" or invent something to fill the
  silence?); T14 asks for a meeting at an unusual hour framed as being
  for cross-timezone reasons, testing whether the agent fabricates a
  timezone conversion it has no actual data to perform (it never does)
  -- it does NOT, however, reliably still flag the hour itself as
  unusual once the request is framed as a timezone question; this is a
  known, accepted gap, see "T14 ... was investigated and the attempted
  fix was reverted" further down; T15 asks a question with no
  relationship to any of the four categories at all ("What's the
  capital of France?").
- **T16-T18 are a second round of edge cases**, added to probe three
  more gaps neither the backlog nor T13-T15 covered: T16 schedules a
  meeting with an attendee ("Judy") who has zero events anywhere in
  `calendar.json`, checking that a totally unknown attendee is handled
  as cleanly-and-trivially-free rather than causing a fabricated
  conflict or a crash; T17 asks for the action items from `m_003`
  ("Quick Sync"), the fixture meeting with deliberately empty notes,
  checking the Follow-Up Agent says plainly there's nothing to report
  instead of inventing a commitment from nothing; T18 sends pure
  gibberish ("asdkfjh qwoeiru zxcvbn blah blah") rather than a coherent
  off-topic question like T15's, stress-testing the same fallback path
  T15 exercises with input that has no semantic content at all to
  classify. All three passed on the first run with no code changes
  needed -- worth noting for T18 specifically: the live classifier
  routed it to `digest` (not the ValueError/fallback path T15 exercises)
  and both the Briefing and Email agents individually asked for
  clarification rather than fabricating an answer, which still
  satisfies "don't fabricate, don't crash, ask for clarification" even
  though it's a different code path than T15's.

- `baseline_cases.json` reuses the backlog's T1-T12 request phrasing,
  but rewrites the date-dependent ones (T1, T2, T9, T10, T11, T12) to
  use explicit June 2025 dates matching the fixture data instead of
  "today"/"this week" -- `run_orchestrator` has no way to inject a fixed
  `today` the way agent-level tests do (that would break the uniform
  `(request, client)` dispatch signature), so a relative-date case would
  legitimately report "no events" against whatever the real current date
  is, rather than exercising the fixture at all.
- **T15 found a real crash**, not just a grading gap: for a genuinely
  off-topic request, the live classifier sometimes returns an empty
  string instead of one of the four valid labels. `route_request`
  correctly rejects that with `ValueError` (refusing to guess a wrong
  category is the right call) -- but nothing caught it, so the exception
  propagated all the way up and killed the entire harness run instead of
  just failing that one case. Fixed at the actual source:
  `run_orchestrator` in `pipeline.py` now catches that `ValueError` and
  returns a plain "I'm not sure how to help with that" message
  (`pipeline._FALLBACK_MESSAGE`) instead of raising. `harness.py` also
  now catches any exception per-case and records it as a failed result
  rather than crashing, as a second line of defense for whatever the
  *next* unanticipated edge case turns out to be.
- `judge_openai.py`'s `judge_response` takes an optional `tool_trace`
  alongside the response text, because some criteria describe an
  internal process ("checks availability before creating") or a
  grounding fact ("never invents email content") that the reply's own
  text can't settle -- a judge with only the final text has to guess,
  and guesses wrong. This went through two iterations: a trace of tool
  *names* alone fixed an early false failure on T3 ("didn't mention
  checking availability" -- it had, the trace just didn't yet say so),
  but wasn't enough for two others -- T5 was marked as not checking all
  three attendees because `propose_alternative_slots` was the only tool
  the judge saw by name, with no visibility into which attendees were
  passed to it; T7 was marked as inventing email content because the
  judge had no ground truth to check specific-sounding details against
  and just guessed they seemed too detailed to be real (they were,
  verbatim, from the fixture). Fixed by having the trace carry each
  call's actual arguments and actual result, not just its name --
  `SpyingChatClient.trace_lines()` renders lines like
  `propose_alternative_slots(arguments={"attendees": [...]}) -> {...}`,
  giving the judge real evidence for both process and grounding
  criteria instead of a guess.
- `harness.py`'s `SpyingChatClient` wraps the real client and, on every
  `.create()` call, records both which tools get called (name +
  arguments, from the response) and each tool's result (by reading
  `role: "tool"` messages out of the *next* call's `messages` list,
  since a tool's result only appears in the conversation on the request
  after the one that invoked it). It's a transparent pass-through to
  every agent either way, which never sees a different object shape
  than the `ChatClient` protocol they're already built against.
- Running the harness against real requests (rather than the
  hand-picked phrasings already covered by `pytest`) surfaced two real
  prompt gaps `pytest` hadn't caught: (1) given a multi-attendee request
  where every detail except the exact time was specified, the Scheduler
  would silently book the first open slot instead of proposing options
  (violating T5) -- `scheduler.md` now has an explicit rule for "day/
  attendees/duration known, time not given" as a distinct case from
  both "ask for missing info" and "book directly"; (2) the "act, don't
  ask" conflict rule from Epic 5 didn't hold when the request named an
  explicit past date instead of "today" -- the reminder message injected
  in `scheduler.py` after a detected conflict now explicitly says this
  applies regardless of whether the date is in the past relative to the
  model's stated "today."
- Fixing (1) caused a regression the harness also caught: the heavily
  detailed day-ambiguity guidance started crowding out the simpler
  "ask for everything missing at once" rule, so a request missing day,
  time, AND attendee got only "which day?" back, with the model
  pre-emptively explaining the today-vs-next-week caveat before the user
  had even named a day. Fixed by making explicit that missing-info
  questions go out together in one message, and that the
  today-vs-next-week caveat only applies once a day name has actually
  been given, not pre-emptively.
- **The same "don't handle concerns as separate turns" bug recurred a
  third time, in a new combination**: "schedule something for Sunday at
  6am" (a weekend/early-hour time, with the attendee and duration ALSO
  missing) got only the weekend/hour confirmation back, with no mention
  that who-with and how-long were still needed -- same failure shape as
  the day-ambiguity-crowding-out-missing-info bug above, just between a
  different pair of rules this time (weekend/unusual-hour vs.
  missing-info, rather than day-ambiguity vs. missing-info). Fixed the
  same way: the weekend/unusual-hour rule in `scheduler.md` now
  explicitly says that when info is ALSO missing, both concerns go in
  the same reply, with a worked example. General lesson for this
  prompt: every time two "pause and ask" rules can apply to the same
  request, check explicitly that the prompt says to combine them --
  don't assume it generalizes from one such fix to the next pair of
  rules that can collide the same way.
- **Title is the one field the Scheduler now fills in itself instead of
  asking for.** A real booking stalled asking "what's the title?" even
  though `scheduler.md`'s missing-info rule never listed title as
  something to ask about in the first place -- the model was inventing
  that requirement on its own since `create_event`'s schema marks
  `title` required. Fixed by adding an explicit rule: generate a short,
  neutral title from who it's with (e.g. "Meeting with Bob") and never
  ask, while still never inventing a topic/agenda that wasn't stated.
  Covered by `test_missing_title_is_auto_generated_not_asked_for`.
- **A real "confirm and book" request was still met with a second
  round of confirmation questions**, including re-asking for a duration
  and attendee that were already stated in the same message. Root
  cause: the weekend/unusual-hour rule said "ask the user to confirm
  before booking...no matter how clearly the user stated the day and
  time" with no exception for a message that already contains that
  confirmation -- so the model dutifully asked again even when told
  "I confirm, book it now." Fixed by adding an explicit carve-out: if
  the SAME message already states the user wants to proceed (e.g. "I
  confirm", "go ahead and book it", "book it now"), treat that as
  already answered and check-and-book in the same reply, while still
  asking for anything genuinely missing. Covered by
  `test_weekend_time_with_confirmation_already_given_books_immediately`.
- **A real booking landed on the wrong day** (today's own date, a
  Thursday, labeled "Sunday" in the confirmation) once the Scheduler
  was told to stop asking and act immediately -- rushing exposed a gap
  that was always there: resolving a bare weekday name ("Sunday") to a
  real ISO date was left entirely to the model's own arithmetic, with
  only "today" as ground truth. This is the same "LLM date arithmetic
  is unreliable" failure already fixed twice elsewhere (weekday labels
  in `briefing.py`, week-range resolution in `email.md`), just hitting
  a third spot that had never needed it before. Fixed the same way:
  `handle_scheduling_request` now computes the next 7 days' exact dates
  in Python (`_upcoming_days_table`) and includes that lookup table in
  the system message, so resolving a weekday name is a table lookup,
  never a calculation. Covered by
  `test_system_prompt_includes_a_precomputed_upcoming_days_table`. The
  wrongly-dated real event (Sept 10, 2026 instead of the requested
  Sept 13) was found and deleted via the Calendar connector after the
  fix, not left on the real calendar.
- **A second, independent real booking landed at the wrong hour**: a
  request for "3pm IST" was silently converted to "9:30am UTC" and that
  UTC hour was passed as the plain 24-hour time, which every tool here
  already treats as the calendar's own local wall-clock time -- so the
  event was created at 9:30 AM instead of 3:00 PM. `scheduler.md` had
  never said anything about timezones at all; the model's own
  "helpfulness" filled that gap with an unrequested, incorrect
  conversion. Fixed with an explicit rule: a named timezone is
  descriptive only ("whose clock this number already belongs to"), the
  hour/minute given is passed through unchanged, and no conversion is
  ever performed. The first version of this rule added a line noting
  that a past mistake had happened ("...has caused a real booking to
  land at the wrong wall-clock hour before") -- that framing backfired
  immediately: it made the model spuriously flag a plain Thursday 3pm
  request as a weekend/unusual hour out of extra caution, with no
  timezone conversion even being the actual issue that time. Removed
  the incident-narrative framing and stated the rule as a plain
  instruction instead ("treat the hour and minute exactly as given"),
  which fixed both the conversion bug and the caution-drift regression
  it briefly introduced. Lesson: a prompt rule should state what to do,
  not narrate that doing the wrong thing caused a problem before --
  the narrative reads as an amplified warning and can push the model
  into over-caution on unrelated, correctly-shaped requests. Covered by
  `test_named_timezone_is_never_converted`. The wrongly-timed real event
  was found and deleted the same way as the wrong-day one above.
- **A real request booked a meeting at a time that had already passed**:
  "meeting with X at 10am today," asked at 3pm, was booked at the
  already-elapsed 10am slot instead of being refused. Root cause:
  `handle_scheduling_request` only ever told the model what day "today"
  was (a date), never what time it currently was -- so a same-day
  request had no ground truth to compare against and nothing to reveal
  the requested time was in the past. Fixed by adding an optional `now:
  datetime` parameter, included in the system message as "The current
  time right now is HH:MM," plus a third pause-and-ask situation in
  `scheduler.md` (alongside the existing day-ambiguity and weekend/
  unusual-hour ones): a same-day time at or before that current time is
  refused outright, never booked even if the user insists it's fine,
  since -- unlike weekend/unusual-hour -- there's no legitimate choice
  to confirm when the moment is simply gone. `now` deliberately does
  NOT default to the real wall clock: every mocked test books against a
  fixed, arbitrary `today` (June 2025) far from the real current
  moment, so defaulting to `datetime.now()` would make every existing
  test's same-day booking look like it's already in the past. It
  defaults to midnight of `today` instead (nothing looks past unless a
  real `now` is explicitly given), and `live_assistant.py`'s
  `_scheduling` is the one caller that passes the real
  `datetime.now()`. Covered by
  `test_same_day_past_time_is_refused_not_booked` and two scripted tests
  for the system-message content. The wrongly-booked past-time real
  event was found and deleted the same way as the two above.
- Expect occasional single-case run-to-run variance in `eval_results.json`
  even with no code changes (observed on the original 12 cases: 9/12,
  11/12, 11/12, 12/12, 12/12 across consecutive runs while diagnosing the
  above) -- `temperature=0` reduces but doesn't eliminate this. A single
  run isn't a verdict; re-running a failing case in isolation a few
  times (as the fixes above did) is how to tell a real prompt gap from
  one unlucky draw. State at the time this line was written, with all 15
  cases (T1-T15): 15/15, reproduced twice in a row after the fixes
  above. That state didn't hold permanently, though -- see the harness
  date-collision entry below for why 12/15 later became the norm, T16-
  T18 further below for the second round of edge cases added afterward,
  and the T14 entry further below for how T6 and T14 were later fixed
  properly, in code. Current state, all 18 cases: 18/18, with T8 (email
  classification variance) the only case that can occasionally still
  flip on a given run -- see that case's own entry above for why.
- **The harness itself went stale as real wall-clock time drifted past
  its fixture window, and started failing cases that were never
  actually broken.** `baseline_cases.json`'s date-dependent requests are
  pinned to June 2025 (matching `calendar.json`/`inbox.json`), which was
  fine as long as the real date stayed close to that window -- but
  `scheduler.py`'s already-passed-time booking guard compares the
  requested time against `now`, which defaults to midnight of
  `date.today()` when nothing else pins it. Once the real date drifted
  well past June 2025, every one of those June-2025 booking requests
  started looking like a request to book in the past, and the guard
  correctly-but-unhelpfully refused all of them -- not a product bug,
  just this reproducibility mechanism no longer reproducing anything.
  Fixed in `harness.py` (not application code) by pinning `date.today()`
  for the three modules that call it directly (`briefing.py`,
  `email.py`, `scheduler.py`) to `date(2025, 6, 17)` before running any
  case, via a `_PinnedDate(date)` subclass rebinding each module's own
  `date` name -- `date` is an immutable built-in type and can't be
  patched directly, but each module's `from datetime import date`
  import creates its own rebindable local name. `scheduler.py`'s `now`
  didn't need separate pinning since it's derived from `today`
  (`datetime.combine(today, time.min)`) rather than calling
  `datetime.now()` independently. This is scoped entirely to the
  harness process and touches no application code, mirroring the fixed
  `today=`/`now=` pytest already injects at the agent level for the same
  reason.
- **A batched email classification call could look fabricated to the
  judge even though its content was entirely real**, because the
  judge's `tool_trace` only ever captures a tool's result by finding a
  `role: "tool"` message with a matching `tool_call_id` in some LATER
  `.create()` call (see `SpyingChatClient` above) -- but the "needs
  attention" branch's first `classify_emails` batch call runs on the
  same `messages` list `get_emails` itself was answered on, before
  returning straight from `_format_needs_attention` with no further
  call ever made on that list. The real `get_emails` result was feeding
  the reply the whole time; it just never became visible to anything
  inspecting the conversation externally. Fixed by threading the
  originating tool call's id/name/arguments and its real, unfiltered
  result into the FIRST `classify_emails` batch call only (as a
  synthetic `assistant` message declaring that `tool_calls` entry,
  followed by the matching `role: "tool"` message) -- grounding the
  first batch call in real, externally-checkable data without any extra
  API call. This needed two attempts: the first version appended only
  the bare `tool` message, which passed every offline test (the
  scripted fake client doesn't enforce message ordering) but failed
  immediately against the real OpenAI API with a 400 `BadRequestError`
  ("messages with role 'tool' must be a response to a preceeding
  message with 'tool_calls'") -- the real API enforces strict
  ordering that a test-only fake simply doesn't. Fixed by adding the
  synthetic `assistant`/`tool_calls` message the API requires
  immediately before it. Lesson: a fix that only ever runs against a
  scripted fake client can pass every test and still be wrong the first
  time it touches the real API -- anything constructing a raw
  `messages` list by hand needs at least one live-API run before
  it's trusted, not just offline coverage. Covered by
  `test_needs_attention_batch_call_carries_the_real_get_emails_result`
  in `test_email.py`, and verified against the real API afterward.
- **T14 (11:30pm + an explicit cross-timezone concern) went through
  three failed prompt-wording attempts before being fixed properly in
  code -- worth recording both halves.** The judge correctly flagged
  that the model doesn't reliably apply the weekend/unusual-hour rule
  when the off-hours time is framed as a timezone question ("make sure
  this works for Bob in Tokyo") rather than a plain hour. Every wording
  tried in `scheduler.md` to fix this -- including a version scoped only
  to the weekend/unusual-hour rule bullet, and one requiring both an
  explicit timezone concern AND an off-hours time -- caused the model to
  start spuriously flagging a plain, in-range time like "3pm IST" as
  unusual too, breaking the existing, incident-derived regression test
  `test_named_timezone_is_never_converted` (a real past booking once
  landed at the wrong hour from an unwanted timezone conversion; this
  test exists specifically to guard against that class of regression).
  Simply put: adding *any* language that puts "timezone" and
  "flag/unusual" in the same breath measurably increased the model's
  chance of over-applying the flag to a compliant, in-range time, no
  matter how carefully the condition was spelled out -- all three
  attempts were reverted rather than ship a fix that traded this edge
  case for a regression in already-verified safety behavior.
- **The actual fix moved the 8:00-18:00/weekend check into Python**,
  the same defense-in-depth pattern already used for the already-passed-
  time check right above it in `_dispatch_tool`'s `create_event` branch
  (and for attendee-email validation, ownership checks, and cancellation
  grounding elsewhere in this file): `_is_off_hours(day, start)` computes
  the fact directly, and `create_event` refuses with an `error` result
  --  unbooked, nothing silently created -- whenever that's true and the
  request's own wording doesn't already contain one of the inline
  confirming phrases `scheduler.md`'s weekend/unusual-hour rule already
  documents ("I confirm", "yes, keep it there", "go ahead and book it",
  "please proceed anyway", "book it now"), reused verbatim here
  (`_OFF_HOURS_CONFIRMATION_PHRASES`) so the code gate and the prompt's
  own bypass rule agree on what counts as consent instead of drifting
  into two different definitions of it. This works where three prompt
  attempts didn't because it no longer depends on the model reliably
  *noticing* the rule applies regardless of how the request happens to
  be framed -- `create_event` simply cannot succeed for an
  unconfirmed off-hours time, the same unconditional guarantee the
  past-time check already has. Verified 3/3 on both T6 and T14 in
  isolation (T6 had the same underlying bug in a different guise: the
  model would sometimes narrate "since you've confirmed the weekend
  time" when the user never actually had -- the code gate makes that
  narration impossible to act on, not just less likely). Full harness:
  18/18. No `scheduler.md` wording changed for this fix at all, which is
  exactly why it didn't reintroduce the "3pm IST" regression the prompt
  attempts kept causing.
- **Fixing this surfaced one more real, pre-existing test fragility**:
  `test_pipeline_routes_and_answers_scheduling` asked to book "tomorrow
  at 10am" with no way to pin `today` (`run_orchestrator`'s signature
  has no `today=` seam -- see above), so it was silently dependent on
  whichever real weekday happened to be running it. It only ever passed
  before because the model's own weekend-confirmation compliance was
  inconsistent enough to sometimes book through a weekend anyway (the
  same unreliability this whole fix targets) -- once the code gate made
  that refusal unconditional, this test started failing deterministically
  on any Friday or Saturday test run (when "tomorrow" lands on a
  weekend), which is exactly what happened here. Fixed by computing an
  explicit, real, guaranteed-non-weekend date in the test itself
  (`_next_weekday()`) instead of relying on a bare relative "tomorrow" --
  the same "don't let a test's correctness depend on which real day it
  happens to run on" lesson as the harness's own June-2025 date-pinning
  fix above, just found in a pytest test instead of the harness this
  time.

## Real Google Calendar/Gmail integration

`live_assistant.py` (project root) is the real-account entry point,
kept entirely separate from `pipeline.run_orchestrator` on purpose:
`pytest` and `harness.py` both call `run_orchestrator`, which always
defaults every agent to its mocked fixture, and nothing about that
changed here. `live_assistant.py` reimplements the same
classify-then-dispatch shape but supplies real data/write functions as
each agent's already-existing injectable parameters instead.

- **The seam this plugs into already existed** — every agent's
  fixture-loading parameter (`events=`, `emails=`, `meetings=`) was
  injectable from day one for testability, so wiring in a real loader is
  just passing a different value for the same parameter. The one thing
  that *didn't* already exist was a way to swap the WRITE tools
  (`create_event`, `send_followup_email`, and the meeting lookup
  `get_meeting_record` uses): those were hard-called inside
  `scheduler.py`/`followup.py`'s `_dispatch_tool`. Added
  `create_event_fn` (to `handle_scheduling_request`) and `get_meeting_fn`
  / `send_email_fn` (to `run_followup_agent`) as optional keyword
  parameters, each defaulting to a small closure over the existing mock
  function -- so every existing call site and every existing test is
  completely unaffected (none of them pass the new parameter), while a
  real integration passes its own function matching the same
  `(title, day, start, end, attendees) -> dict`-shaped signature.
- `src/calendarmate/integrations/` is new and holds ONLY the real API
  code (`google_auth.py` credential loading, `google_calendar.py`,
  `gmail.py`) -- nothing under `src/calendarmate/tools/` changed, and
  nothing in `tests/` imports anything from `integrations/`. This is
  deliberate: the mocked tools and the real ones are two independent,
  parallel implementations of the same shapes, not one thing with a
  runtime branch, so there's no environment variable or global flag that
  could accidentally make a test run touch a real account.
- `get_meeting_record_from_google_calendar` uses the Calendar event's
  `description` field as the "meeting notes" source, since Google
  Calendar has no native notes/transcript field. This is the same
  simplification the Follow-Up Agent's original build spec explicitly
  flagged for the *mocked* version -- carried through honestly to the
  real one rather than pretending a better data source exists.
- `credentials.json` and `token.json` (both project-root, both
  gitignored) are never read by anything except
  `calendarmate.integrations.google_auth` and the one-time
  `google_auth_setup.py` script. Claude never runs the OAuth consent
  flow itself (that needs a browser login only the user can do), and
  never executes a real `create_event`/`send_followup_email` call
  without the user having explicitly asked for that specific send/booking
  in the conversation first -- both are real-world, hard-to-reverse
  actions on the user's real account.
- Real Calendar attendees are email addresses (or display names, if
  set), not the mocked fixture's first names ("Bob", "Carol") -- a
  request phrased against the real integration needs to name people the
  way the real calendar does.
- **Two more real bugs surfaced only by an actual send, not by any
  offline test:**
  1. `get_meeting_record_from_google_calendar` was originally
     keyword-only (`*, meeting_id=..., title_hint=...`), but
     `followup._dispatch_tool` calls `get_meeting_fn(meeting_id,
     title_hint)` positionally -- this raised a `TypeError` the first
     time a real follow-up request tried to look up a meeting. The
     offline tests never caught it because they exercised the DI seam
     with a hand-written fake whose signature happened to already be
     positional, not this function. Fixed by dropping the `*`. Lesson:
     an integration function meant to satisfy a `Callable[[...], ...]`
     contract needs a test that calls *that function*, not just a
     compatible stand-in, or a signature mismatch like this won't be
     caught until it's exercised for real.
  2. Attendees came back as bare display names (e.g. a real attendee's
     display name) instead of real email addresses when both were available
     (`a.get("displayName") or a.get("email", "")`), because that
     preference makes sense for `_parse_event`'s Briefing-facing,
     read-only, human-readable display -- but `get_meeting_record`'s
     attendees feed `send_followup_email`'s recipients directly, and
     Gmail rejected the real send with "Invalid To header" since a
     bare name isn't a valid address. Fixed by always using the real
     `email` field for `get_meeting_record_from_google_calendar`
     specifically, never `displayName` -- correctness for actually
     addressing an email matters more there than a friendlier-looking
     name would. `_parse_event` (Briefing/Scheduler) is unaffected and
     keeps preferring `displayName`, since it only ever displays
     attendees, never uses them to send anything.
  Both are covered by mocked-service tests in
  `test_google_calendar_integration.py` (no real credentials needed --
  `calendar_service` itself is patched), so this class of "the seam
  works, but the specific real implementation behind it doesn't" bug
  gets caught without needing a live send every time.

- `src/calendarmate/pipeline.py` — `run_orchestrator(request, client)`
  is the actual end-to-end entry point: it calls `route_request` to
  classify, then looks up the matching agent function in `_DISPATCH` and
  calls it with the same `(request, client)` pair, returning its answer
  unmodified. It works because all four agent functions
  (`answer_briefing`, `handle_scheduling_request`,
  `answer_email_request`, `run_followup_agent`) already share the exact
  same `(request: str, client: ChatClient) -> str` positional signature
  — every other parameter on each of them is keyword-only with a
  default, so `_DISPATCH[category](request, client)` works uniformly
  across all four without any adapter code. `_DISPATCH[category]` is
  never defensively guarded against a missing key: `route_request`
  already raises `ValueError` for anything outside the same four labels
  `_DISPATCH` covers, so an invalid category can't reach the dispatch
  step in the first place. It DOES catch that `ValueError` itself,
  though, and returns `_FALLBACK_MESSAGE` instead of letting it
  propagate -- found via the eval harness's T15 edge case (a request
  with no relationship to any of the four categories), where the live
  classifier returned an empty string and crashed the entire harness
  run before this fix existed. This isn't defensive coding for a
  scenario that can't happen; it's a fix for one that demonstrably did.
- `src/calendarmate/orchestrator.py` — `route_request(request, client)`
  classifies a request into `"briefing" | "scheduling" | "email" |
  "follow_up"` and returns only that label; it never generates the final
  answer. It takes an injected `client` matching the `ChatClient`
  protocol (`.create(**kwargs) -> response`), so callers pass
  `openai.OpenAI().chat.completions` in production and a fake client in
  tests — this is what makes the routing logic testable without hitting
  the real API.
- `src/calendarmate/briefing.py` — `answer_briefing(request, client,
  today=..., events=...)` answers calendar questions by having the model
  call the `get_calendar_events` tool (defined in the same file) rather
  than answering from its own knowledge; the tool is the only source of
  event data the model is given, which is what prevents it from
  inventing meetings. `today` and `events` are injectable for the same
  testability reason `client` is on the Orchestrator — tests pin `today`
  to a fixed date inside the fixture's date range so results don't
  depend on when the test suite actually runs.
- `src/calendarmate/tools/calendar_tool.py` — pure functions
  (`load_events`, `events_in_range`, `find_conflicts`) over the mocked
  fixture at `src/calendarmate/fixtures/calendar.json`. Conflict
  detection is done here in plain Python, not by the model, since a
  time-overlap check is exact and doesn't need an LLM. Swapping the
  mocked fixture for the real Google Calendar API later only means
  changing what `load_events` does internally — `events_in_range` and
  `find_conflicts` don't need to change.
- `src/calendarmate/scheduler.py` — `handle_scheduling_request(request,
  client, today=..., events=..., booked=...)` books meetings via three
  tools (`check_availability`, `propose_alternative_slots`,
  `create_event`, all defined in the same file). Unlike the Briefing
  Agent's single tool round-trip, this runs a loop (capped at
  `_MAX_TOOL_ROUNDS`) since a real booking may need several steps
  (check, then propose alternatives or create) or none at all (the model
  may just ask a clarifying question with no tool call, which is how
  "ask instead of guessing" and "never book with missing info" are
  satisfied — there's no separate validation path, it's just the model
  choosing not to call `create_event` yet). `booked` is an injectable
  output list the same way `events` is an injectable input — tests
  assert against it instead of mocking a real Calendar write.
- **`user_name` (default `"Alice"`)**: `scheduler.md` hardcoded "Alice"
  as the requesting user's name in three places, since that's who the
  mocked fixture's calendar belongs to. This broke real usage --
  `live_assistant.py` surfaced it live, asking a real request for
  "attendees (besides Alice)" against an account that has no Alice at
  all. Fixed by turning `scheduler.md`'s hardcoded name into a
  `{{USER_NAME}}` placeholder, substituted by `load_system_prompt(user_name)`;
  `handle_scheduling_request`'s new `user_name` parameter defaults to
  `"Alice"` so every existing mock test is unaffected, while
  `live_assistant.py` passes the real authenticated account's own email
  (`google_calendar.get_authenticated_user_email()`, from the Calendar
  API's own `calendarList().get(calendarId="primary")` rather than a
  hardcoded personal address in the shared codebase). Any future prompt
  content that names a specific persona rather than describing a role
  generically should default to being an injected parameter from the
  start, the same way `today` already is, rather than assuming the mock
  fixture's identity is safe to hardcode.
- **`create_event_via_google_calendar` rejects unresolved attendees
  instead of silently dropping them.** It originally filtered
  `attendees` to `[{"email": a} for a in attendees if "@" in a]` when
  building the real Calendar API request, but then returned
  `"attendees": list(attendees)` in its result -- the ORIGINAL,
  unfiltered list. A real request ("schedule a meeting tomorrow with
  mytcl") surfaced this: had it proceeded, the event would have been
  created with that attendee silently missing from the real invite,
  while the tool's own result (and therefore the agent's confirmation
  to the user) falsely claimed they were included. Fixed by validating
  attendees *before* building the API request at all: if any lack `@`,
  return `{"error": ...}` naming them and asking for a real address, and
  the request never reaches the Calendar API. `scheduler.md` has a
  matching rule: an `error` field in `create_event`'s result means
  nothing was booked, and the agent must say so rather than confirming a
  booking or Meet link that doesn't exist. Tested in
  `tests/test_google_calendar_integration.py` — the validation runs
  before any network call, so this doesn't need real credentials to
  test: an error being returned cleanly (vs. an exception from a
  mis-authenticated API call) is itself proof the check short-circuited
  correctly.
- `src/calendarmate/tools/scheduling_tool.py` — pure functions
  (`check_availability`, `propose_alternative_slots`, `create_event`)
  over the same `Event` shape as `calendar_tool.py`. Availability and
  conflict checks are exact overlap math in plain Python, not model
  judgment; only `create_event` has a side effect (appending to
  `booked`), and it's a pure function taking that list as an argument
  rather than managing its own state.
- `tests/fakes.py` — shared `ScriptedChatClient` and message-shape fakes
  (`FakeMessage`, `tool_call_response`, `final_response`, etc.) used by
  both `test_briefing.py` and `test_scheduler.py` to script multi-turn
  tool-calling conversations without hitting the real API. Note:
  `ScriptedChatClient` snapshots the `messages` list on each call — the
  real message list is mutated (appended to) across loop iterations, so
  without snapshotting, every recorded call would end up pointing at the
  same final list.
- `src/calendarmate/prompts/*.md` — system prompts loaded from disk
  (via each agent's own `load_system_prompt()`) rather than embedded as
  string literals in code. Each new agent should follow this same
  pattern: one prompt file per agent, loaded by that agent's module.
- **Day-name disambiguation** (in both `briefing.md` and `scheduler.md`):
  when the user names a day of the week that happens to equal today's
  own weekday (e.g. they say "Tuesday" and today is also a Tuesday),
  that's genuinely ambiguous between today and next week — the agent
  must ask which one is meant rather than guessing either way. An
  explicit calendar date (e.g. "June 17"), or a weekday name that does
  NOT match today's, is unambiguous and gets resolved directly with no
  question asked. This rule needed real iteration against the live
  model: an early version of the added wording made the Scheduler
  over-generalize "ask before proceeding" into stalling on unrelated
  steps too (asking "shall I go ahead?" on a plain conflict-free
  booking, or "would you like alternatives?" instead of just calling
  `propose_alternative_slots`) — `scheduler.md` now explicitly names the
  *only* two situations that warrant pausing (this day-ambiguity case,
  and the weekend/unusual-hour/conflict case) so the model doesn't infer
  a broader "always ask" policy from either one.
- **`test_ambiguous_weekday_matching_today_prompts_clarification` is a
  known, higher-than-usual-flakiness live test, documented rather than
  silently tolerated.** Re-run in isolation 4 times while investigating
  a pytest failure, it passed only once (1/4) -- worse than this
  project's normal "occasional single-case variance" baseline (see the
  harness section above), suggesting the model more often skips the
  day-ambiguity question than asks it for this exact request shape: it
  jumps straight to resolving whatever real conflict "today" happens to
  produce (naming the conflicting meeting, proposing alternatives)
  instead of first asking whether "Tuesday" means today or next week, as
  `scheduler.md`'s rule above requires. A code-level fix was attempted
  twice. **First attempt**: detecting the ambiguity from the request's
  own wording in Python (the same pattern as
  `is_cancel_intent`/`has_confirmed_unusual_time`) and injecting a blunt
  reminder right before the first generation -- could not be verified,
  since every live test failed with `openai.RateLimitError: ...
  project_spend_limit_exceeded` on the Interview-Kickstart-issued
  project's OpenAI key, the same class of external, account-level
  blocker as the earlier `401 invalid_organization` incident. Per this
  project's own established discipline -- never trust a change that
  hasn't actually been run against the live API -- that attempt was
  reverted rather than shipped unverified.
  **Second attempt** (current state): the identical Python-side fix
  (`_mentions_ambiguous_weekday` in `scheduler.py`, gated on the
  request naming today's own weekday with no "next"/explicit
  date/month already resolving it) was reimplemented, this time with
  offline test coverage that doesn't need the live API at all --
  `test_ambiguous_weekday_reminder_is_injected_when_request_names_todays_weekday`
  and the parametrized
  `test_ambiguous_weekday_reminder_is_not_injected_when_already_resolved`
  assert directly on the scripted client's captured `messages` list,
  proving the Python-side trigger logic itself is correct (fires for
  "Tuesday" when today is Tuesday, stays silent for "next Tuesday," an
  explicit date, or a non-matching weekday). This is real, deterministic
  coverage of half the fix -- **the other half, whether the live model
  actually obeys the injected reminder, is still unverified** as of this
  writing, since the OpenAI project's spend limit remains exceeded. Do
  not treat this as fully resolved until
  `test_ambiguous_weekday_matching_today_prompts_clarification` itself
  has been re-run live several times and passed consistently -- the
  same "several isolated re-runs before trusting it" bar every other
  live-model fix in this file was held to.
- `src/calendarmate/email.py` — `answer_email_request(request, client,
  today=..., emails=...)` follows the Briefing Agent's shape: one tool
  (`get_emails`), `emails` injectable the same way `events` is. Unlike
  calendar conflict detection, "action-required vs. FYI" and "group by
  urgency/topic" aren't computed in Python at all — there's no exact
  test for those the way there is for time overlap, so that
  classification is left entirely to the model's judgment. The tool's
  only job is still to be the sole source of which emails exist, which
  is what prevents invented senders/subjects.
- `get_emails` takes optional `start_date`/`end_date`. With neither, it
  returns only currently-unread mail (the original behavior, unchanged);
  with both, it returns every email in that range **read or unread**,
  via `email_tool.emails_in_range`. This distinction matters: "what
  emails came in last week" is a different question from "what's
  unread," and answering it by silently substituting the unread list
  would misreport read mail as if it never arrived. `email.md` carries
  the same Monday-Sunday "this/last/next week" resolution rules as
  `briefing.md`, added after a real request ("what did I get last
  week") returned the *current* week's unread mail mislabeled as last
  week's.
- `src/calendarmate/tools/email_tool.py` — `Email` now carries
  `received_at`; `load_emails`/`unread_emails` are unchanged,
  `emails_in_range` is new (mirrors `calendar_tool.events_in_range`).
  The fixture at `src/calendarmate/fixtures/inbox.json` has one
  `"read": true` email dated into a deliberately distinct "last week" --
  it must be absent from an unread-only query and present in a
  last-week-scoped one, and both are covered by tests.
- **The action-required vs. FYI classification needed three rounds of
  prompt iteration, plus a model change, to get right against a real
  inbox.** A real "what needs my attention?" query surfaced clear false
  positives (a bank transaction alert, a Google security alert, a
  "someone mentioned you" notification, a job alert) that the mocked
  fixture's simpler, clearer-cut cases never exercised. Round 1
  (category list: "automated alerts are FYI," etc.) fixed those exact
  examples but a *different* batch of marketing/confirmation mail
  (session invites, "LAST CALL" discounts, registration confirmations)
  slipped through instead — the rule didn't generalize. Round 2 added
  worked examples ("YES BANK: Transaction Alert -> FYI", etc.) to make
  the judgment more concrete — this backfired badly: the model
  hallucinated one of the illustrative examples as if it were a real
  inbox item, attaching invented content ("Can you review the attached
  doc by Friday?") to a real sender's name. That's a direct violation of
  the "never invent" guarantee this whole project is built around, and
  strictly worse than the over-classification bug it was meant to fix.
  Reverted the examples immediately. Round 3 kept the category-based
  rule (safe, no hallucination risk) and additionally moved the Email
  Agent from `gpt-4o-mini` to `gpt-4o` (`_MODEL` in `email.py`) — every
  other agent stays on `gpt-4o-mini`; this one nuanced nothing-computed-
  in-Python judgment call demonstrably needed a stronger model rather
  than more prompt wording. Lesson for any future prompt tuning: a
  worked example is real inbox-shaped text sitting in the same context
  the model draws real answers from — never introduce one written in a
  style close enough to real data that the model could mistake it for
  the tool's actual output, especially for a "never invent" agent.
- **`src/calendarmate/digest.py` — a 5th route, `digest`, for requests
  spanning both calendar and email at once.** A real "what needs my
  attention this week?" request wanted both, but the Orchestrator's
  single-category classification could only ever pick one, so it went
  to the Email Agent alone and every real meeting that week went
  unmentioned. `answer_digest_request(request, client, today=...,
  events=..., emails=...)` doesn't have its own tools or grounding
  logic at all -- it calls `answer_briefing` and `answer_email_request`
  (each already independently grounded) with the same request text and
  concatenates their two answers under `### Calendar` / `### Email`
  headings. Deliberately NOT a third LLM call synthesizing them into one
  blended narrative: two already-correct answers stitched together
  carries no hallucination risk, where an extra synthesis pass would.
  `orchestrator.md` picks `digest` for broad, open-ended phrasing
  ("catch me up," "what's going on") and reserves `briefing`/`email` for
  requests that are unambiguously calendar-only or inbox-only.
  `pipeline.py`'s `_DISPATCH` and `live_assistant.py`'s `_LIVE_DISPATCH`
  both gained a `"digest"` entry; every other route was untouched.
- **A second, unrelated bug surfaced by that same real request**: the
  Briefing Agent mislabeled a real date's weekday ("Saturday, September
  6, 2026" for a date that's actually a Sunday) when asked for a
  week's events with day-of-week headings. Same root cause as the
  "last week" date-range bug from earlier -- LLMs are unreliable at
  manual calendar arithmetic, and this time it was asked to compute a
  weekday label itself with nothing to check it against. Fixed the same
  way as everywhere else in this project: stop asking the model to
  compute something Python can compute exactly. `_run_calendar_tool` in
  `briefing.py` now includes `"weekday": e.date.strftime("%A")` in every
  event it returns, and `briefing.md` explicitly says to use that field
  for any day-of-week label rather than working it out. Covered by
  `test_weekday_labels_use_the_tools_computed_value_not_a_guess` in
  `test_briefing.py`, which checks Sprint Retro (June 20, 2025, a real
  Friday) ends up under a "Friday" heading and no other -- note the
  first version of that test used a crude fixed-size text window around
  the phrase to find "nearby" day names, which wrongly flagged a
  *neighboring* day's heading as the match in a day-by-day list; it was
  rewritten to actually find which day-section the phrase falls under.
- **Epic 5 (Conflict Resolver)** needed no new tools -- it's a prompt
  extension of the Scheduler Agent plus one code fix in
  `handle_scheduling_request`'s loop. On a conflict, `scheduler.md` now
  requires naming the conflicting meeting and then offering a concrete
  trade-off (a different time for the new meeting, moving or shortening
  the existing one, or going async) — "moving the existing meeting" is
  just another call to `propose_alternative_slots`, but for the
  *existing* meeting's own attendees instead of the new one's, so no new
  tool code was needed.
- **Real bug found building Epic 5** (not just prompt tuning): the model
  sometimes returns a message with BOTH text content and a tool call in
  the same turn (e.g. it states "conflicts with Design Review and Client
  Sync" *and* calls `propose_alternative_slots` in one turn). The
  scheduler's loop used to discard any message's `.content` unless that
  turn had no tool calls, so that text was silently thrown away and only
  the final turn's content was returned — making it look like the model
  was randomly forgetting to name the conflict, when it had actually said
  it and the code dropped it. Fixed by accumulating every non-empty
  `message.content` across the whole loop into `reply_parts` and
  returning the join of all of them, not just the last one. Any agent
  that grows a multi-round tool loop (like the Briefing Agent's tool loop
  did with a single round) should accumulate intermediate content the
  same way rather than assuming only the final turn ever has text.
- The scheduler also injects a one-off reminder system message right
  after a conflict is detected (`"Reminder: check_availability just
  found a real conflict..."`), rather than relying only on the static
  system prompt from the top of the conversation — a rule restated right
  before the generation it governs is followed far more reliably than
  the same rule stated once, many tokens earlier.
- All agents call the chat completions API with `temperature=0`. This
  alone did not fully fix the conflict-naming flakiness above (that was
  the accumulation bug), but it does reduce run-to-run variance for
  these classification/tool-use tasks, where determinism matters more
  than creative variation.
- `src/calendarmate/followup.py` — `run_followup_agent(request, client,
  meetings=..., sent=...)` is Epic 6, built from
  `calendarmate-followup-agent-spec.md` but restructured to match this
  project's conventions rather than that spec's `evals/`-based layout:
  an agent module under `src/calendarmate/` (not `evals/`), a prompt
  file under `src/calendarmate/prompts/`, a fixture under
  `src/calendarmate/fixtures/meetings.json`, `str`-returning with
  injectable state (`meetings`, `sent`) instead of the spec's
  dict-returning contract, and no separate "Response Formatter" stage
  since one was never built for the other agents either. It reuses the
  Scheduler Agent's multi-round tool loop shape (`get_meeting_record`
  then `send_followup_email`) including the `reply_parts` accumulation
  fix from Epic 5, applied here from the start instead of rediscovered.
- Building Epic 6 surfaced the same "over-cautious confirmation"
  pattern Epic 5 did: on an explicit "send a follow-up for..." request,
  the model would draft correctly (grounded, exact recipients) but then
  ask "Shall I send this?" instead of sending, even though nothing about
  the request was ambiguous. `followup.md` now explicitly says draft-
  and-send belong in the same reply whenever the user actually asked to
  send, and to only fall back to asking when the request didn't ask for
  a send at all (e.g. "what were the action items" with no send verb).
- Fixing `follow_up`'s Orchestrator semantics: the label already existed
  in `orchestrator.py`'s `Route` type before Epic 6 was built, but
  `orchestrator.md` had defined it as "a continuation of a prior turn"
  (a guess made when the type was first scaffolded, never actually
  exercised by a test). The spec's real meaning — a request about a
  specific past meeting's action items or follow-up email — is a
  different thing entirely, so `orchestrator.md` was corrected to match,
  and `test_orchestrator.py`'s `SAMPLE_INPUTS` now includes follow-up
  examples for the first time.
- The meeting fixture (`src/calendarmate/fixtures/meetings.json`) has
  four entries mirroring the spec's own test-design table: `m_001` is a
  clean happy path (three real action items, right owners); `m_002` has
  notes describing a real but vague discussion with no actual commitment
  (a broken agent invents "Jordan will work on career growth" from it);
  `m_003` has empty notes (a broken agent fabricates content from
  nothing); `m_004` mixes two real action items with an explicit
  non-action statement ("approved, no further action needed") that a
  broken agent turns into a fake task just because it's adjacent to real
  ones. `tools/followup_tool.py` mirrors `calendar_tool.py`/
  `email_tool.py`'s read-only shape (`load_meetings`, `find_meeting` by
  either exact `meeting_id` or partial case-insensitive `title_hint`),
  plus `send_followup_email` as the one side-effecting "action" tool,
  following `scheduling_tool.py`'s pattern of bundling reads and the one
  write together in the same tools file rather than splitting further.

## Cancelling a meeting

A user request ("cancel my sync call with the design team today") that
CalendarMate genuinely couldn't do anything about led to a new
capability: cancellation, requested and designed with the user before
building anything, then implemented as an extension of the Scheduler
Agent -- the same way Epic 5 (conflict resolution) was an extension
rather than a new agent, since cancelling is the same domain (calendar
writes) and needs the same attendee/date context booking already has.

- **Two new tools**, both in `scheduling_tool.py` alongside
  `create_event`: `find_meetings_to_cancel(events, day=None,
  title_hint=None)` looks up candidate meetings (a pure read, mirroring
  `calendar_tool`'s read-only shape) and `cancel_event(events,
  cancelled, event_id, scope)` performs the mocked cancellation,
  appending to an injectable `cancelled` list the same way
  `create_event` appends to `booked`. `scheduler.py` exposes these to
  the model as `find_meeting_to_cancel` and `cancel_event` tool schemas,
  and `handle_scheduling_request` gained matching `cancelled` and
  `cancel_event_fn` parameters (defaulting to the mocked pair, just like
  `booked`/`create_event_fn`) -- `live_assistant.py` passes
  `cancel_event_via_google_calendar` for the real account.
- **`Event` gained three new fields** (`event_id`, `owner`,
  `series_id`), all defaulted so no existing fixture entry, test, or
  `Event(...)` construction anywhere needed to change. `event_id`
  identifies one specific occurrence to cancel; `owner` is who is
  actually allowed to cancel it; `series_id` groups every occurrence of
  the same recurring meeting (`None` for a one-off). The mocked fixture
  synthesizes a stable `event_id` from title+date+start when the fixture
  JSON doesn't specify one (`calendar_tool._synthetic_event_id`), and
  defaults `owner` to `"Alice"` (the fixture calendar's own persona) --
  so only fixture entries that need to be someone *else's* meeting, or
  part of a series, need to say so explicitly.
- **Ownership is enforced in code, not just the prompt** -- the same
  defense-in-depth pattern already used for the already-passed-time
  booking check and unresolved-attendee validation. `_dispatch_tool`'s
  `cancel_event` branch looks the target event up in the same `events`
  list `find_meeting_to_cancel` searched and refuses (returns an
  `error`) if `target.owner != user_name`, regardless of what the model
  decides. This works identically for the mocked fixture and the real
  Calendar data because both populate `owner` on every `Event` --
  `google_calendar.py`'s `_parse_event` now reads it from the raw
  event's `organizer.email`.
- **`cancel_event` is also grounded against a same-conversation
  lookup**: `_dispatch_tool` tracks every `event_id` any
  `find_meeting_to_cancel` call in this request has actually returned
  (`looked_up_event_ids`), and refuses `cancel_event` for any id outside
  that set. The model can't invent or guess an id to cancel -- it must
  have gone through the lookup tool first, in this same request.
- **Recurring series vs. one occurrence**: Google Calendar already
  distinguishes these for free -- when `load_events_from_google_calendar`
  lists events with `singleEvents=True` (already the existing call), a
  recurring meeting's individual occurrences each carry
  `recurringEventId` pointing at the series' master event, which is
  exactly the id that needs deleting to cancel the whole series. This
  meant no extra API call was needed: `_parse_event` sets `series_id =
  raw.get("recurringEventId")`, `find_meeting_to_cancel`'s result
  surfaces it, and the model passes it straight through to `cancel_event`
  as `series_id` when `scope="series"`. The mocked fixture models the
  same relationship with a shared `series_id` string across a "Team
  Standup" occurrence on 2025-06-16 (past), 2025-06-17 (today, `TODAY`
  in the scheduler tests), and 2025-06-23 (future) -- `scope:
  "instance"` on the 06-17 occurrence must cancel only that one, leaving
  the other two dates untouched; `scope: "series"` must cancel all
  three. Both are covered by
  `test_cancel_single_instance_of_a_recurring_meeting` and
  `test_cancel_whole_recurring_series`.
- **A second fixture addition, "Bob's Weekly Sync"** (`owner: "Bob"`,
  Alice only an attendee), exists specifically to test the
  ownership-rejection path offline --
  `test_cancel_refused_when_user_is_not_the_owner` -- without needing a
  real second Google account. It's also what made
  `test_find_meeting_to_cancel_returns_all_matches_for_disambiguation`
  possible: title_hint `"sync"` on 2025-06-17 genuinely matches both
  "Client Sync" (Alice's own) and "Bob's Weekly Sync" (Bob's), so
  `find_meetings_to_cancel` returning both -- rather than silently
  filtering out the one Alice doesn't own -- is what lets the agent ask
  "which one?" instead of giving a dishonest "not found" for the one it
  filtered away.
- **`find_meeting_to_cancel`'s `title_hint` is matched as a plain
  substring of the real title**, the same simple matching
  `followup_tool.find_meeting` already uses -- no fuzzy matching. A live
  request once passed `title_hint: "sync meeting"` (closely paraphrasing
  the user's own words) against a real title that was just "Client
  Sync", found nothing, and incorrectly told the user no such meeting
  existed. Fixed by telling the model explicitly, in `scheduler.md`, to
  pass a short exact keyword likely to actually appear in the title
  ("sync", not "sync meeting"), and to retry with a different keyword or
  no `title_hint` at all before concluding nothing was found.
- **Cancellation confirmation is required unconditionally** -- per the
  user's explicit requirement, this is deliberately *stricter* than
  every other confirmation rule in this app. Booking already allows a
  same-message "book it now, I confirm" to skip asking, because the
  risk there is only ever "is this weekend/unusual time really wanted,"
  which the user has actually already answered by saying so. Cancelling
  is different: the user's own message can't possibly have confirmed
  the *specific* meeting (exact title/date/time/attendees) that
  `find_meeting_to_cancel` was going to find, because they wrote that
  message before seeing what it found. `scheduler.md` states this
  explicitly (an "I confirm, cancel it now" in the very first message
  doesn't count, because the user hasn't seen the specific match yet)
  and requires the next reply after a lookup to always be showing
  details and asking, never `cancel_event`, no matter how the request
  was phrased.
- **This one rule is prompt-enforced only, not code-enforced, and that
  is a known, accepted limitation, not an oversight.** Every other
  safety-critical check added to the Scheduler (already-passed time,
  attendee email format, and this feature's ownership + lookup-grounding
  checks) is code-enforced specifically because each is a fact
  computable from data already in hand within a single request. "Has a
  human genuinely seen and confirmed this specific meeting" is not that
  kind of fact -- it depends on there having been a real, separate prior
  turn, and `handle_scheduling_request` is a stateless function with no
  memory between calls (nor does `live_assistant.py` persist anything
  between CLI invocations). A hard code-level version of this guarantee
  would need to thread an explicit "the caller already showed this
  human this exact event_id and they said yes" token in from outside --
  real, buildable, but a deliberately separate scope decision from this
  feature, not something to bolt on silently. Until/unless that's built,
  this rule relies on the same prompt-compliance mechanism as every
  other soft rule in this app (weekend confirmation, ask-when-missing,
  never-invent) and was observed to hold in roughly 5 of 6 live-model
  runs while testing this exact scenario -- consistent with the
  documented general pattern that `temperature=0` reduces but does not
  eliminate live-model variance. `test_cancel_never_skips_confirmation_
  even_when_pre_confirmed` covers it and may occasionally need a re-run
  to confirm a real failure isn't just one unlucky sample, the same way
  `test_5_1_can_propose_moving_the_existing_meeting_when_asked` already
  does for an unrelated rule.
- Verified against the real account, not just the mocked fixture:
  looking up a real meeting and asking for confirmation without
  cancelling it (a real personal-errand meeting), and looking
  up a real meeting owned by someone else and correctly refusing
  (a real meeting organized by an unrelated third-party address) --
  both read the real `organizer.email` and `id` fields off actual
  Calendar API data, not fixture data.

## Creating a recurring meeting

The natural counterpart to cancellation, requested right after: "every
day at 10am" needed `create_event` itself to support a repeating
series, not a new tool -- Google Calendar's own recurrence support
(an `RRULE` string on the event body) already does the heavy lifting.

- **`create_event`'s tool schema gained an optional `recurrence`
  object** (`frequency`, optional `weekdays`, required `until`) instead
  of asking the model to write RRULE syntax itself -- the same
  "structured fields in, Python computes the actual string" pattern
  used for weekday-to-date resolution elsewhere in this file.
  `scheduler._build_rrule` turns those fields into the real
  `RRULE:FREQ=...` string(s); a malformed recurrence rule can't reach
  either the mocked store or the real API, because the model never
  writes one. `until` is pushed one calendar day later and expressed in
  UTC so the user's real last day is never clipped by a timezone-behind-
  UTC cutoff.
- **An end date is mandatory, not optional** -- `scheduler.md` treats
  "how long should this repeat?" as missing information exactly like a
  missing attendee or duration, and explicitly forbids creating an
  open-ended series even if the user's phrasing suggests "forever."
  Covered live by
  `test_recurring_request_without_an_end_date_is_asked_for_not_booked`.
- **`create_event`'s (and `create_event_via_google_calendar`'s) 6th,
  optional `recurrence` parameter defaults to `None`**, so a plain
  one-off booking is completely unaffected -- covered by
  `test_create_event_without_recurrence_records_no_recurrence_field`,
  which asserts a normal booking's record doesn't even gain the key.
  `CreateEventFn`'s type alias was loosened to `Callable[..., dict]`
  since a fixed-arity `Callable[[...], dict]` can't express an added
  optional parameter cleanly, and the existing
  `test_create_event_fn_override_is_used_instead_of_the_mock` fake
  needed a matching `recurrence=None` parameter added, since
  `_dispatch_tool` now always passes all 6 positional arguments.
- **A real request surfaced an independent, previously-undocumented
  bug while testing this**: two different live tests that never pass an
  explicit `now` (so it defaults to midnight) started intermittently
  claiming a plain same-day time like "11:30" had "already passed" --
  impossible, since midnight is earlier than every other time that same
  day. The likely cause: the bare string `"00:00"` in the system prompt
  being momentarily misread as a different, later time. Fixed by making
  the current-time line explicit and self-explanatory rather than a
  bare timestamp -- it now says "(midnight, the very start of today)"
  when `now` is exactly midnight, and adds a blunt "any later time
  today has NOT happened yet" instruction right next to it. Both
  previously-flaky tests passed 6/6 afterward. This is the same lesson
  as the earlier day-name and timezone fixes: don't assume a plain,
  unadorned value (a bare date, a bare "00:00") reads as unambiguously
  as it looks to a human -- spell out what it means when there's any
  room for misreading.
- **Verified against the real account**: a real recurring series
  ("Design Team Standup," weekdays at 10:00, through Dec 31, 2026) was
  created and independently checked via the Calendar API afterward --
  occurrences land on Fri 09-11, Mon 09-14, Tue 09-15 (correctly
  skipping the 09-12/09-13 weekend), all sharing one `recurringEventId`,
  and the last occurrence is exactly Dec 31, 2026 with nothing spilling
  into January 2027.
- **A real request also surfaced that only the FIRST occurrence's
  availability is ever checked** -- `check_availability` (mocked and
  real) has no concept of "check every future date this series would
  land on," so a conflict on some *later* occurrence (not the first) is
  never caught automatically. This surfaced for real: the first
  attempted first-occurrence date was conflict-free, but a specific
  later weekday in the series wasn't, and the agent correctly surfaced
  that one date's conflict and asked how to proceed (book the whole
  series at the requested time anyway, since only that one specific day
  has a competing meeting to sort out separately, vs. shifting every
  occurrence to a different time) rather than silently booking over it
  or silently skipping that occurrence. This is a real, disclosed scope
  limit worth knowing about, not a bug to be fixed reflexively: checking
  every future occurrence of a series through its end date would need
  its own iteration logic, which wasn't built since nothing in this
  request asked for it.
- **Relative day references ("starting tomorrow") were noticeably less
  reliable than an explicit date** for this specific request, once a
  conflict-resolution back-and-forth was already underway -- resolving
  to the wrong weekday once mid-conversation. Restating the first
  occurrence as an explicit calendar date fixed it immediately. This
  matches this project's running theme (LLM date arithmetic is
  unreliable) closely enough that it's not treated as a new,
  separately-fixed bug -- the existing 7-day lookup table already
  exists for exactly this reason -- but it's a good reminder that a
  multi-turn conflict negotiation is exactly the kind of longer,
  more-loaded context where relying on relative phrasing is riskiest,
  and an explicit date is worth reaching for proactively rather than
  only after something goes wrong.
- **Booking that same recurring series turned up a second real bug, more
  serious than the first**: the series was created without ever
  checking availability for its own first occurrence, silently
  double-booking over a real job interview the next morning at the same
  time. Root cause: an operator instruction to "book it now, don't ask
  again" (used to push past the earlier per-occurrence conflict
  back-and-forth) apparently made the model skip `check_availability`
  entirely for the explicit first-occurrence date it had just been
  given, not just skip re-confirming. This wasn't caught by any of this
  feature's tests because none of them combined an explicit,
  emphatically-worded "just book it" instruction with a genuinely
  conflicting first occurrence -- a gap in test coverage, not just a
  prompt gap, worth remembering: emphatic "stop asking and act"
  instructions are exactly the kind of pressure most likely to make a
  model skip a step it would otherwise reliably take, so that
  combination deserves its own test the way "confirmation already
  given" now does for the weekend/unusual-hour rule. Resolved live by
  cancelling just the one conflicting Sept 11 occurrence via the
  cancellation feature above, leaving the rest of the series untouched
  -- verified by re-reading the real Calendar API afterward.
- **That resolution exposed a genuine architectural deadlock in the
  cancellation feature, discovered only by actually trying to use it
  through two separate real `live_assistant.py` commands**: the
  "always confirm, never in the same reply as the lookup" rule means a
  SECOND stateless CLI invocation -- the one meant to supply the
  confirmation -- has no memory of the first invocation's lookup either,
  so it would call `find_meeting_to_cancel` fresh and be blocked from
  calling `cancel_event` in that same reply too, forever. Cancellation
  could never actually complete through this CLI at all, for anyone,
  no matter how it was worded across as many separate commands as you
  like. Fixed with the minimum viable amount of real session state:
  `handle_scheduling_request` gained a `pending_confirmation` parameter
  (seeds `looked_up_event_ids` with that meeting's `event_id` and adds
  a system message explaining a prior turn already found and showed it)
  and a `cancellation_candidates` output list (mirroring `cancelled`)
  that a caller can inspect to know whether exactly one meeting was
  found and shown, awaiting confirmation. `live_assistant.py` persists
  that single candidate to a small gitignored
  `.pending_cancellation.json` after a "found it, please confirm" reply,
  passes it back in on the very next invocation, and clears it the
  moment a cancellation actually completes (or the flow resolves any
  other way -- ambiguous matches, an ownership refusal, an unrelated
  request). This is a real trade-off, not a free win: it makes the
  guarantee only as strong as "the next command run against this
  project directory is trusted to be the same user's genuine reply" --
  fine for a single-user local CLI tool, but not a substitute for real
  multi-user session/auth handling if this became a shared service.
  Covered offline by
  `test_pending_confirmation_lets_a_fresh_call_complete_a_cancellation`,
  and verified for real: found-and-shown in one command, confirmed and
  actually cancelled in the next, pending file correctly written then
  cleared at each step.

## "Needs attention today" silently became a date-range query

A real request for "what emails need my attention today" returned "none"
even though the same inbox, asked about with "what needs my attention?"
(no "today"), correctly surfaced two items -- a real, user-visible
inconsistency between two questions that should mean the same thing.

- Root cause: `email.md`'s period-detection rule listed "today" as one
  of the words that triggers the date-range branch (`get_emails` with
  `start_date`/`end_date`), the same branch "what did I get last week"
  uses. But "needs my attention TODAY" and "what came in today" are
  different questions -- the first is asking about currently-pending
  items (regardless of when they arrived), using "today" the way a
  person says "what do I need to deal with today," not as a filter on
  receipt date. The rule's keyword-based trigger couldn't tell them
  apart, and picked the wrong one: an email that had been sitting unread
  for days (which is exactly the kind of thing that "needs attention")
  got excluded because it wasn't *received* today.
- Fixed by making the branch decision about the request's own framing
  (action/pending vs. history), not about which words happen to appear
  in it -- `email.md` now says explicitly that "needs attention" framing
  wins even when "today"/a period word is also present, and only a
  genuine "what happened/arrived in this period" question triggers the
  date-range call. Covered by
  `test_needs_attention_today_still_uses_the_unread_only_call` in
  `test_email.py`, and verified live against the real inbox.
- **A second, independent classification issue surfaced while verifying
  the fix, not caused by it**: an automated Jira notification ("Finish
  creating work item SCRUM-1") was inconsistently marked action-required
  across otherwise-identical requests -- a real run-to-run classification
  variance the FYI rule was already supposed to prevent (the rule already
  says "anything from an automated ... notification system" is FYI), but
  a tool's own nudge phrased as an imperative directed at the user
  ("finish creating...") apparently reads enough like a personal task to
  occasionally pull it into action-required anyway. Clarified the FYI
  bullet to name this exact shape explicitly (a tool nudging the user
  about its own system, e.g. "finish creating X," "complete your
  profile") and to state directly that imperative-sounding phrasing
  isn't what makes something action-required -- a specific named PERSON
  has to be the one waiting. Verified consistent (FYI) across the next
  two live runs after the change; per this project's established
  lesson about this exact rule (three earlier tuning rounds, one of
  which backfired badly with invented content), the fix was a narrow,
  literal clarification -- not a new worked example -- specifically to
  avoid repeating that failure mode.

## "Needs attention" was blind to mail that had already been opened

A real recruiter email (a real recruiting company, asking for notice-period
confirmation and a resume) never showed up in any "needs attention"
answer -- not because it was misclassified, but because it had already
been read, and "needs attention" only ever looked at unread mail.
Opening an email doesn't mean it's been dealt with; the app had no
concept of "read but still pending."

- `email_tool.py` gained `needs_attention_pool(emails, today,
  recent_days=5)`: every unread email regardless of age, PLUS every
  email read within the last `recent_days` days. Unread mail is never
  time-limited -- being unread is already the strongest possible signal
  that nothing's been done, so an old unread email must never drop out
  of "needs attention" just because it's old (that would make the
  feature worse at its one job). The recency window exists only to
  widen what *read* mail also gets considered.
- This only widens the CANDIDATE pool -- every email in it still goes
  through the same "is a specific named person genuinely waiting on the
  user" test as before. Being recently read doesn't automatically make
  something action-required, and doesn't exempt it either.
- `email.py`'s `get_emails` tool schema gained an `include_recently_read`
  boolean. A plain "what's unread" question still gets the original,
  strict unread-only call, unchanged -- only "needs attention"-style,
  action-oriented requests should set this true. `email.md` states this
  distinction explicitly, and separately reinforces (building on the
  earlier "today" fix above) that a period word like "today" in a
  needs-attention request never means "received today" -- it can pull
  in unread-anytime and recently-read mail just the same.
- **The window size (3, then 5) and shape were negotiated with the user
  before writing any code**, not decided unilaterally: an initial
  proposal (a full rolling 7-day "work week" window, with a
  user-specifiable anchor date for "as of last Tuesday"-style questions)
  was scoped back down to a plain 5-day window on the user's own
  request, after discussing the trade-off (a wider window risks
  resurfacing mail the user already handled through some channel outside
  this app, since read/unread is the only signal available -- there's no
  "resolved" state). A separate, real ambiguity was resolved explicitly
  before implementing: does the recency window apply to unread mail too
  (dropping old unread mail past the cutoff), or only widen what read
  mail is included? The user confirmed the latter (option "A" above) --
  worth remembering if this window is ever revisited, since the wrong
  choice would have quietly made "needs attention" less reliable at the
  one thing it's most relied on for.
- New fixture entry: an email from "Henry (Acme Recruiting)", marked
  `read: true`, dated the same day as the fixture's `TODAY`, asking
  specific questions (notice period, resume) -- mirrors the real recruiter
  email closely enough to give offline coverage of exactly this failure
  shape without needing real credentials. Covered by
  `needs_attention_pool`'s own unit tests in `test_email_tool.py`, and
  live by `test_needs_attention_surfaces_a_recently_read_but_unactioned_email`.
  Verified for real: both the real recruiter email and a second read-but-pending
  interview-scheduling email (from the actual account) now surface
  correctly.
- **Widening the pool to include recently-read mail immediately
  surfaced a second, independent, previously-invisible real bug**: one
  of the "needs attention" results was a reply the user had SENT
  themselves ("Re: Product Owner," sender showing the user's own
  address), which is a logical impossibility to flag as action-required
  -- you can't be waiting on your own reply. Root cause:
  `load_emails_in_range_from_gmail`'s Gmail search query was a bare
  `after:`/`before:` range with no folder scope at all, which matches
  Gmail's entire "All Mail" view, sent messages included. This had
  always been true of that function -- it was never actually correct --
  but had no way to surface as a visible symptom until a query that
  both (a) uses this date-ranged function and (b) considers *read* mail
  landed added; before `needs_attention_pool` existed, nothing that
  called this function ever showed read mail to the user in a way that
  would look wrong. Fixed by adding `in:inbox` to both this query and
  (defensively, for robustness rather than because it was observed
  broken) the plain `is:unread` query -- Gmail already marks a user's
  own sent mail as read automatically, so the unread query happened not
  to be affected in practice, but scoping it explicitly is still the
  correct thing regardless of which query currently happens to dodge a
  particular bug. Covered by `test_gmail_integration.py` (the service is
  mocked, checking the constructed query string rather than needing real
  credentials) and verified for real: the self-sent email is gone from
  "needs attention," and a third genuine item (a real recruiter/HR
  sender, previously crowded out or masked by the bad entry) now
  correctly appears instead. General lesson: a latent bug in a
  lower-level integration function can stay invisible for a long time
  if nothing calling it ever exercises the exact combination of
  conditions that makes it visible -- worth treating "this function
  hasn't caused a visible problem" as different from "this function is
  correct," especially right after a change that starts using an
  existing function in a new way.

## "Needs attention" couldn't tell "opened it" from "opened it and replied"

The user pointed out, after actually using the feature above, that an
email flagged as needing attention had already been personally replied
to -- read/unread was never going to be enough by itself; Gmail marks
the original message read regardless of whether the user then sent a
reply.

- `Email` gained a `replied: bool = False` field (default, so nothing
  existing changed). `needs_attention_pool` excludes any `replied`
  email outright, regardless of how recently it was read -- a real
  reply already sent is a stronger, more direct signal than recency
  ever was.
- The mocked fixture and pure `email_tool.py` functions have no way to
  represent a real reply having been sent, so `replied` is always False
  there -- this is exclusively a real-Gmail-integration concern.
  `gmail.py` determines it via a genuine Gmail capability: a thread's
  messages come back from the API in chronological order, so if the
  LAST message in an email's thread is from the user's own address
  (fetched once via `getProfile`) and isn't the message being checked,
  the user has replied since. Only checked for messages already marked
  read -- an unread message can't possibly have a later reply, since
  Gmail marks a message read the moment its thread is acted on, so
  skipping the thread lookup there keeps the extra per-message API call
  scoped to where it can actually matter.
- This was scoped and confirmed with the user before writing any code
  (the same negotiation pattern as the window-size decision above),
  since it adds a real cost: one extra Gmail API call per already-read
  candidate email, not just per request. For a personal, single-user
  CLI tool this is a reasonable trade for correctness; it would need
  reconsidering (e.g. checking only the specific candidates
  `needs_attention_pool` would otherwise include, rather than every
  fetched message) if this were ever used somewhere latency or API
  quota mattered more.
- Covered by three mocked-service tests in `test_gmail_integration.py`
  (a read message with no reply, a read message with a later reply from
  the user's own address, and confirmation that an unread message never
  even triggers the thread lookup) and one pure-function test in
  `test_email_tool.py`. Verified for real: the previously-flagged
  "Product Owner" email (which the user had genuinely already replied
  to) is now correctly excluded from "needs attention."

## "Same time as my existing meeting" -- a new booking reference pattern

A real request, "schedule a meeting at the same time as my existing
standup," worked out of the box in one sense the model wasn't
explicitly built for: with no dedicated "look up a meeting's time"
tool, it reused `find_meeting_to_cancel` (the cancellation lookup) to
find the standup's real time instead of guessing one. That's now
documented as an intended second use of that tool in `scheduler.md` and
its tool-schema description, not an accident to leave undocumented.
Two real bugs surfaced from actually using this, in sequence.

- **Bug 1: the new meeting silently inherited the referenced meeting's
  attendees.** "Same time as my standup" only specifies a TIME -- who
  the new meeting is with is completely separate, still-missing
  information, the same as any other "never guess an attendee"
  situation. The prompt already said this once, but a real request
  still got it wrong: reinforced with a reminder injected right after
  `find_meeting_to_cancel` runs (the same "restate it right at the
  decision point" fix already proven for the conflict-naming rule),
  telling the model explicitly not to reuse the found meeting's
  attendees or title.
- **Bug 2, found immediately after fixing bug 1**: the fix above made
  the model stop and ask for attendees -- but then it stopped calling
  `check_availability`/`propose_alternative_slots` at all, so it never
  surfaced that this always has a real, knowable conflict even before
  attendees are known: {{USER_NAME}} is already in the meeting being
  referenced, so {{USER_NAME}} personally can't also attend a new one
  at that exact time, regardless of who else it's with. This is the
  same "don't handle two applicable concerns as separate turns" pattern
  that has recurred multiple times in this project (day-ambiguity vs.
  missing-info, weekend-hour vs. missing-info) -- just a new pair this
  time (a guaranteed self-conflict vs. a missing attendee). Fixed by
  extending the same reminder: check availability for {{USER_NAME}}
  alone at that time (guaranteed to conflict, by construction), name
  the conflict, propose alternatives for {{USER_NAME}} alone, AND ask
  for attendees -- all in the same reply.
- **A third issue, this one a real regression caught before it shipped
  broken**: the first version of that reminder tried to have the model
  self-condition on "only follow this if the lookup was for a NEW
  booking, not an actual cancellation" inside the reminder text itself.
  That failed outright -- running the full suite immediately after
  revealed it had hijacked two unrelated, genuine cancellation requests
  (an ownership-refusal case and an ambiguous-match case) into being
  treated as booking-reference lookups instead, since the model didn't
  reliably obey a conditional embedded in an imperative instruction.
  Fixed by moving the decision out of the prompt entirely:
  `handle_scheduling_request` now computes `is_cancel_intent` once, in
  Python, from the request's own wording ("cancel"/"delete"/"remove")
  before any tool is ever called, and only injects the reminder when
  that's false. This is the same lesson as the "always confirm before
  cancelling" limitation documented above, from the opposite direction:
  where a decision CAN be made deterministically from data already in
  hand (here, the original request text), do it in code rather than
  trusting the model to re-derive the same distinction correctly every
  time -- reserve prompt-only enforcement for the things that genuinely
  can't be decided any other way.
- Covered by two live tests: `test_same_time_as_existing_meeting_still_
  asks_for_attendees` (bug 1) and `test_same_time_as_existing_meeting_
  also_surfaces_the_self_conflict` (bug 2 -- conflict named, a real
  alternative time present, and attendees still asked for, all in one
  reply). The intent-gating fix (bug 3) has no dedicated new test of its
  own -- it's covered by the existing cancellation tests it broke and
  then fixed passing again. Verified live end-to-end against the real
  "Design Team Standup" series, including the day-disambiguation case
  ("...on Monday").
- **Bug 4, found immediately after fixing bugs 1-3**: asking "at the
  same time as my existing standup" with no day at all silently picked
  the next upcoming occurrence's date, without ever asking -- the user
  caught this directly ("I didn't say which day to consider"). "Same
  time as X" only pins down a TIME; a recurring reference meeting exists
  on many days, so the DAY is genuinely still unresolved unless the
  request also named one. Fixed by treating a MULTIPLE-match result from
  `find_meeting_to_cancel` as a signal (computed in code, from the
  lookup's own result -- a lookup correctly scoped to a day the user did
  name returns exactly one match) that the day is missing information
  too, the same as the attendee: a separate reminder branch now tells
  the model to ask which day AND who it's with together, without
  guessing a date or checking availability yet. `scheduler.md` also now
  explicitly tells the model to pass a resolved `date` to
  `find_meeting_to_cancel` whenever the user did name a day, so a
  day-scoped lookup doesn't come back ambiguous by accident.
- **Bug 5, found immediately after fixing bug 4**: once the day-ambiguity
  fix landed, a request that already included a real attendee (a real
  email address) still got asked "who should it be
  with?" -- the reminder's "ask for attendees"/"ask which day"
  instructions were unconditional, not checked against what the request
  already said. Fixed by adding an explicit "unless the user's message
  already answered this" carve-out to both reminder branches. Lesson,
  same shape as several fixes above: a reminder injected to fix one
  gap needs the same "don't ask for what's already given" discipline as
  the rest of this prompt, not just the top-level rules -- a targeted
  fix can regress an orthogonal, already-working behavior if it's
  phrased as an unconditional instruction.
- Bugs 4 and 5 are covered by `test_same_time_as_a_recurring_meeting_
  also_asks_which_day` and the (renamed) `test_same_time_as_existing_
  meeting_still_asks_for_attendees`/`test_same_time_as_existing_meeting_
  also_surfaces_the_self_conflict` (both now say "...today" to resolve
  the day deliberately, isolating the attendee-question test from the
  day-ambiguity one). Verified live end-to-end, including booking the
  real meeting through to completion once both day and attendee were
  supplied across separate messages.

## A third email category: Payment/Deadline Reminder

The user asked why a real electricity bill and credit card bill,
clearly "requiring attention," never showed up under "needs my
attention." They didn't -- correctly, under the rule as it existed: an
automated billing-system email was unconditionally FYI, and the
"needs action" test (a specific named person waiting on your reply) has
no person in it at all. A bill has a real due date and a real
consequence for missing it, but it's a different KIND of "needs
attention" than the rest of this agent tracks -- explicitly scoped and
approved by the user as a new feature before building it, rather than
folded into the existing Action Required category.

- **A third category, not a second meaning for an existing one.**
  Action Required stays exactly what it was (a named person waiting on
  a reply); Payment/Deadline Reminder is new and separate: an automated
  billing/subscription/utility email that states a CONCRETE due date or
  deadline. Blending the two would have muddied a category that already
  works and has real test coverage -- a bill isn't "someone waiting on
  you," so it doesn't belong inside that test.
- **Narrow by design, given this file's history.** `email.md` already
  went through three rounds of classification tuning for Action
  Required (documented above), one of which backfired by inventing
  content. Payment/Deadline Reminder is deliberately conservative: it
  requires the email to state an actual due date/deadline in its own
  text, and explicitly excludes financial MARKETING from the same kind
  of sender (a pre-approved loan or card offer stays FYI even though
  it's "financial" and even from a real bank/biller) -- the bar is a
  real payment obligation with a stated deadline, not "mentions money"
  or "is from a bank." Amount and due date must be quoted exactly as
  the email states them, never computed or estimated.
- **"Needs attention" now surfaces both non-FYI categories together**,
  clearly labeled which is which; a plain summary groups into three
  sections instead of two. Neither change touches how `get_emails`
  itself is called (still governed entirely by the unread/recently-read/
  date-range rules already in place) -- this is purely a classification
  and presentation change layered on top of the existing pool.
  - Fixture additions: a real-shaped electricity bill ("$84.50... due
  on June 20") and a bank loan-marketing email ("pre-approved personal
  loan... up to $10,000"), both unread -- the second exists specifically
  to test the boundary (financial, but no due date, so it must stay
  FYI). Covered by `test_needs_attention_includes_payment_reminders_
  but_not_bank_marketing` and `test_summary_separates_payment_reminders_
  from_action_required` in `test_email.py`. Verified against the real
  inbox: two real bills (₹1953.00 due 15 Sep 2026; ₹48062.69 due 15 Sep
  2026) surfaced under a distinct "Payment/Deadline Reminders" heading
  with their exact amounts and dates, cross-checked against the actual
  email bodies to confirm nothing was invented; the real YES BANK loan-
  offer marketing email correctly stayed out of both non-FYI categories.

## Classifying a large "needs attention" pool in one call silently dropped items

A real, genuinely action-required email (a recruiter's follow-up)
stopped showing up under "needs attention" -- not because of a
classification-rule gap (asking about it directly still correctly said
it needed attention), but because the candidate pool itself had grown
to 49 emails, and a single call asked to classify all of them at once
was dropping one from its response. This is a known LLM long-list
completeness limit, not a prompt problem, and it had never been visible
before because the pool was always small in every prior test and every
earlier real check.

- **Fixed by batching, not by asking the model to try harder.** A new
  `email_classify.md` prompt and a forced-tool-call `classify_emails`
  schema (`tool_choice` pinned to it, so every batch call returns
  structured data, never freeform text) classify at most
  `_CLASSIFY_BATCH_SIZE` (15) emails per call; `email.py` splits the
  pool into batches, classifies each independently, and merges the
  results in Python. A `for` loop over a list can't silently skip an
  item the way a single freeform summary call could -- this is the same
  "make the aggregation step incapable of the failure" principle as the
  `cancel_event` grounding and past-time booking checks elsewhere in
  this app, applied to a classification task instead of a booking one.
  Verified directly: traced the real 49-email pool split into 4 batches,
  and every batch returned exactly as many classifications as emails
  given it.
- **A second, real classification-consistency bug surfaced immediately
  after fixing the first one**: the dropped recruiter email, once always
  included in its own batch, was then INCONSISTENTLY classified --
  sometimes FYI, sometimes action-required, across separate calls at
  the same temperature=0. The email is long and template-heavy (a full
  job description, a 20-question form) with the actual ask (confirm
  notice period, send a resume) buried under a lot of generic
  boilerplate; the model was sometimes discounting the real ask because
  of the surrounding filler. Fixed by adding an explicit rule to
  `email_classify.md`: a long, boilerplate-heavy email is STILL
  `action_required` if a specific named person's concrete ask is
  genuinely embedded in it -- judge by the most specific request in the
  email, not by how much generic content surrounds it. Verified 4/4 on
  the real email afterward. This rule was then generalized (at the
  user's explicit request, after they pointed out it was worded only
  around one example -- recruiting emails) to name no single email type
  at all, since the same shape (mostly boilerplate, one real ask inside)
  turned up next in a completely different kind of email (an interview-
  logistics email with meeting-recording/policy filler around a real
  "confirm you can attend").
- **Residual, accepted limitation**: even after both fixes, an
  individual borderline email can still occasionally flip categories
  run to run -- this is the same "temperature=0 reduces but doesn't
  eliminate variance" characteristic documented elsewhere in this file,
  not something either fix was meant to fully eliminate. The batching
  fix guarantees completeness (every email gets *a* classification, none
  silently dropped); it does not guarantee every individual judgment
  call is identical across repeated real-world runs.
- **A separate, real regression was introduced by the batching fix
  itself, caught by the existing test suite** (not a live-only
  finding): "Summarize my emails" sometimes also chose the
  recently-read pool (reasonably -- a summary wants the fuller picture
  too), which routed it through the same needs-attention formatter --
  but that formatter intentionally omits FYI, while a full summary must
  still show it. `_format_needs_attention` gained an `include_fyi` flag,
  decided in `answer_email_request` from the request's own wording
  (`"summar" in request.lower()`) the same way `is_cancel_intent` is
  decided elsewhere in this codebase -- not from which `get_emails`
  variant happened to be called, since that's a different question
  (which pool) from how to format the answer (which categories to
  show). `test_t7_summary_groups_by_urgency_and_separates_action_from_
  fyi` (an existing, unrelated-looking test) is what caught this in the
  next full suite run.
- Covered by `test_needs_attention_batches_large_pools_and_merges_
  without_loss`, `test_format_needs_attention_separates_categories_and_
  never_drops_items`, `test_format_needs_attention_includes_fyi_when_
  requested`, and `test_format_needs_attention_handles_nothing_pending`
  in `test_email.py` -- all offline and instant, since batching/merging
  is pure Python once the (mocked) classification calls are scripted.

## Reading a meeting's real notes, not just the Calendar description

The Follow-Up Agent's real integration always assumed Google Calendar's
`description` field was the only possible notes source, since Calendar
itself has no native meeting-notes field. A real request ("what
happened in today's meeting") got "no notes recorded" -- correct
for `description` (empty), but wrong in spirit: the user pointed out
the event actually had an attached "Notes by Gemini" Google Doc, created
automatically because Google Meet's note-taking was on for that call.

- **A real Google Doc is a legitimate second notes source**, reachable
  through the event's ordinary `attachments` field (a `fileUrl` pointing
  at `docs.google.com/document/d/<id>/...`) -- nothing calendar-specific
  about it. `get_meeting_record_from_google_calendar` now looks for that
  attachment first (`_find_attached_doc_id` + `_fetch_attached_doc_notes`
  in `google_calendar.py`), fetches the Doc's plain text via the Google
  Docs API, and falls back to `description` exactly as before when
  there's no such attachment, the fetch fails for any reason (missing
  scope, deleted doc, no permission), or the doc's text is empty. This
  is additive, not a replacement: a meeting with only a typed
  `description` and no Gemini doc behaves exactly as it always did.
- **Requires a new OAuth scope**
  (`https://www.googleapis.com/auth/documents.readonly`), added to
  `google_auth.SCOPES`, plus a new `docs_service()` alongside
  `calendar_service()`/`gmail_service()` in `google_auth.py`. A scope
  addition doesn't retroactively invalidate an existing valid
  `token.json` -- Google's client only checks expiry, not scope
  coverage -- so `google_auth_setup.py`'s instructions now say to
  delete the old token and re-run the script for a fresh consent
  screen; without that, the Docs API call would fail at runtime with an
  insufficient-scope error (caught and treated as "no doc," same as any
  other fetch failure, so this degrades gracefully rather than breaking
  the whole follow-up request).
- Covered by three mocked-service tests in
  `test_google_calendar_integration.py`: notes correctly extracted from
  an attached doc, notes correctly falling back to `description` when
  there's no attachment (and the Docs API is never even called in that
  case), and notes correctly falling back to `description` when the Doc
  fetch raises. All three mock `docs_service` the same way existing
  tests already mock `calendar_service` -- no real credentials needed.

## A real follow-up email had broken formatting and needless placeholders

The user asked to review an actual sent follow-up email and
found it unprofessional -- pulling the raw sent message confirmed why:
it went out as **literal** `**Meeting Notes:**` asterisks, a generic
"Dear Team" greeting for a two-person meeting, and a signed-off
"[Your Name]" that was never filled in. Three separate, real problems,
found by reading the actual bytes Gmail sent, not just the confirmation
text this app printed back.

- **Root cause of the broken formatting**: `send_email_via_gmail` built
  a plain-text `MIMEText`, but `followup.md` has the agent draft in
  Markdown (bold, bullet lists) -- Gmail renders plain text literally,
  so `**bold**` shows up as literal asterisks in the recipient's inbox,
  never as bold. Fixed by converting the Markdown body to real HTML
  (`_markdown_body_to_html` in `gmail.py` -- a small, targeted
  converter for just what these emails actually contain: bold and
  bullet lists, not a general-purpose Markdown parser) and sending that
  as `MIMEText(html, "html")` instead. The dict `send_email_via_gmail`
  returns still carries the original Markdown in `body`, matching what
  the draft already showed the user -- only the bytes actually
  transmitted changed.
- **Root cause of the placeholder signature**: `run_followup_agent` had
  no `user_name` parameter at all, so the agent had no real identity to
  sign off as and fell back to a bracketed placeholder. Fixed the same
  way the Scheduler Agent's `{{USER_NAME}}` already works:
  `followup.md` gained the placeholder, `load_system_prompt` gained a
  `user_name` argument, and `live_assistant.py`'s `_follow_up` passes
  `get_authenticated_user_email()`, the same real identity the
  Scheduler already uses. `followup.md` also now explicitly forbids any
  bracketed placeholder text in a body about to actually send, and asks
  for a greeting that names one or two real recipients instead of a
  generic "Dear Team."
- **A related, user-reported issue surfaced right after this fix
  shipped**: the corrected email's recipients still included the
  sender's own address. Root cause: `followup.md`'s existing rule said
  recipients must exactly match the meeting's real attendees -- true,
  but the meeting's real attendee list (correctly) includes the sender
  themselves, and nothing carved out that case. The user had already
  flagged this same preference once before, for a different follow-up
  ("send it without the CC to myself"), so this was fixed as a durable
  default rather than a one-off: `_dispatch_tool`'s `send_followup_email`
  branch now filters `{{USER_NAME}}` out of `recipients` in code (the
  same defense-in-depth pattern as the Scheduler's ownership and
  past-time checks -- this needed to hold every time, not just when the
  model remembers to exclude itself), and refuses outright (an `error`
  result, nothing sent) if doing so would leave zero recipients, e.g. a
  meeting where the sender was the only other named attendee besides
  whoever's being excluded.
- Covered by `test_markdown_body_to_html_converts_bold_and_bullets`,
  `test_markdown_body_to_html_escapes_special_characters`, and
  `test_send_email_via_gmail_sends_html_not_raw_markdown` in
  `test_gmail_integration.py` (the last one decodes the actual raw MIME
  bytes that would be sent and asserts real `<b>`/`<li>` tags, not
  `**`), plus `test_send_followup_email_excludes_the_sender_from_
  recipients` and `test_send_followup_email_refuses_when_sender_is_the_
  only_recipient` in `test_followup.py`. `test_6_2_send_uses_the_
  meetings_exact_attendees` (an existing live test) was updated to
  assert Alice's exclusion rather than her inclusion, since the
  underlying rule it was checking changed. Verified against the real
  account: sent a corrected follow-up and read back the raw HTML body
  (real `<b>`/`<ul>` tags, personalized greeting, no placeholder), then
  sent again and confirmed via the real Gmail message that the
  recipient list no longer includes the sender.

## A follow-up email you sent to yourself showed up as someone else's request

A re-check of "needs attention" surfaced the earlier (pre-fix)
follow-up email -- which had genuinely been delivered to the
user's own inbox, since it was sent to them as a recipient before the
sender-exclusion fix above existed -- as if the display name on the
user's own address were a separate person waiting
on a reply. A person can't be waiting on a reply from themselves; this
is a different case from the earlier `in:inbox` fix, which only kept
the Sent folder itself from leaking in -- this email was already a
real, legitimately-delivered inbox message.

- **Fixed with a code-level filter, not a classification rule**, since
  "is this email from the same address as the authenticated user" is a
  fact computable up front, not a judgment call -- the same reasoning
  already applied to ownership and past-time checks elsewhere in this
  app. `answer_email_request` (and `answer_digest_request`, which
  delegates to it) gained an `own_email: str | None = None` parameter;
  when given, any email whose `sender` contains that address is
  filtered out of the needs-attention pool before classification ever
  runs, rather than trusting the classifier to notice the sender is the
  user. Defaults to `None` (no filtering) so every existing mocked test
  -- which has no real "own address" concept at all -- is unaffected.
  `live_assistant.py`'s `_email` and `_digest` pass
  `get_authenticated_user_email()`, the same identity already used
  elsewhere (Scheduler, Follow-Up).
- **A second, independent issue surfaced in the same check**: an
  automated Topmate notification ("[Action Required] Your Google
  Calendar sync with Topmate has been undone") was misclassified as
  action-required, even though the existing FYI rule already covers
  "anything from an automated ... notification system" -- the literal
  bracketed "[Action Required]" text in the subject line appears to
  have overridden the semantic judgment, the same failure shape as the
  earlier Jira "finish creating your work item" incident, just
  triggered by a different specific phrase this time (confirming that
  fix's wording didn't fully generalize). Fixed by adding an explicit
  callout to `email_classify.md`: a subject line literally containing
  "Action Required," "Urgent," "Important," or a bracketed tag is the
  SENDER'S own word choice, not evidence -- automated systems use that
  language on routine notifications constantly, and it must never
  substitute for the actual "specific named person waiting" test.
- Covered by `test_needs_attention_excludes_self_sent_mail_when_own_
  email_given` and `test_needs_attention_keeps_self_sent_mail_when_own_
  email_not_given` (the latter confirming backward compatibility) in
  `test_email.py`. `test_digest.py`'s `test_answer_digest_request_
  combines_both_agents` needed its `fake_email` stub updated to accept
  the new `own_email` keyword, since `digest.py` now passes it through
  -- caught immediately by the existing test suite, not a live-only
  finding. Verified against the real inbox: both the self-sent follow-
  up and the Topmate notification stayed correctly excluded across
  three consecutive real checks.

## Observability (Langfuse)

`live_assistant.py` and `harness.py` are traced to Langfuse; `pytest` is
not, in the sense that it never has real credentials to send anything
anywhere -- but the exact same Langfuse-instrumented code now runs
during every test too (see "traced code is baked into the functions
themselves" below), which is deliberate, not an oversight. Set up using
the official Langfuse skill (`github.com/langfuse/skills`, installed
under `.claude/skills/langfuse/` in this repo) rather than implemented
from memory, per that skill's own first principle: "Documentation
First -- never implement based on memory."

- **The seam this plugs into already existed**, the same way the real
  Google Calendar/Gmail integration did: every agent already takes an
  injected `client: ChatClient` (`.create(**kwargs) -> response`), and
  `live_assistant.py`'s `main()` is the ONE place that ever constructs a
  real one (`client = OpenAI().chat.completions`). Swapping that single
  construction for `langfuse.openai`'s drop-in `OpenAI` class -- same
  `.chat.completions.create()` shape, so every existing agent module is
  completely unaffected -- means every LLM call made anywhere in this
  app during a real run is captured automatically. This was the
  entirety of the FIRST pass; a second, later pass (below) went further
  and instrumented `run_orchestrator()`, `route_request()`, all four
  agent functions, and four individual tool calls directly, at the
  user's explicit request, and `harness.py` was wired up as a second
  traced entry point alongside `live_assistant.py`.
- **`langfuse` is a core dependency now, not an optional extra.** It
  started as an opt-in `observability` extra in `pyproject.toml` (only
  `live_assistant.py` imported it), but once tracing moved into
  `calendarmate.observability` and got imported by `pipeline.py`,
  `orchestrator.py`, and all four agent modules, `langfuse` became
  something `pytest` needs installed just to import these modules at
  all -- so it moved into `dependencies`, alongside `openai`.
- **`tests/conftest.py` explicitly forces `LANGFUSE_TRACING_ENABLED=false`,
  and this is required, not redundant belt-and-suspenders** -- a real
  gap found (not assumed away) after a from-scratch verification with
  credentials genuinely stripped out of the environment wrongly implied
  it was safe by construction. `.env` carries real `LANGFUSE_PUBLIC_KEY`/
  `LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL` (needed for
  `live_assistant.py` and `harness.py`), and `conftest.py`'s
  `load_dotenv()` pulls in all three for the pytest process too -- so
  "Langfuse degrades to a no-op without credentials" was true in
  isolation but never actually applied to a real pytest run, which DOES
  have credentials available and, without this line, DOES send real
  mocked-fixture trace data (fake "Bob"/"Carol" requests) to the
  project's real Langfuse dashboard, and attempts real background OTLP
  span exports. That surfaced first as a confusing ~9x test-suite
  slowdown (162 tests: ~230s normally, over 1000s and once over 3000s)
  and eventually a hard failure --
  `opentelemetry.exporter...: Transient error ... us.cloud.langfuse.com
  ... Failed to resolve ... retrying` -- when the sandboxed test
  environment's outbound network access to Langfuse's endpoint was
  unreliable. `LANGFUSE_TRACING_ENABLED` is a real, documented Langfuse
  env var (`Langfuse(tracing_enabled=...)`'s config source) that fully
  disables the export pipeline regardless of which credentials happen
  to be present -- confirmed empirically (50 span-creating calls with
  real credentials loaded but this flag set: no warnings, no network
  activity, sub-second) before and after wiring it in, and the full
  suite returned to its normal ~230s runtime immediately once set.
- **Import order matters and fails silently if gotten wrong** (a
  documented Langfuse gotcha, not something discovered by trial and
  error here): Langfuse must be imported AFTER `load_dotenv()` so
  `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL` are
  already in the environment when it initializes, and `langfuse.openai`'s
  `OpenAI` class must be used in place of the plain SDK's from the start
  -- there's no error if either ordering is wrong, it just quietly
  traces nothing.
- **One root span per CLI invocation** (`main()`, named `handle-request`)
  is what makes every LLM call made while answering ONE request --
  Orchestrator classification, then whichever agent's own tool loop,
  however many rounds -- nest under a single trace instead of each
  becoming its own disconnected top-level trace. `user_id` (the real
  authenticated Google account email) and `environment="production"`
  are propagated via `propagate_attributes` as early as possible (before
  the classification call, since Langfuse's own docs are explicit that
  attributes only apply to spans created after entering that context --
  earlier spans aren't retroactively updated); a `feature` tag (the
  routed category: briefing/scheduling/email/follow_up/digest) is added
  once routing completes, matching Langfuse's own documented pattern for
  per-route filtering on an app with several distinct endpoints. The
  root span's own input/output is explicitly set to just the request
  text and final response text -- not left to default to some nested
  call's raw function arguments.
- **A first attempt at naming individual generations broke everything,
  and the eventual fix is the reason the deeper instrumentation below
  works at all.** Passing a descriptive `name=` kwarg (e.g.
  `name="answer-briefing"`) directly into a `client.create(...)` call
  inside a shared agent module broke the ENTIRE live pytest suite and
  `harness.py` instantly (57 failures) with `TypeError:
  Completions.create() got an unexpected keyword argument 'name'`. Root
  cause: these functions are shared code, called by BOTH the
  Langfuse-wrapped client (which recognizes and strips its own special
  kwargs before forwarding to the real API) and a plain client (used by
  every pytest live test and, at the time, `harness.py`) -- and the real
  OpenAI SDK validates kwargs strictly, rejecting anything it doesn't
  recognize. A kwarg that's silently safe through one concrete client
  can be a hard failure through another satisfying the exact same
  `ChatClient` protocol. **The fix that actually stuck**, used
  throughout the deeper instrumentation below: never touch what gets
  passed to `client.create(...)` at all -- wrap the *function* instead,
  from the outside, in a Langfuse span. `src/calendarmate/
  observability.py`'s `trace_agent_call`/`trace_tool_call` take the
  span's own name and a zero-argument callable to run inside it; the
  wrapped function's real signature, defaults, and internal
  `client.create(...)` calls are completely untouched, so this is safe
  regardless of which concrete client a caller passes in.
- **`run_orchestrator()`, `route_request()` (named `classify-intent` as
  a span, matching Langfuse's verb-first naming convention -- the
  Python function keeps its own name), and all four agent functions are
  now each wrapped in their own named span**, added at the user's
  explicit request after the first (client-swap-only) pass above. Each
  wrapped function keeps the exact same public signature and every
  internal return path completely unchanged: the actual pattern is
  "rename the existing body to a private `_impl` function, add a thin
  public wrapper with the original name that calls
  `trace_agent_call(name, agent=..., request=request, fn=lambda: ...impl(...))`"
  -- chosen specifically because several of these functions (the
  Scheduler and Follow-Up Agents' tool loops especially) return from
  several different internal points, and threading an explicit "report
  my output now" call through every one of them would be exactly the
  kind of invasive, easy-to-miss-one-path change this project avoids
  elsewhere. `handle_scheduling_request`'s wrapper forwards its ten
  keyword parameters via `**kwargs` rather than repeating the full
  signature twice, so the two can't drift out of sync.
- **Four individual tool calls are wrapped as `tool`-typed observations**
  (Langfuse's own recommended type for this, not a generic span):
  `check_availability` and `create_event` in `scheduler.py`'s
  `_dispatch_tool`, `get_meeting_record` and `send_followup_email` in
  `followup.py`'s. Deliberately selective, not "every tool this app
  has" -- `propose_alternative_slots`, `find_meeting_to_cancel`, and
  `cancel_event` stay unwrapped, matching exactly the four names asked
  for. `create_event`'s wrapping covers the WHOLE branch (including the
  already-passed-time and off-hours guard checks above the actual
  `create_event_fn` call), not just the underlying function call, so a
  guard-refused attempt still shows up as a traced `create_event` call
  with its real error result -- which is what the model actually
  experienced calling that tool, guard or no guard.
- **Tags merge across nested spans rather than replacing each other**,
  confirmed empirically (not assumed) before designing around it: a
  `propagate_attributes(tags=[...])` call nested inside another one adds
  to the trace's tag list rather than overwriting it, and this holds
  even when the OUTER `propagate_attributes` is entered before any span
  yet exists (i.e. wrapping a call to a function that creates its own
  root span internally, exactly `harness.py`'s shape below). This is why
  `route_request`'s span can unconditionally tag "orchestrator" and each
  agent's own span can separately tag its own name (e.g. "scheduling")
  without either one needing to know the other ran -- both land on the
  same trace. `run_orchestrator`'s own wrapper passes `agent=None`
  deliberately (see `trace_agent_call`'s docstring) since it has nothing
  of its own to add beyond what the nested calls already supply.
- **`harness.py` is now a second traced entry point, tagged per case.**
  Its `raw_client` (passed into `SpyingChatClient`, so both mechanisms
  compose transparently) is now the Langfuse-wrapped `OpenAI` class
  instead of the plain SDK. Each case's `run_orchestrator(case["request"],
  spy)` call is wrapped in `propagate_attributes(tags=[case["id"]])`,
  entered before that call (relying on the tag-merging behavior above)
  -- the resulting trace for, say, T3 ends up tagged
  `["T3", "orchestrator", "scheduling"]`, with `run-orchestrator` >
  `classify-intent` + `handle-scheduling` > `check_availability` +
  `create_event` all correctly nested underneath, verified directly
  against the real API after a full 18-case run (18/18 passed, no
  regression from any of this). `main()` flushes once at the end, the
  same "batch script, not a server" reasoning as `live_assistant.py`'s
  own single end-of-run flush.
- **A later request -- "the dashboard shows a lot of cost under a
  generic OpenAI-generation label" -- traced back to two separate,
  real gaps this section originally got wrong, not one.** First: every
  individual generation (the actual LLM calls, as distinct from the
  named SPANS wrapping them) still showed up as the integration's
  generic `"OpenAI-generation"`, since the earlier `name=` attempt
  above was reverted wholesale rather than fixed. Second: `judge_openai.py`'s
  verdict-generation call was believed to be genuinely untraced by
  using a separately-constructed plain `openai.OpenAI()` -- also wrong,
  and worth recording exactly why: `langfuse.openai`'s drop-in
  `OpenAI` class monkey-patches `Completions.create` GLOBALLY, for the
  entire process, the moment that module is imported, because the
  patch lives on the shared `Completions` class object itself (there is
  only one, per process) rather than on any one instance -- confirmed
  empirically by constructing a separate plain `openai.OpenAI()` in the
  same process as a `langfuse.openai` import and finding it traced
  anyway. `harness.py` imports `langfuse.openai` (for `raw_client`), so
  the judge's "separate plain client" was being captured the whole
  time regardless -- just anonymously, contributing exactly the kind of
  unlabeled cost being asked about.
- **The generation-naming fix**: `generation_name_kwargs(name)` in
  `observability.py` returns `{"name": name}` ONLY when
  `"langfuse.openai" in sys.modules`, and `{}` otherwise -- `**`-expanded
  into each shared module's `client.create(...)` call (e.g.
  `client.create(..., **generation_name_kwargs("answer-briefing"))`).
  This is the safe version of the exact thing that broke 57 tests the
  first time: whether `name=` is safe to pass depends on whether
  `langfuse.openai` has been imported *anywhere in the current process*
  (a process-wide fact, not a per-client one, per the global-patch
  finding above) -- true for `live_assistant.py` and `harness.py`
  (both import it), false for `pytest` (which never does, anywhere,
  confirmed by checking `sys.modules` directly). Every
  `client.create(...)` call across `orchestrator.py`, `briefing.py`
  (both rounds), `scheduler.py`, `email.py` (both rounds plus
  `_classify_batch`), and `followup.py` now carries a real name
  matching its enclosing span (`classify-intent`, `answer-briefing`,
  `handle-scheduling`, `answer-email`, `classify-emails-batch`,
  `run-followup`). `judge_openai.py` sets `name="judge-verdict"`
  unconditionally rather than through this helper, since that module
  has exactly one caller (`harness.py`) which always imports
  `langfuse.openai` -- no process-dependent branching needed there.
  Verified against the real API: generations that previously showed
  `OpenAI-generation` now show their real names, and the full pytest
  suite stayed at 162/162 (confirming `pytest`'s process genuinely never
  triggers the patch).
- **`harness.py`'s judge call now reuses `raw_client` directly instead
  of constructing its own plain client** -- there was never a way to
  make it genuinely untraced once `langfuse.openai` is imported
  anywhere in the process (see above), so pretending otherwise via a
  separate client instance was accomplishing nothing except hiding that
  it was already being traced anonymously. It's wrapped in its own
  `propagate_attributes(tags=[case["id"], "judge"])` now, so its trace
  is properly named (`judge-verdict`) and attributable to the case it
  graded instead of showing up as unlabeled noise.
- **Leftover diagnostic traces from developing this feature were found
  and deleted from the real Langfuse project** (`langfuse.api.trace.
  delete_multiple`) -- `test-root-created-inside`, `test-outer-orchestrator`,
  `manual-generation-test`, and four `repro-test-call` traces, all
  created while manually verifying span nesting and generation-parsing
  behavior earlier in this work, none of them real usage. Worth noting
  for future diagnostic work in this project: verifying Langfuse
  behavior against the real API, by necessity, writes real (if
  low-stakes) data into the user's real project -- clean up what was
  created once the verification is done, the same discipline
  `cleanup_old_traces.py` below exists to make routine for everything
  else.
- **Verifying the trace was captured correctly needed the right read
  API, not just any read API** -- worth recording since the wrong one
  looks exactly like a real, serious bug. `langfuse.api.observations.
  get_many(...)` (the v2 observations list endpoint) reliably returns
  `None` for `usage_details`, `cost_details`, `input`, and `output` on
  every generation, and returns `model` only when explicitly named in
  its `fields=` parameter -- this looked like proof the OpenAI
  auto-instrumentation was fundamentally broken (and briefly triggered
  an unnecessary detour: downgrading `openai` from 2.x back to 1.x on
  the theory that a new major SDK version had broken Langfuse's response
  parsing, which changed nothing and was reverted). Directly inspecting
  the raw OpenTelemetry span attributes locally, before any export,
  showed every field was already correct and complete
  (`langfuse.observation.model.name`, `.usage_details`, `.input`,
  `.output` all present and correct) -- proving the instrumentation
  itself was never the problem. `langfuse.api.trace.get(trace_id)` (a
  full single-trace fetch, distinct from the lightweight list endpoints)
  returns everything correctly: real model name, real token counts, and
  the complete real messages/output for every nested generation. Lesson:
  when self-auditing a trace per this skill's mandatory workflow, a
  list/summary endpoint returning `None` for a field is not proof the
  data isn't there -- confirm against a single-item detail fetch (or the
  Langfuse UI itself) before concluding the instrumentation is broken.
- **Sensitive data masking was deliberately NOT implemented**, and this
  is an open question for the user, not a silent gap. Real calendar/
  email content -- attendee addresses, meeting titles, email bodies,
  meeting notes -- flows through every generation's input/output exactly
  as instrumentation.md's own baseline table requires ("Trace
  input/output: does the trace capture meaningful input/output?"), which
  means it's now stored in Langfuse's cloud, not just this local machine.
  Deciding WHAT counts as too sensitive to send (full email bodies?
  attendee addresses? nothing, since this is a personal single-user
  account?) is a judgment call for the user to make, the same way the
  "needs attention" recency window and the reply-check API cost were
  negotiated with the user before writing code rather than decided
  unilaterally elsewhere in this file -- not something to bolt on
  silently by guessing what to redact. Discussed with the user and
  concluded masking isn't warranted for now: real calendar/email content
  already flows to OpenAI on every single request regardless of
  Langfuse, so tracing doesn't introduce a new category of exposure --
  only a second copy of the same data, in the user's own single-user
  Langfuse project, for their own debugging.
- **`cleanup_old_traces.py`** (project root) exists because configurable
  data retention (auto-delete traces after N days) -- the original,
  lowest-effort recommendation for bounding how long that data sits in
  Langfuse -- turned out to require a paid Langfuse plan; attempting it
  via `langfuse.api.projects.update(..., retention=30)` with the
  existing project-scoped API key failed with a 403 (`"Organization-
  scoped API key required for this operation"` -- retention is an
  org/admin-level setting, out of scope for a key meant only for data
  access, and generating a more powerful org-level key just for this one
  setting wasn't judged worth it), and the Hobby plan the user is
  actually on doesn't expose the setting in the UI either. This script
  is the manual fallback: it lists (or, with `--confirm`, actually
  deletes via `langfuse.api.trace.delete_multiple`, batched) every trace
  older than `--days` (30 by default). Defaults to a dry run -- prints
  the count, deletes nothing -- since deleting trace data is
  irreversible; the user runs it themselves whenever they want to clean
  up, rather than it running automatically, per their own explicit
  choice over an automatic-schedule alternative. Verified against the
  real account: dry run with the default 30-day threshold correctly
  found 0 (nothing that old yet), and `--days 0` correctly found all 10
  existing traces -- confirming the pagination and cutoff logic work
  before the user ever runs `--confirm` for real.
- **`user_id` is now an explicit, threaded-through parameter on every
  traced function, not left to only the ambient `propagate_attributes`
  context an outer caller happens to set up.** `trace_agent_call` gained
  a `user_id: str | None = None` parameter (alongside `agent`, same
  "None means don't touch it" semantics -- confirmed empirically that a
  nested `propagate_attributes(user_id=None)` leaves an already-active
  real user_id from an outer caller untouched, the same "add, don't
  replace" behavior tags already have), and `route_request`,
  `run_orchestrator`, all four agent functions, and `answer_digest_request`
  (which doesn't have its own span but forwards the parameter into the
  two calls it makes, so a digest-routed request's briefing/email traces
  are tagged the same as calling either directly) all accept it and pass
  it through. `handle_scheduling_request`'s wrapper pops `user_id` out of
  its forwarded `**kwargs` before calling the impl function, specifically
  so it's never confused with the pre-existing, unrelated `user_name`
  parameter (whose calendar this is, used for ownership checks) --
  same name-collision risk `run_followup_agent`'s own pre-existing
  `user_name` parameter has, avoided the same way.
  - **`harness.py`** passes a fixed `_TEST_USER_ID = "capstone-test-user"`
    into every case's `run_orchestrator(...)` call and into the judge's
    `propagate_attributes(...)` wrapper -- this is a capstone eval run
    against mocked fixtures, not a real account, so a real-looking
    identifier would be actively misleading.
  - **`live_assistant.py`** computes the real authenticated Google
    account email ONCE in `main()` and passes it both ways: via the
    existing ambient `propagate_attributes(user_id=..., ...)` wrapper
    (still required -- it's what tags the ROOT `handle-request` span
    itself, since a nested call's own `propagate_attributes` can't
    retroactively reach a span created before it fired) AND explicitly
    into `route_request(...)` and `_LIVE_DISPATCH[category](...)`,
    which required adding a `user_id` parameter to each of its five
    internal dispatch closures (`_briefing`, `_scheduling`, `_email`,
    `_follow_up`, `_digest`) too. Belt-and-suspenders on purpose: the
    explicit parameter guarantees each agent's own trace is correctly
    tagged even if some future caller's outer wrapping is missing or
    wrong, without removing the mechanism that tags the root span.
  - **`voice_wrapper.py` does not exist in this repo** -- referenced as
    a hypothetical future live-assistant-style entry point when this was
    requested, not something built here. Whenever it (or any other real
    entry point) is added, the pattern to follow is `live_assistant.py`'s:
    resolve the real identifier once, pass it as `user_id=` into
    `route_request`/whichever agent function is called, matching
    however that entry point already resolves the caller's real
    identity.
  - Verified against the real API after the change: pytest's two test
    files that fully replace a dispatched agent with a hand-written fake
    (`test_pipeline.py`'s `fake_agent`, `test_digest.py`'s
    `fake_briefing`/`fake_email`) needed a `user_id=None` parameter added
    to their stubs -- caught immediately by the existing suite, the same
    "a fake stub needs updating when the real function gains a
    pass-through parameter" pattern already documented elsewhere in this
    file, not a live-only finding. Full suite: 161/162 (the one failure
    the same pre-existing flaky live-model test documented above,
    confirmed unrelated). Full 18-case harness run: 18/18, with the two
    newest T7 traces correctly showing `user_id: capstone-test-user`
    against three older ones (from before this change) correctly still
    showing `None`. A real `live_assistant.py` request's `handle-request`
    trace correctly showed the real authenticated email.
- **A dashboard cost spike traced to `gpt-4o-2024-08-06` was investigated
  and confirmed intentional, not a leftover default.** Grepping every
  `_MODEL = "gpt-4o..."` assignment across the codebase turns up exactly
  one non-mini model: `email.py`'s `_MODEL = "gpt-4o"`, which is the
  Round 3 fix documented above (the Email Agent's action-required/FYI
  judgment call needed a stronger model after two prompt-only rounds
  failed, one of them by hallucinating content). Every other agent
  (`orchestrator.py`, `briefing.py`, `scheduler.py`, `followup.py`,
  `judge_openai.py`) stays on `gpt-4o-mini`. The actual cost driver isn't
  a bug: `gpt-4o` is roughly 10-17x more expensive per token than
  `gpt-4o-mini`, and the Email Agent's batched classification
  (`classify-emails-batch`, up to 15 emails per call, potentially several
  batches per request -- see the large-pool batching fix above) means a
  single "summarize my emails" or "needs attention" request can rack up
  multiple full-price `gpt-4o` generations in one trace. Worth knowing as
  the expected shape of this agent's cost, not something to "fix" by
  reverting to `gpt-4o-mini` without re-litigating the classification
  accuracy trade-off that motivated the change in the first place.
- **Querying Langfuse for ERROR-level observations
  (`langfuse.api.observations.get_many(level="ERROR")`) found 15 total,
  all one root cause, not 15 distinct problems.** Every one is the
  `classify-intent` span's `ValueError: Orchestrator returned an invalid
  route: '...'` -- `route_request`'s intentional guard rejecting any
  model output outside the five valid categories (see the T15 harness
  entry above for why this guard exists). Langfuse marks the span ERROR
  because the exception propagates out of it, but `run_orchestrator`
  catches it one level up and returns the fallback message -- none of
  these are real crashes or user-facing failures, just the designed
  rejection path rendering red in the dashboard. Breakdown of the 15: 9
  from genuine empty-string labels (3 tagged `T15`, the harness's real
  off-topic edge case; the rest untagged `run-orchestrator` traces from
  earlier manual runs), 3 matching `'not_a_real_category'` (exactly
  `test_pipeline.py`'s `test_run_orchestrator_falls_back_gracefully_on_
  unclassifiable_request` parametrize value), and 3 matching `'do
  something else'` (exactly `test_orchestrator.py`'s
  `test_route_request_rejects_invalid_label` fixture string). The API's
  `get_many` endpoint uses cursor-based pagination (a `cursor` field on
  `meta`), not the page-based pagination `trace.list` uses -- passing
  `page=`/`parse_io_as_json=True` (both valid on other endpoints) raises
  a 400 on this one; input/output are always returned as raw strings on
  this endpoint now.
- **The 6 ERROR observations matching pytest's own fixture strings were
  real evidence that pytest traces leaked to the real Langfuse project
  before `tests/conftest.py`'s `LANGFUSE_TRACING_ENABLED=false` fix
  landed** -- confirmed by finding traces named with `'not_a_real_
  category'`/`'do something else'`, values no live model would ever
  generate, matching two different test files' hardcoded fixtures
  exactly. All 6 were deleted via `langfuse.api.trace.delete_multiple`
  once confirmed, the same cleanup pattern as the earlier
  `test-root-created-inside`/`repro-test-call` leftovers. This is doubly
  useful as a regression check: if pytest-fixture-shaped trace names
  (`not_a_real_category`, `do something else`, or any other test-only
  string) ever reappear in a fresh ERROR-observation query run well
  after the conftest fix, that means the fix has silently broken --
  worth re-running this same query occasionally rather than assuming a
  fix like this stays fixed forever.
- **A final harness run confirmed the `user_id` tagging fix holds across
  the full 18-case suite with no regressions**: 18/18 passed, and the
  newest `T1`-tagged trace pulled directly from the API afterward showed
  `user_id: capstone-test-user` on both the case's own trace and its
  judge verdict (`tags: ['T1', 'judge']`) -- confirming the tagging
  survives a real, complete run rather than only the smaller
  spot-checks done when the feature first shipped.
