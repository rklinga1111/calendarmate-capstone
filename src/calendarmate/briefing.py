"""The Briefing Agent: answers questions about the user's calendar.

Uses the `get_calendar_events` tool (backed by a mocked fixture -- see
`calendarmate.tools.calendar_tool`) so the model always grounds its
answer in real event data instead of inventing meetings.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Protocol

from calendarmate.observability import generation_name_kwargs, trace_agent_call
from calendarmate.tools.calendar_tool import Event, events_in_range, find_conflicts, load_events

_PROMPT_PATH = Path(__file__).parent / "prompts" / "briefing.md"

_MODEL = "gpt-4o-mini"

_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_calendar_events",
        "description": (
            "Get calendar events for an inclusive date range, with any "
            "scheduling conflicts already flagged."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "ISO date, e.g. 2025-06-17"},
                "end_date": {"type": "string", "description": "ISO date, e.g. 2025-06-17"},
            },
            "required": ["start_date", "end_date"],
        },
    },
}


def load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


class ChatClient(Protocol):
    def create(self, **kwargs: object) -> object: ...


def _run_calendar_tool(start_date: str, end_date: str, events: list[Event]) -> str:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    range_events = events_in_range(events, start, end)
    conflicts = find_conflicts(range_events)
    conflicting_ids = {id(e) for pair in conflicts for e in pair}

    payload = {
        "events": [
            {
                "title": e.title,
                "date": e.date.isoformat(),
                # Computed here, not left for the model to work out --
                # LLMs are unreliable at manual weekday arithmetic (a real
                # request once got September 6, 2026 labeled "Saturday"
                # when it's actually a Sunday), so the tool result carries
                # the ground truth instead of asking for a guess.
                "weekday": e.date.strftime("%A"),
                "start": e.start.isoformat(timespec="minutes"),
                "end": e.end.isoformat(timespec="minutes"),
                "attendees": list(e.attendees),
                "has_conflict": id(e) in conflicting_ids,
            }
            for e in range_events
        ],
        "conflicts": [
            {"a": a.title, "b": b.title, "date": a.date.isoformat()} for a, b in conflicts
        ],
    }
    return json.dumps(payload)


def answer_briefing(
    request: str,
    client: ChatClient,
    *,
    today: date | None = None,
    events: list[Event] | None = None,
    user_id: str | None = None,
) -> str:
    return trace_agent_call(
        "answer-briefing",
        agent="briefing",
        request=request,
        user_id=user_id,
        fn=lambda: _answer_briefing_impl(request, client, today=today, events=events),
    )


def _answer_briefing_impl(
    request: str,
    client: ChatClient,
    *,
    today: date | None = None,
    events: list[Event] | None = None,
) -> str:
    today = today if today is not None else date.today()
    events = events if events is not None else load_events()

    messages: list[dict[str, object]] = [
        {
            "role": "system",
            "content": (
                f"{load_system_prompt()}\n\n"
                f"Today's date is {today.isoformat()} ({today.strftime('%A')})."
            ),
        },
        {"role": "user", "content": request},
    ]

    response = client.create(
        model=_MODEL, messages=messages, tools=[_TOOL_SCHEMA], temperature=0, **generation_name_kwargs("answer-briefing")
    )
    message = response.choices[0].message  # type: ignore[attr-defined]
    tool_calls = getattr(message, "tool_calls", None)

    if not tool_calls:
        return message.content.strip()  # type: ignore[union-attr]

    messages.append(message)  # type: ignore[arg-type]
    for tool_call in tool_calls:
        args = json.loads(tool_call.function.arguments)
        result = _run_calendar_tool(args["start_date"], args["end_date"], events)
        messages.append(
            {"role": "tool", "tool_call_id": tool_call.id, "content": result}
        )

    final = client.create(
        model=_MODEL, messages=messages, tools=[_TOOL_SCHEMA], temperature=0, **generation_name_kwargs("answer-briefing")
    )
    return final.choices[0].message.content.strip()  # type: ignore[union-attr]
