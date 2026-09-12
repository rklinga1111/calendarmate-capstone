"""Wires the Orchestrator's routing decision to the matching specialist
agent, producing the final answer for one end-to-end request. This is
the one place all the epics actually connect -- everywhere else, each
piece (the classifier, each agent) was only ever exercised on its own.

`run_orchestrator` does exactly two things: classify, then dispatch. It
never answers a request itself, same discipline as `route_request` --
that's the whole point of keeping this as a thin seam rather than
folding dispatch logic into `orchestrator.py` itself.
"""

from __future__ import annotations

from typing import Callable, Protocol

from calendarmate.briefing import answer_briefing
from calendarmate.digest import answer_digest_request
from calendarmate.email import answer_email_request
from calendarmate.followup import run_followup_agent
from calendarmate.observability import trace_agent_call
from calendarmate.orchestrator import CHITCHAT_REPLIES, Route, chitchat_precheck, route_request
from calendarmate.scheduler import handle_scheduling_request


class ChatClient(Protocol):
    def create(self, **kwargs: object) -> object: ...


AgentFn = Callable[[str, ChatClient], str]

_DISPATCH: dict[Route, AgentFn] = {
    "briefing": answer_briefing,
    "scheduling": handle_scheduling_request,
    "email": answer_email_request,
    "follow_up": run_followup_agent,
    "digest": answer_digest_request,
}


FALLBACK_MESSAGE = (
    "I'm not sure how to help with that -- I can help with your calendar, "
    "scheduling a meeting, your email, following up on a past meeting, or "
    "a combined overview of your calendar and email. Could you rephrase "
    "your request in those terms?"
)


def run_orchestrator(request: str, client: ChatClient, *, user_id: str | None = None) -> str:
    # `agent=None`: this span itself isn't any specific agent -- the
    # nested `route_request` call already tags the trace "orchestrator",
    # and whichever specialist agent actually runs tags its own name on
    # the same trace (nested tag propagation adds rather than replaces,
    # confirmed empirically), so this wrapper doesn't need to add a
    # redundant tag of its own. `user_id` IS forwarded here, though --
    # unlike `agent`, there's no nested call that would otherwise supply
    # it if the caller (e.g. harness.py) doesn't pass one in.
    return trace_agent_call(
        "run-orchestrator",
        agent=None,
        request=request,
        user_id=user_id,
        fn=lambda: _run_orchestrator_impl(request, client, user_id=user_id),
    )


def _run_orchestrator_impl(request: str, client: ChatClient, *, user_id: str | None = None) -> str:
    # Pure conversational input ("hi", "thanks", "what can you do") is
    # matched here, before route_request is even called -- no model call,
    # no agent dispatch. Without this, a greeting was being forced into
    # `digest` (two full agent calls, one on the more expensive gpt-4o)
    # or an empty/invalid label, for input that was never asking about a
    # calendar or inbox at all.
    precheck_reply = chitchat_precheck(request)
    if precheck_reply is not None:
        return precheck_reply

    try:
        category = route_request(request, client, user_id=user_id)
    except ValueError:
        # A request that doesn't fit any of the categories at all (e.g.
        # "What's the capital of France?") can make the classifier
        # return something unparseable rather than force a wrong label --
        # that's `route_request` correctly refusing to guess, not a bug,
        # so the fallback here is a real answer to a real case, not
        # defensive code for something that can't happen.
        return FALLBACK_MESSAGE
    if category == "chitchat":
        # The model-classified fallback for conversational input the
        # exact-match precheck above didn't catch -- never dispatched to
        # an agent, same as the precheck path above.
        return CHITCHAT_REPLIES["other"]
    return _DISPATCH[category](request, client, user_id=user_id)
