"""The Follow-Up Agent: drafts and sends post-meeting follow-ups.

Uses two tools -- `get_meeting_record` (the only source of a meeting's
attendees/notes, which is what prevents inventing action items) and
`send_followup_email` (mocked, mirrors `create_event`'s pattern in the
Scheduler Agent). Runs a multi-round tool loop like the Scheduler Agent,
since a real request needs both tools in sequence: look up the meeting,
then send.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Protocol

from calendarmate.observability import generation_name_kwargs, trace_agent_call, trace_tool_call
from calendarmate.tools.followup_tool import Meeting, find_meeting, load_meetings, send_followup_email

# Signatures a real integration (e.g. Google Calendar/Gmail) must match to
# be a drop-in replacement for the mocked lookup/send functions.
GetMeetingFn = Callable[[str | None, str | None], Meeting | None]
SendEmailFn = Callable[[list, str, str], dict]

_PROMPT_PATH = Path(__file__).parent / "prompts" / "followup.md"

_MODEL = "gpt-4o-mini"

_MAX_TOOL_ROUNDS = 5

_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_meeting_record",
            "description": "Retrieve a meeting's attendees and notes by meeting_id or title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_id": {"type": "string", "description": "Optional exact id"},
                    "title_hint": {
                        "type": "string",
                        "description": "Optional partial title match, e.g. 'design review'",
                    },
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


def load_system_prompt(user_name: str = "Alice") -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8").replace("{{USER_NAME}}", user_name)


class ChatClient(Protocol):
    def create(self, **kwargs: object) -> object: ...


def _dispatch_tool(
    name: str, args: dict, get_meeting_fn: GetMeetingFn, send_email_fn: SendEmailFn, user_name: str
) -> str:
    if name == "get_meeting_record":

        def _do_get_meeting_record() -> dict:
            meeting = get_meeting_fn(args.get("meeting_id"), args.get("title_hint"))
            if meeting is None:
                return {"error": "no matching meeting found"}
            return {
                "meeting_id": meeting.meeting_id,
                "title": meeting.title,
                "attendees": list(meeting.attendees),
                "notes": meeting.notes,
            }

        result = trace_tool_call("get_meeting_record", args, _do_get_meeting_record)
    elif name == "send_followup_email":

        def _do_send_followup_email() -> dict:
            # The sender doesn't need a copy of their own follow-up email
            # addressed to themselves -- a real send once did this because
            # the meeting's actual attendee list (correctly) included the
            # sender, and "recipients must exactly match attendees" had no
            # exception for the sender being one of them. Enforced here in
            # code, not just the prompt, since the user asked for this
            # specifically and it shouldn't depend on the model remembering
            # it every time.
            recipients = [r for r in args["recipients"] if r.lower() != user_name.lower()]
            if not recipients:
                return {
                    "error": (
                        "Excluding the sender leaves no recipients -- the only attendee "
                        "besides the sender is the sender themselves. Nothing was sent."
                    )
                }
            return send_email_fn(recipients, args["subject"], args["body"])

        result = trace_tool_call("send_followup_email", args, _do_send_followup_email)
    else:
        result = {"error": f"unknown tool {name}"}
    return json.dumps(result)


def run_followup_agent(
    request: str,
    client: ChatClient,
    *,
    meetings: list[Meeting] | None = None,
    sent: list[dict] | None = None,
    get_meeting_fn: GetMeetingFn | None = None,
    send_email_fn: SendEmailFn | None = None,
    user_name: str = "Alice",
    user_id: str | None = None,
) -> str:
    return trace_agent_call(
        "run-followup",
        agent="follow_up",
        request=request,
        user_id=user_id,
        fn=lambda: _run_followup_agent_impl(
            request,
            client,
            meetings=meetings,
            sent=sent,
            get_meeting_fn=get_meeting_fn,
            send_email_fn=send_email_fn,
            user_name=user_name,
        ),
    )


def _run_followup_agent_impl(
    request: str,
    client: ChatClient,
    *,
    meetings: list[Meeting] | None = None,
    sent: list[dict] | None = None,
    get_meeting_fn: GetMeetingFn | None = None,
    send_email_fn: SendEmailFn | None = None,
    user_name: str = "Alice",
) -> str:
    meetings = meetings if meetings is not None else load_meetings()
    sent = sent if sent is not None else []
    if get_meeting_fn is None:
        # Default: look up in the pre-loaded mocked list. A real
        # integration (e.g. Google Calendar) passes its own live lookup
        # instead -- existing callers that don't pass one are unaffected.
        def get_meeting_fn(meeting_id: str | None, title_hint: str | None) -> Meeting | None:
            return find_meeting(meetings, meeting_id=meeting_id, title_hint=title_hint)

    if send_email_fn is None:
        def send_email_fn(recipients: list, subject: str, body: str) -> dict:
            return send_followup_email(sent, subject, body, recipients)

    messages: list[dict[str, object]] = [
        {"role": "system", "content": load_system_prompt(user_name)},
        {"role": "user", "content": request},
    ]

    reply_parts: list[str] = []

    for _ in range(_MAX_TOOL_ROUNDS):
        response = client.create(
            model=_MODEL,
            messages=messages,
            tools=_TOOL_SCHEMAS,
            temperature=0,
            **generation_name_kwargs("run-followup"),
        )
        message = response.choices[0].message  # type: ignore[attr-defined]
        tool_calls = getattr(message, "tool_calls", None)

        if message.content:  # type: ignore[union-attr]
            reply_parts.append(message.content.strip())  # type: ignore[union-attr]

        if not tool_calls:
            return "\n\n".join(reply_parts)

        messages.append(message)  # type: ignore[arg-type]
        for tool_call in tool_calls:
            args = json.loads(tool_call.function.arguments)
            result = _dispatch_tool(tool_call.function.name, args, get_meeting_fn, send_email_fn, user_name)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})

    raise RuntimeError("Follow-Up Agent exceeded its tool-call budget without a final answer")
