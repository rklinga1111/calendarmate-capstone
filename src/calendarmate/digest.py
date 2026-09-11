"""The Digest Agent: a combined calendar + email overview.

Doesn't have its own tools or grounding logic -- it delegates to the
Briefing Agent and the Email Agent (each already independently grounded
and tested) and stitches their two answers together under clear
headings. This is deliberately NOT a third LLM call synthesizing the two
into one blended narrative: concatenating two already-correct answers
carries no risk of introducing new hallucination or inconsistency,
whereas an extra synthesis pass would.

Exists because "what needs my attention this week" is genuinely
ambiguous between calendar and email, and a real user's own reading of
that phrase turned out to mean both at once -- something no amount of
tuning either single-domain agent's prompt could produce on its own,
since each only ever sees its own tool's data.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from calendarmate.briefing import answer_briefing
from calendarmate.email import answer_email_request
from calendarmate.tools.calendar_tool import Event
from calendarmate.tools.email_tool import Email


class ChatClient(Protocol):
    def create(self, **kwargs: object) -> object: ...


def answer_digest_request(
    request: str,
    client: ChatClient,
    *,
    today: date | None = None,
    events: list[Event] | None = None,
    emails: list[Email] | None = None,
    own_email: str | None = None,
    user_id: str | None = None,
) -> str:
    # No `trace_agent_call` wrapper of its own here -- this agent has no
    # tools or grounding logic beyond the two calls below, each already
    # independently traced; `user_id` is just forwarded through so a
    # digest-routed request's underlying briefing/email traces are
    # tagged the same as if either had been called directly.
    calendar_part = answer_briefing(request, client, today=today, events=events, user_id=user_id)
    email_part = answer_email_request(
        request, client, today=today, emails=emails, own_email=own_email, user_id=user_id
    )
    return f"### Calendar\n{calendar_part}\n\n### Email\n{email_part}"
