"""The Orchestrator: routes a user request to a specialist agent.

The Orchestrator never answers a request itself -- it only returns a
routing decision. The caller is responsible for dispatching the request
to whichever agent (briefing, scheduling, email) the decision names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from calendarmate.observability import generation_name_kwargs, trace_agent_call

Route = Literal["briefing", "scheduling", "email", "follow_up", "digest"]

_VALID_ROUTES: frozenset[str] = frozenset(
    {"briefing", "scheduling", "email", "follow_up", "digest"}
)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "orchestrator.md"

_MODEL = "gpt-4o-mini"


def load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


class ChatClient(Protocol):
    """The subset of the OpenAI client's `chat.completions` interface we need."""

    def create(self, **kwargs: object) -> object: ...


def route_request(request: str, client: ChatClient, *, user_id: str | None = None) -> Route:
    # Named "classify-intent" (verb-first, per Langfuse's own naming
    # convention) rather than reusing this function's own name -- the
    # span's job is to describe what it does, not mirror the Python
    # symbol calling it.
    return trace_agent_call(
        "classify-intent",
        agent="orchestrator",
        request=request,
        user_id=user_id,
        fn=lambda: _route_request_impl(request, client),
    )


def _route_request_impl(request: str, client: ChatClient) -> Route:
    response = client.create(
        model=_MODEL,
        max_tokens=10,
        temperature=0,
        messages=[
            {"role": "system", "content": load_system_prompt()},
            {"role": "user", "content": request},
        ],
        **generation_name_kwargs("classify-intent"),
    )
    label = response.choices[0].message.content.strip().lower()  # type: ignore[attr-defined]
    if label not in _VALID_ROUTES:
        raise ValueError(f"Orchestrator returned an invalid route: {label!r}")
    return label  # type: ignore[return-value]
