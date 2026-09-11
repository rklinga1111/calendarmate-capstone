"""Shared Langfuse tracing helpers used by the Orchestrator, every agent,
and a handful of individual tool calls.

A single small module rather than duplicating `get_client()`/span
boilerplate across six-plus files. Safe to import and call
unconditionally everywhere -- including from `pytest` (which never sets
LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY/LANGFUSE_BASE_URL) and from any
scripted fake `ChatClient` -- because Langfuse's own client degrades to
a silent, fast no-op without credentials (logs one warning line, never
raises, never blocks on a network call). Confirmed empirically before
wiring this into shared code every test and `harness.py` also run
through, not assumed.

Each wrapped function keeps its own return value and every existing
internal return path completely unchanged -- `traced_call` wraps the
call from the OUTSIDE (capture whatever the wrapped function returns
exactly once, after it returns) rather than requiring the function body
to explicitly report its own output before each return statement. This
is deliberate: several of the wrapped functions (the Scheduler and
Follow-Up Agents' tool loops especially) return from several different
points, and threading an explicit "report my output now" call through
every one of them would be exactly the kind of invasive, error-prone
change (easy to miss one path) this project avoids elsewhere.
"""

from __future__ import annotations

import sys
from typing import Callable, TypeVar

from langfuse import get_client, propagate_attributes

_T = TypeVar("_T")


def generation_name_kwargs(name: str) -> dict[str, str]:
    """`**`-expand into a `client.create(...)` call to name the resulting
    generation -- e.g. `client.create(..., **generation_name_kwargs("answer-briefing"))`.

    Only actually includes `name=` when it's safe to: `langfuse.openai`'s
    drop-in OpenAI class monkey-patches `Completions.create` GLOBALLY,
    for the entire process, the moment that module is imported --
    confirmed empirically that this affects every client in the process,
    including a separately-constructed plain `openai.OpenAI()`, since
    the patch lives on the shared `Completions` class itself, not on any
    one instance. `live_assistant.py` and `harness.py` both import
    `langfuse.openai`, so the patch is active there and strips `name=`
    before it ever reaches the real API. `pytest` never imports
    `langfuse.openai` anywhere, so in that process the patch is never
    applied and a plain client's `.create()` rejects an unrecognized
    `name` kwarg outright (confirmed the hard way: this exact mistake
    broke the entire live suite once already). Checking whether
    `langfuse.openai` has been imported into the current process is a
    safe, reliable proxy for "is passing name= actually safe here" --
    this is process-wide state, not per-client, which is exactly why a
    static, unconditional `name=` couldn't work but this can.
    """
    if "langfuse.openai" in sys.modules:
        return {"name": name}
    return {}


def trace_agent_call(
    name: str, *, agent: str | None, request: str, fn: Callable[[], _T], user_id: str | None = None
) -> _T:
    """Wraps one top-level Orchestrator/agent call in a named span, input
    set to just the request text (not the client object or any fixture
    data also passed to these functions -- the "don't leak raw function
    args" baseline requirement every one of these follows).

    `agent` tags the CURRENT trace with which agent handled it (e.g.
    "briefing", "scheduling") -- confirmed empirically that nested tag
    propagation ADDS to a trace's tags rather than replacing them, so
    `run_orchestrator`'s own wrapper passes `agent=None` (nothing to add
    from the dispatcher itself) and lets the nested `classify-intent`
    call (tags "orchestrator") and whichever specialist agent actually
    runs (tags its own name) supply the real tags on the same trace.

    `user_id` is optional here for the same reason `agent` is -- a
    caller that has no real identity to attach (an offline test, or
    `run_orchestrator`'s own wrapper, which doesn't know the caller's
    identity any better than the nested calls it dispatches to) passes
    nothing, and does NOT clear a `user_id` an outer caller already
    propagated (confirmed empirically: a nested `propagate_attributes(
    user_id=None)` leaves an already-active real user_id untouched,
    the same "add, don't replace" behavior tags already have). Callers
    that DO have a real identity -- `live_assistant.py`'s authenticated
    Google account email, `harness.py`'s fixed eval-run placeholder --
    pass it explicitly so it's never left to chance whether an outer
    caller remembered to set it via its own `propagate_attributes` call.
    """
    langfuse = get_client()
    with langfuse.start_as_current_observation(as_type="span", name=name, input=request) as span:
        if agent is not None or user_id is not None:
            with propagate_attributes(tags=[agent] if agent is not None else None, user_id=user_id):
                result = fn()
        else:
            result = fn()
        span.update(output=result)
        return result


def trace_tool_call(name: str, args: dict, fn: Callable[[], _T]) -> _T:
    """Wraps one tool invocation (check_availability, create_event,
    get_meeting_record, send_followup_email) in a `tool`-typed span, the
    same observation type Langfuse's own docs recommend for a tool call
    rather than a generic span -- input is the tool's real arguments,
    output its real result, both exactly what the model actually saw.
    """
    langfuse = get_client()
    with langfuse.start_as_current_observation(as_type="tool", name=name, input=args) as span:
        result = fn()
        span.update(output=result)
        return result
