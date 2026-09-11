import os
from datetime import date

import pytest

from calendarmate import digest as digest_module
from calendarmate.digest import answer_digest_request
from calendarmate.tools.calendar_tool import load_events
from calendarmate.tools.email_tool import load_emails

TODAY = date(2025, 6, 17)  # Tuesday, same fixture week as the other agents


def test_answer_digest_request_combines_both_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolate digest.py's own combination logic from the two sub-agents'
    # (already independently tested) generation logic.
    calls = []

    def fake_briefing(request, client, *, today=None, events=None, user_id=None):
        calls.append(("briefing", request, today, events))
        return "You have 2 meetings today."

    def fake_email(request, client, *, today=None, emails=None, own_email=None, user_id=None):
        calls.append(("email", request, today, emails))
        return "Bob needs your approval on the budget."

    monkeypatch.setattr(digest_module, "answer_briefing", fake_briefing)
    monkeypatch.setattr(digest_module, "answer_email_request", fake_email)

    result = answer_digest_request(
        "What needs my attention this week?", client=object(), today=TODAY, events=[], emails=[]
    )

    assert "You have 2 meetings today." in result
    assert "Bob needs your approval on the budget." in result
    assert "Calendar" in result
    assert "Email" in result
    # both sub-agents got the same request and today, and their own data
    assert calls[0] == ("briefing", "What needs my attention this week?", TODAY, [])
    assert calls[1] == ("email", "What needs my attention this week?", TODAY, [])


live = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="requires OPENAI_API_KEY to call the real model",
)


@pytest.fixture
def live_client():
    import openai

    return openai.OpenAI().chat.completions


@live
def test_digest_grounds_both_calendar_and_email(live_client) -> None:
    result = answer_digest_request(
        "What needs my attention this week?",
        live_client,
        today=TODAY,
        events=load_events(),
        emails=load_emails(),
    )
    lowered = result.lower()
    # calendar half: this week's real conflicts should be grounded
    assert "design review" in lowered or "client sync" in lowered or "sprint retro" in lowered
    # email half: a real action-required item should be grounded
    assert "bob" in lowered or "budget" in lowered or "carol" in lowered
