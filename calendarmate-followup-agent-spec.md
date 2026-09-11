# Follow-Up Agent — Detailed Build Spec (Epic 6)

## 1. Where it sits in the system design

From the architecture diagram: `Orchestrator (Router) → Follow-Up Agent
→ Response Formatter → User Output`, with Gmail API as its one external
dependency (send), inside the Observability boundary like every other
agent.

- **Upstream dependency**: the Orchestrator. It receives control only
  when the Orchestrator classifies a request as `follow_up` — e.g. "send
  a follow-up for my 2pm sync," "did the design review have any action
  items." It never self-triggers in this build (see Design Decisions in
  the system design doc — on-demand, not an automatic "meeting just
  ended" poller).
- **Downstream dependency**: none. It's a leaf node — its output goes
  straight to the Response Formatter, not to another agent.
- **Sibling relationship**: independent of Briefing/Scheduler/Email —
  doesn't call them and isn't called by them. This means it can be built
  and tested in isolation any time after the Orchestrator exists (Epic 1),
  without needing Epics 2–4 finished first.

## 2. External dependencies

| Dependency | Real system | Mocked-for-build version |
|---|---|---|
| Meeting data (attendees + notes/transcript) | Google Calendar API (attendees) + notes doc or transcript service | `evals/followup_meeting_fixtures.json` (already built — 4 fixtures) |
| Send capability | Gmail API `users.messages.send` | Mock `send_followup_email()` function returning a fake message id, same pattern as `create_event()` in the Scheduler Agent |

Note the real-system version pulls attendees from Calendar and notes from
wherever your org keeps them — for this build, both are pre-merged into
a single `meeting_record` per fixture entry, which is a reasonable
simplification to flag in your submission's design decisions.

## 3. Tool schemas (OpenAI tool-calling format, matching the Scheduler Agent's pattern)

```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_meeting_record",
            "description": "Retrieve a meeting's attendees and notes/transcript by meeting_id or title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_id": {"type": "string", "description": "Optional exact id"},
                    "title_hint": {"type": "string", "description": "Optional partial title match, e.g. 'design review'"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_followup_email",
            "description": "Send the drafted follow-up email to the meeting's actual attendees.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "recipients": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["subject", "body", "recipients"],
            },
        },
    },
]
```

Two tools, matching the two real dependencies above. The agent must call
`get_meeting_record` before it can produce anything — it has no other way
to know what was discussed, which is what makes the "never invent"
grounding rule enforceable rather than just stated.

## 4. System prompt

```
You generate post-meeting follow-ups.

Rules:
- Always call get_meeting_record first. Never draft a follow-up from
  assumption or memory of what "usually" happens in this kind of meeting.
- Base action items only on what's explicitly stated in the meeting's
  notes/transcript. Do not infer a task from a topic being discussed —
  "we talked about X" is not the same as "someone agreed to do X."
- If the notes contain no concrete commitments (even if notes exist and
  are non-empty), say explicitly that no action items were identified.
  Do not manufacture generic filler action items to avoid an empty list.
- The recipients list must exactly match the meeting's actual attendees
  from get_meeting_record — never add, drop, or guess a recipient.
- Only call send_followup_email after the draft is ready. Show the draft
  in your response either way, so the user can see exactly what would be
  sent.
```

## 5. Data contract

**Orchestrator → Follow-Up Agent:**
```json
{"category": "follow_up", "original_request": "send a follow-up for the design review"}
```

**Follow-Up Agent → Response Formatter:**
```json
{"agent": "follow_up", "result": {"subject": "...", "body": "...", "recipients": ["..."], "sent": true|false}, "tool_calls_made": ["get_meeting_record", "send_followup_email"]}
```

`sent: false` should be a valid, expected output — e.g. when Story 6.3
applies and there's nothing worth sending, or during testing when you
want the draft without actually calling send.

## 6. Acceptance criteria (from the backlog — restated here for convenience)

**Story 6.1 — grounded action items**
- Action items derived only from actual meeting data, never invented
- Specific enough to act on, not a vague restatement of the topic
- If source data is thin, says so rather than fabricating

**Story 6.2 — correct recipients**
- Recipients match the meeting's actual attendee list exactly
- Sent via the tool, not just displayed as chat text

**Story 6.3 — honest about "nothing to report"**
- Explicitly states no action items were found, when true
- Does not send a follow-up that fabricates content to have something to send

## 7. Test fixtures and what each one is designed to catch

| Fixture | Tests | What a broken agent does wrong |
|---|---|---|
| `m_001` (Q3 Roadmap Sync) | 6.1 happy path | Misses an action item, or attributes it to the wrong owner |
| `m_002` (1:1, vague notes) | 6.3 — the hard case | Invents "Jordan to work on career growth" from a general discussion that had no actual commitment |
| `m_003` (empty notes) | 6.3 — the easy case | Fabricates any content at all when there's nothing to work with |
| `m_004` (mixed real + non-action) | 6.1 + 6.2 precision | Turns "approved, no further action needed" into a fake action item just because it's adjacent to real ones |

Test in this order: `m_001` first to confirm basic wiring works, then
`m_003` (easiest failure to catch), then `m_002` and `m_004` (the ones
that actually expose grounding failures).

## 8. Ready-to-paste prompt for Claude Code

```
Build the Follow-Up Agent for CalendarMate as
evals/follow_up_agent_openai.py, following the same structure as
evals/scheduler_agent_openai.py (OpenAI tool-calling loop, mocked tool
implementations, a __main__ block that runs test cases).

Use:
- The system prompt in section 4 of calendarmate-followup-agent-spec.md
- The tool schemas in section 3 of the same doc
- evals/followup_meeting_fixtures.json as the data source for
  get_meeting_record (match by meeting_id or fuzzy title_hint)
- A mocked send_followup_email() that returns {"status": "sent",
  "message_id": "mock-..."} without actually sending anything

Write run_followup_agent(user_input: str) -> dict returning the shape in
section 5's "Follow-Up Agent → Response Formatter" contract.

Test against all four fixtures (m_001-m_004) and show me the output for
each. Then check each against the acceptance criteria in section 6 —
pay the closest attention to m_002 and m_004, per section 7, since those
are the ones designed to catch a grounding failure rather than a wiring
bug.
```
