"""Shared fakes for scripting OpenAI-shaped chat completion responses."""

from __future__ import annotations

import json


class FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: dict) -> None:
        self.id = call_id
        self.function = FakeFunction(name, json.dumps(arguments))


class FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message


class FakeResponse:
    def __init__(self, message: FakeMessage) -> None:
        self.choices = [FakeChoice(message)]


class ScriptedChatClient:
    """Replays one canned response per `.create()` call, and records the
    kwargs each call was made with, so tests can inspect what the agent
    sent back to the model (e.g. a tool's result)."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> FakeResponse:
        # `messages` is mutated (appended to) by the caller after every
        # call, so snapshot it now -- otherwise every recorded call would
        # end up pointing at the same final list.
        snapshot = dict(kwargs)
        if "messages" in snapshot:
            snapshot["messages"] = list(snapshot["messages"])
        self.calls.append(snapshot)
        return self._responses.pop(0)


def tool_call_response(call_id: str, name: str, arguments: dict) -> FakeResponse:
    return tool_calls_response([(call_id, name, arguments)])


def tool_calls_response(calls: list[tuple[str, str, dict]]) -> FakeResponse:
    return FakeResponse(
        FakeMessage(tool_calls=[FakeToolCall(cid, name, args) for cid, name, args in calls])
    )


def text_and_tool_call_response(text: str, call_id: str, name: str, arguments: dict) -> FakeResponse:
    """A single turn carrying both reply text and a tool call -- real
    models sometimes do this (e.g. state a fact, then call a tool), and
    agent loops must not discard the text just because tool_calls is also
    present."""
    return FakeResponse(
        FakeMessage(content=text, tool_calls=[FakeToolCall(call_id, name, arguments)])
    )


def final_response(text: str) -> FakeResponse:
    return FakeResponse(FakeMessage(content=text))
