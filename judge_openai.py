"""LLM-as-judge for the CalendarMate baseline eval.

Scores one agent response against a case's acceptance criteria with a
real model call, since the criteria are natural-language judgments
("mentions the conflict", "asks clarifying questions") that a simple
keyword match can't reliably check across arbitrary phrasing -- the
project's own pytest suite already has to work around exactly this with
broad keyword lists for its live tests.
"""

from __future__ import annotations

import json
from typing import Protocol

_MODEL = "gpt-4o-mini"

_JUDGE_SYSTEM_PROMPT = """You are grading an AI assistant's response
against a specific set of acceptance criteria for a request.

You are given the response text, and (when available) a trace of the
backend tool calls the assistant actually made while producing it, in
order -- each line shows the tool name, the exact arguments it was
called with, and the exact result it got back. Use this trace for:
- process criteria ("checks availability before creating", "checks
  availability for all three attendees") -- check the tool's
  ARGUMENTS, not just whether the tool ran. A tool called with a list
  of attendees checked all of them, even if the reply text doesn't
  narrate that explicitly.
- grounding criteria ("never invents email content", "never invents a
  meeting") -- check the tool's RESULT as ground truth. If a name,
  subject, or detail in the reply appears in a tool result, it is NOT
  invented, no matter how specific or detailed it looks. Only mark
  "invents" criteria as failed when something in the reply has no
  basis in any tool result you were given.

Judge criteria purely about the reply's own content (what it says, what
tone it takes, whether it asks a question) from the response text
itself.

For each criterion, decide whether it's satisfied. Be strict: a
criterion is only met if the evidence (response text and/or tool trace)
actually demonstrates it, not merely if the response seems reasonable
overall.

Respond with ONLY valid JSON in this exact shape:
{
  "criteria_results": [
    {"criterion": "<criterion text>", "met": true or false, "reasoning": "<one sentence>"}
  ],
  "passed": true or false,
  "overall_reasoning": "<one or two sentences>"
}

"passed" is true only if every criterion is met.
"""


class ChatClient(Protocol):
    def create(self, **kwargs: object) -> object: ...


def judge_response(
    request: str,
    criteria: list[str],
    response: str,
    client: ChatClient,
    *,
    tool_trace: list[str] | None = None,
) -> dict:
    if tool_trace:
        trace_block = "Tool calls made, in order:\n" + "\n".join(f"- {line}" for line in tool_trace)
    else:
        trace_block = "Tool calls made: none"
    user_content = (
        f"Request: {request}\n\n"
        f"{trace_block}\n\n"
        "Acceptance criteria:\n" + "\n".join(f"- {c}" for c in criteria) + "\n\n"
        f"Agent's response:\n{response}"
    )
    result = client.create(
        model=_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        # Unconditional, not generation_name_kwargs()-gated: this module
        # has exactly one caller (harness.py), which always imports
        # langfuse.openai, so the name is always safe to pass here --
        # unlike the shared agent modules, which are also reachable from
        # a process (pytest) that never imports it.
        name="judge-verdict",
    )
    return json.loads(result.choices[0].message.content)  # type: ignore[attr-defined]
