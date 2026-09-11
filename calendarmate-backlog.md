# CalendarMate — Product Backlog

How to use this: paste one story at a time into Claude Code, in order.
Each story includes acceptance criteria pulled straight from the
assignment's baseline dataset (T1–T12) — these are what you'll check
the output against, and what feeds directly into the eval harness later.
Don't move to the next story until Claude Code's output satisfies the
current one's acceptance criteria.

---

## Epic 1: Core routing (build this first — everything depends on it)

### Story 1.1 — Route a request to the right agent
**As a user**, when I send any natural-language request, **the system
should** classify it as briefing, scheduling, or email, and hand it to the
right specialist — without trying to answer it directly itself.

**Acceptance criteria:**
- "What does my day look like?" routes to briefing
- "Schedule a meeting with X" routes to scheduling
- "Summarize my emails" routes to email
- The orchestrator never generates a final answer itself — only a routing decision

**Prompt for Claude Code:**
> Build the Orchestrator: a function that takes a user request string and
> returns one of "briefing" | "scheduling" | "email" | "follow_up". Use
> the system prompt from calendarmate-capstone-submission.md under
> "Orchestrator". Write a few unit tests using the sample inputs above.

---

## Epic 2: Briefing Agent

### Story 2.1 — Daily briefing
**As a user**, when I ask what my day looks like, **the system should**
list today's meetings with times and attendees, and flag any conflicts.

**Acceptance criteria (= test T1):**
- Lists today's meetings with times and attendees
- Mentions any scheduling conflicts if they exist

### Story 2.2 — Future-day briefing without inventing events
**As a user**, when I ask about tomorrow's meetings, **the system should**
list only real events for that day.

**Acceptance criteria (= test T2):**
- Lists tomorrow's meetings with times
- Never invents a meeting not present in the calendar data

### Story 2.3 — Week-level conflict check
**As a user**, when I ask if I have conflicts this week, **the system
should** check every day, not just today.

**Acceptance criteria (= test T10):**
- Checks the whole week for overlapping times
- Lists specific conflicts, or explicitly confirms there are none

### Story 2.4 — Specific-day lookup
**As a user**, when I ask what's happening on a specific future day,
**the system should** show that day only.

**Acceptance criteria (= test T12):**
- Shows events for the exact day asked
- Never shows the wrong day's events

**Prompt for Claude Code:**
> Build the Briefing Agent using the system prompt from
> calendarmate-capstone-submission.md. It needs a calendar read tool
> (start with a mocked calendar fixture — a JSON file of fake events —
> before wiring the real Google Calendar API, so we can test the logic
> independently of OAuth setup). Test it against stories 2.1–2.4 above.

---

## Epic 3: Scheduler Agent

### Story 3.1 — Straightforward booking
**As a user**, when I give a complete request (attendee, date, time,
duration), **the system should** check availability and create the event.

**Acceptance criteria (= test T3):**
- Checks availability before creating
- Creates an event with a Google Meet link
- Confirms the exact time and attendee back to me

### Story 3.2 — Ask, don't guess, when info is ambiguous
**As a user**, when my request is vague ("sometime this week"), **the
system should** ask clarifying questions instead of picking something for me.

**Acceptance criteria (= test T4):**
- Asks which day, what time, and who specifically
- Never creates an event without confirmation

### Story 3.3 — Multi-attendee availability
**As a user**, when I ask to schedule with multiple people, **the system
should** check everyone, not just the first person mentioned.

**Acceptance criteria (= test T5):**
- Checks availability for all named attendees
- Proposes multiple candidate slots, not just one

### Story 3.4 — Flag unusual requests
**As a user**, when I ask for an odd time (weekend, 6am), **the system
should** flag it rather than booking silently.

**Acceptance criteria (= test T6):**
- Flags the weekend/unusual hour
- Asks for confirmation or warns before booking

### Story 3.5 — Ask when info is missing entirely
**As a user**, if I forget to say who a meeting is with, **the system
should** ask, never book with a blank attendee.

**Acceptance criteria (= test T9):**
- Asks who the meeting is with
- Never creates an event with a missing attendee

### Story 3.6 — Detect conflicts with existing meetings
**As a user**, when my request collides with something already on my
calendar, **the system should** catch it and offer alternatives.

**Acceptance criteria (= test T11):**
- Detects the conflict with the existing meeting
- Warns me
- Suggests alternative times

