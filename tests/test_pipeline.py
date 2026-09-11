import datetime as dt
import os

import pytest

from calendarmate import pipeline
from fakes import ScriptedChatClient, final_response


def _next_weekday() -> dt.date:
    """The next real calendar date after today that isn't a weekend.

    `run_orchestrator` has no way to inject a fixed `today` (see
    CLAUDE.md's harness section), so a live scheduling test here is
    stuck with the real wall clock. A bare "tomorrow" is fine most days
    but silently trips the scheduler's weekend-confirmation gate on a
    Friday or Saturday run -- an explicit, computed weekday date sidesteps
    that regardless of which real day this test happens to run on.
    """
    day = dt.date.today() + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day


def _classify_as(label: str) -> ScriptedChatClient:
    """A client whose only scripted response is the Orchestrator's
    classification -- the dispatched agent function is monkeypatched out
    in these tests, so it never needs a second scripted response."""
    return ScriptedChatClient([final_response(label)])


@pytest.mark.parametrize(
    "category,request_text",
    [
        ("briefing", "What does my day look like?"),
        ("scheduling", "Schedule a meeting with Bob"),
        ("email", "Summarize my emails"),
        ("follow_up", "Send a follow-up for the design review"),
        ("digest", "What needs my attention this week?"),
    ],
)
def test_run_orchestrator_dispatches_to_the_classified_agent(
    monkeypatch: pytest.MonkeyPatch, category: str, request_text: str
) -> None:
    client = _classify_as(category)
    calls: list[tuple[str, object]] = []

    def fake_agent(request: str, client: object, *, user_id: str | None = None) -> str:
        calls.append((request, client))
        return f"{category.upper()} RESULT"

    monkeypatch.setitem(pipeline._DISPATCH, category, fake_agent)

    result = pipeline.run_orchestrator(request_text, client)

    assert result == f"{category.upper()} RESULT"
    assert calls == [(request_text, client)]


def test_run_orchestrator_never_answers_itself() -> None:
    # Two scripted responses: one for the Orchestrator's classification
    # call, one for the (real, not monkeypatched) Email Agent's own
    # generation call. If the pipeline tried to answer the request itself
    # instead of dispatching, the agent's call would never happen and
    # this client would still have an unused response left over -- or the
    # result wouldn't match what the agent actually produced.
    client = ScriptedChatClient(
        [final_response("email"), final_response("Here is your inbox summary.")]
    )
    result = pipeline.run_orchestrator("Summarize my emails", client)
    assert result == "Here is your inbox summary."
    assert len(client.calls) == 2


@pytest.mark.parametrize("garbage_label", ["", "not_a_real_category", "  \n"])
def test_run_orchestrator_falls_back_gracefully_on_unclassifiable_request(garbage_label: str) -> None:
    # Real scenario this guards against: "What's the capital of France?"
    # made the live classifier return an empty label, which route_request
    # correctly rejects with ValueError -- without this fallback, that
    # exception would propagate all the way up and crash the caller
    # (it took down a full eval harness run before this fix existed).
    client = _classify_as(garbage_label)
    result = pipeline.run_orchestrator("What's the capital of France?", client)
    assert isinstance(result, str)
    assert result == pipeline.FALLBACK_MESSAGE


live = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="requires OPENAI_API_KEY to call the real model",
)


@pytest.fixture
def live_client():
    import openai

    return openai.OpenAI().chat.completions


@live
def test_pipeline_routes_and_answers_briefing(live_client) -> None:
    result = pipeline.run_orchestrator("What's happening on June 17, 2025?", live_client)
    lowered = result.lower()
    assert "design review" in lowered
    assert "client sync" in lowered


@live
def test_pipeline_routes_and_answers_scheduling(live_client) -> None:
    result = pipeline.run_orchestrator(
        f"Schedule a 30-minute meeting with Bob on {_next_weekday().isoformat()} at 10am.", live_client
    )
    lowered = result.lower()
    assert "bob" in lowered
    assert "meet.google.com" in lowered or "meet link" in lowered


@live
def test_pipeline_routes_and_answers_email(live_client) -> None:
    result = pipeline.run_orchestrator("Summarize my emails", live_client)
    lowered = result.lower()
    assert "bob" in lowered or "budget" in lowered


@live
def test_pipeline_routes_and_answers_followup(live_client) -> None:
    result = pipeline.run_orchestrator(
        "What were the action items from the Q3 Roadmap Sync?", live_client
    )
    lowered = result.lower()
    assert "priya" in lowered
    assert "roadmap" in lowered


@live
def test_pipeline_routes_and_answers_digest(live_client) -> None:
    # An explicit date, for both halves, keeps this grounded in the mock
    # fixture regardless of the real current date -- run_orchestrator has
    # no way to inject a pinned `today`/`events`/`emails` the way
    # test_digest.py's direct call does (see pipeline.py's own note on
    # this same constraint for the other categories).
    result = pipeline.run_orchestrator(
        "Give me a combined overview of my calendar and email for June 17, 2025.", live_client
    )
    lowered = result.lower()
    # calendar half: June 17's real conflict
    assert "design review" in lowered or "client sync" in lowered
    # email half: a real June 17 email
    assert "bob" in lowered or "budget" in lowered or "carol" in lowered
