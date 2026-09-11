import os

import pytest

from calendarmate.orchestrator import route_request

SAMPLE_INPUTS = [
    ("What does my day look like?", "briefing"),
    ("Schedule a meeting with X", "scheduling"),
    ("Summarize my emails", "email"),
    ("Send a follow-up for the Q3 Roadmap Sync", "follow_up"),
    ("Did the design review have any action items?", "follow_up"),
    ("What needs my attention this week?", "digest"),
    ("Catch me up", "digest"),
]


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [FakeChoice(content)]


class FakeChatClient:
    """Returns a canned label regardless of input, for testing the
    parsing/validation contract without calling the real API."""

    def __init__(self, label: str) -> None:
        self._label = label

    def create(self, **kwargs: object) -> FakeResponse:
        return FakeResponse(self._label)


@pytest.mark.parametrize("request_text,expected_route", SAMPLE_INPUTS)
def test_route_request_parses_valid_label(request_text: str, expected_route: str) -> None:
    client = FakeChatClient(expected_route)
    assert route_request(request_text, client) == expected_route


def test_route_request_rejects_invalid_label() -> None:
    client = FakeChatClient("do something else")
    with pytest.raises(ValueError):
        route_request("anything", client)


def test_route_request_is_case_and_whitespace_insensitive() -> None:
    client = FakeChatClient("  Briefing  \n")
    assert route_request("What does my day look like?", client) == "briefing"


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="requires OPENAI_API_KEY to call the real model",
)
@pytest.mark.parametrize("request_text,expected_route", SAMPLE_INPUTS)
def test_route_request_live(request_text: str, expected_route: str) -> None:
    import openai

    client = openai.OpenAI().chat.completions
    assert route_request(request_text, client) == expected_route