**Prompt for Claude Code:**
> Build the Scheduler Agent using its system prompt from the spec doc.
> Start against the same mocked calendar fixture from the Briefing Agent.
> Work through stories 3.1–3.6 in order — 3.1 first since it's the happy
> path, then the edge cases. Don't move to real Calendar API writes until
> all six pass against the mock.

---

## Epic 4: Email Agent

### Story 4.1 — Summarize and group unread email
**As a user**, when I ask for an email summary, **the system should**
group by urgency/topic and separate action items from FYIs.

**Acceptance criteria (= test T7):**
- Groups by urgency or topic
- Separates action-required from FYI
- Never invents email content

### Story 4.2 — Surface only what needs attention
**As a user**, when I ask what needs my attention, **the system should**
show only action-required items with sender and subject.

**Acceptance criteria (= test T8):**
- Identifies only action-required emails
- Includes sender and subject for each
- Never fabricates emails

**Prompt for Claude Code:**
> Build the Email Agent using its system prompt. Use a mocked inbox
> fixture first (a JSON list of fake emails with sender/subject/body/read
> status), same pattern as the calendar mock. Test against 4.1 and 4.2.

---

## Epic 5: Extended capability — Conflict Resolver

Already partially covered by story 3.6. If you want the full extended
capability (not just detection), add:

### Story 5.1 — Propose a concrete resolution
**As a user**, when a conflict is detected, **the system should** propose
at least one real trade-off, not just say "there's a conflict."

**Acceptance criteria:**
- Detects true time overlaps (partial and full)
- Proposes at least one alternative slot or explicit trade-off (shorten /
  move the other meeting / go async)
- Never silently reschedules an existing meeting without surfacing it as
  a proposal first

---

## Epic 6: Follow-Up Agent

Independent of Briefing/Scheduler/Email — a leaf node fed only by the
Orchestrator's `follow_up` route, with no downstream agent calls. Full
build spec (tool schemas, system prompt, data contract, test fixtures)
in [calendarmate-followup-agent-spec.md](calendarmate-followup-agent-spec.md).

### Story 6.1 — Grounded action items
**As a user**, when I ask for a meeting's follow-up or action items,
**the system should** derive them only from that meeting's actual notes.

**Acceptance criteria:**
- Action items derived only from actual meeting data, never invented
- Specific enough to act on, not a vague restatement of the topic
- If source data is thin, says so rather than fabricating

### Story 6.2 — Correct recipients
**As a user**, when a follow-up is sent, **the system should** send it
to exactly the people who were in the meeting.

**Acceptance criteria:**
- Recipients match the meeting's actual attendee list exactly
- Sent via the tool, not just displayed as chat text

### Story 6.3 — Honest about "nothing to report"
**As a user**, when a meeting had no real commitments, **the system
should** say so rather than inventing something to send.

**Acceptance criteria:**
- Explicitly states no action items were found, when true
- Does not send a follow-up that fabricates content to have something
  to send

**Prompt for Claude Code:**
> Build the Follow-Up Agent using the system prompt and tool schemas
> from calendarmate-followup-agent-spec.md, adapted to this project's
> existing conventions (an agent module under src/calendarmate/, a
> prompt file under src/calendarmate/prompts/, a mocked fixture under
> src/calendarmate/fixtures/, pytest tests) rather than the spec's
> evals/-based layout. Use a mocked meeting-record fixture with the
> four cases the spec describes (m_001-m_004) covering the happy path,
> vague notes with no real commitments, empty notes, and notes mixing
> real action items with an explicitly-not-an-action statement. Test
> against 6.1-6.3, in the order the spec recommends (m_001, then m_003,
> then m_002 and m_004).

---

## Suggested order

1. Epic 1 (routing) — nothing works without this
2. Epic 2 (briefing) — simplest agent, read-only, good confidence builder
3. Epic 3 (scheduler) — the meatiest one, most acceptance criteria
4. Epic 4 (email) — similar shape to briefing, should go faster
5. Epic 5 (conflict resolver) — only after 3.6 is solid
6. Epic 6 (follow-up) — independent of 2-5; can be built any time after
   Epic 1

Once all epics pass against mocked data, swap the mocks for the real
Google Calendar/Gmail connectors — that's a plumbing change, not a logic
change, since your agents were already tested against the same data shape.
