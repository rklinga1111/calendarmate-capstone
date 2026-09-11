# CalendarMate

AI-powered multi-agent productivity assistant for PMs/TPMs — automates
daily briefing, meeting scheduling, and email triage. Built as an
Applied Agentic AI capstone project.

A multi-agent assistant that routes natural-language requests to a
briefing, scheduling, email, follow-up, or combined digest specialist. See
[calendarmate-backlog.md](calendarmate-backlog.md) for the full story
backlog and acceptance criteria (T1-T12), and
[calendarmate-followup-agent-spec.md](calendarmate-followup-agent-spec.md)
for the Follow-Up Agent's detailed build spec. [workflow.json](workflow.json)
is a hand-authored, machine-readable description of the same
orchestration graph -- entry points, the Orchestrator's routing, each
agent, the mocked/real data layer, and what's traced to Langfuse. This
project is hand-coded Python rather than built in a visual workflow
tool (n8n/Make/Flowise), so nothing was literally "exported"; this file
serves the same purpose.

## Status

- [x] Epic 1 — Orchestrator routing (`src/calendarmate/orchestrator.py`)
- [x] Epic 2 — Briefing Agent (`src/calendarmate/briefing.py`)
- [x] Epic 3 — Scheduler Agent (`src/calendarmate/scheduler.py`)
- [x] Epic 4 — Email Agent (`src/calendarmate/email.py`)
- [x] Epic 5 — Conflict Resolver (extends `src/calendarmate/scheduler.py`)
- [x] Epic 6 — Follow-Up Agent (`src/calendarmate/followup.py`)
- [x] Digest Agent (`src/calendarmate/digest.py`) — a 5th route for
  requests spanning both calendar and email at once ("what needs my
  attention this week", "catch me up"), combining the Briefing and
  Email agents' own grounded answers rather than answering on its own.
- [x] End-to-end pipeline — `run_orchestrator()` in
  `src/calendarmate/pipeline.py` classifies a request and dispatches it
  to the matching agent, returning the final answer. Every epic above
  was previously only tested in isolation; this is what actually wires
  them into one working assistant.
- [x] Real Google Calendar/Gmail integration — `live_assistant.py`, an
  explicitly separate entry point from everything above (see "Real
  Google Calendar/Gmail" below).

## Setup

```bash
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in your key:

```bash
cp .env.example .env
```

```
OPENAI_API_KEY=sk-...
```

`.env` is gitignored and never committed. `pytest` loads it automatically
(via `tests/conftest.py`). Without a key set, the live orchestrator test
is skipped and only the offline parsing/validation tests run.

## Test

```bash
pytest
```

## Baseline eval (T1-T12 + edge cases, judged)

```bash
python harness.py
```

Runs all 15 cases in [baseline_cases.json](baseline_cases.json) — the
12 backlog acceptance cases (T1-T12) plus 3 edge cases (T13-T15: an
empty calendar day, a cross-timezone/midnight scheduling request, and a
request with no clear intent at all) — through the real end-to-end
pipeline (`run_orchestrator()` — classification and dispatch together,
not each agent called directly) and grades each response against its
criteria with an LLM judge ([judge_openai.py](judge_openai.py)). Writes
full results (response text, tool-call trace, per-criterion verdicts)
to `eval_results.json`. This is a different kind of check than
`pytest`: the test suite makes deterministic keyword assertions per
agent; the harness judges natural-language output against the backlog's
actual acceptance criteria, through the full pipeline, the way a real
user request would flow. Needs `OPENAI_API_KEY` set (`.env` or the
environment) — there's no offline/skip mode for this one, since judging
*is* the point.

`pytest` and `harness.py` always run against the mocked fixtures
(`calendar.json`/`inbox.json`/`meetings.json`) — nothing below changes
that.

## Real Google Calendar/Gmail (optional, separate from everything above)

`live_assistant.py` runs CalendarMate against your actual Google
Calendar and Gmail instead of the mocked fixtures. It is a completely
separate entry point — `pytest`, `harness.py`, and every mocked fixture
stay exactly as they are; nothing about running the test suite or the
eval touches a real account.

**Setup (once):**

```bash
pip install -e ".[dev,google]"
```

1. In [Google Cloud Console](https://console.cloud.google.com/), create/pick
   a project, enable the **Google Calendar API** and **Gmail API**, set up
   the OAuth consent screen, and create an OAuth client ID of type
   **Desktop app**. Download it as `credentials.json` into this directory.
2. Run the one-time consent flow yourself:
   ```bash
   python google_auth_setup.py
   ```
   This opens a browser for you to log in and grant access, then saves
   `token.json` here. Both `credentials.json` and `token.json` are
   gitignored — never commit either; they grant real access to your
   real account.

**Use it:**

```bash
python live_assistant.py "What does my day look like?"
python live_assistant.py "Schedule a 30-minute meeting with jane@example.com tomorrow at 2pm"
```

**Before you run a scheduling or follow-up request for real:** `create_event`
creates an actual event (with a real Google Meet link) on your actual
calendar and can send a real invite to whoever you name; `send_followup_email`
sends a real email from your real account. Neither one asks for
confirmation first — decide before running the command the same way
you'd think before sending any other email or invite yourself. Also,
real attendees are real email addresses, not the mocked fixture's first
names ("Bob", "Carol") — phrase requests accordingly.
