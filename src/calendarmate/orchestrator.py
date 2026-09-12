"""The Orchestrator: routes a user request to a specialist agent.

The Orchestrator never answers a request itself -- it only returns a
routing decision. The caller is responsible for dispatching the request
to whichever agent (briefing, scheduling, email) the decision names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from calendarmate.observability import generation_name_kwargs, trace_agent_call

Route = Literal["briefing", "scheduling", "email", "follow_up", "digest", "chitchat"]

_VALID_ROUTES: frozenset[str] = frozenset(
    {"briefing", "scheduling", "email", "follow_up", "digest", "chitchat"}
)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "orchestrator.md"

_MODEL = "gpt-4o-mini"

# Pure conversational input -- no calendar, scheduling, email, or
# follow-up content at all -- was being force-classified into `digest`
# (the broadest category) or an empty/invalid label, either way costing
# a real classification call and, for `digest`, two full agent calls
# (Briefing + Email, the latter on the more expensive gpt-4o) for a
# plain "hi" or "thanks". Matched here, in code, before any model call,
# since these are a closed, exact set of phrasings -- not something that
# needs an LLM to recognize. `chitchat` (below, in _VALID_ROUTES) is the
# model-classified fallback for anything this exact-match list misses.
_GREETINGS = {"hi", "hello", "hey", "hiya", "yo", "howdy", "greetings",
              "good morning", "good afternoon", "good evening"}
_WELLBEING = {"how are you", "how are you doing", "how's it going",
              "hows it going", "what's up", "whats up", "sup"}
_THANKS = {"thanks", "thank you", "thanks a lot", "thank you so much",
           "ok thanks", "okay thanks", "thx", "ty", "much appreciated",
           "appreciate it"}
_FAREWELLS = {"bye", "goodbye", "see you", "see ya", "later",
              "talk later", "take care"}
_CAPABILITY = {"what can you do", "what do you do", "what are you",
               "what is this"}
_IDENTITY = {"who are you"}

# Each sub-category gets its own reply, not one reused generic line --
# "bye" and "who are you" call for genuinely different responses, and
# collapsing them all into one made the assistant sound scripted.
CHITCHAT_REPLIES: dict[str, str] = {
    "greeting": "Hi! I can help with your calendar, meetings, or email -- what do you need?",
    "wellbeing": "Doing well, thanks for asking! What can I help you with -- your calendar, email, or scheduling something?",
    "thanks": "You're welcome! Let me know if you need anything else.",
    "capability": "I can check your calendar, schedule or reschedule meetings, summarize your email, and follow up on action items after a meeting. What would you like to start with?",
    "identity": "I'm CalendarMate -- your assistant for calendar, email, and meeting follow-ups. What can I help with?",
    "farewell": "Bye! Reach out anytime you need help with your schedule or inbox.",
    # The model-classified fallback (chitchat_precheck missed it, but
    # route_request's own classifier still recognized it as pure
    # conversational input) doesn't know which sub-category it was --
    # a neutral variant of the capability reply covers that case.
    "other": "I can help with your calendar, email, scheduling, or meeting follow-ups -- what would you like to do?",
}


def _normalize(text: str) -> str:
    return text.strip().lower().rstrip("!?.,;: ")


def chitchat_precheck(request: str) -> str | None:
    """Returns a direct reply for pure conversational input, or None if
    the request should go through classify-then-dispatch as normal. No
    model call -- exact-match against a closed set of common phrasings,
    so this never mistakes a real request ("hi, can you check my
    calendar") for chitchat, since the whole (normalized) message must
    match, not just contain, one of these phrases."""
    normalized = _normalize(request)
    if normalized in _GREETINGS:
        return CHITCHAT_REPLIES["greeting"]
    if normalized in _WELLBEING:
        return CHITCHAT_REPLIES["wellbeing"]
    if normalized in _THANKS:
        return CHITCHAT_REPLIES["thanks"]
    if normalized in _FAREWELLS:
        return CHITCHAT_REPLIES["farewell"]
    if normalized in _CAPABILITY:
        return CHITCHAT_REPLIES["capability"]
    if normalized in _IDENTITY:
        return CHITCHAT_REPLIES["identity"]
    return None


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
