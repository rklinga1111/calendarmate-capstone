"""Runs the CalendarMate baseline eval: all 12 backlog acceptance tests
(T1-T12) through the real end-to-end pipeline (classification + dispatch
+ grounded generation), graded by an LLM judge against each case's
acceptance criteria.

The project's pytest suite already covers this ground per-agent, with
deterministic keyword assertions. This harness is the natural-language,
judged counterpart: the first pass that runs every backlog story through
`run_orchestrator()` -- classification and dispatch together -- rather
than calling each agent function directly.

Usage: python harness.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Langfuse must be imported AFTER load_dotenv() (so LANGFUSE_PUBLIC_KEY/
# LANGFUSE_SECRET_KEY/LANGFUSE_BASE_URL are already in the environment)
# and its OpenAI class used in place of the plain SDK's -- same gotcha
# documented in live_assistant.py. Every case's run_orchestrator() call
# is now traced the same way a real live_assistant.py request is; the
# per-case tag below (the case id, e.g. "T6") is what tells them apart
# in Langfuse, on top of the agent-name tags run_orchestrator's own
# instrumentation already adds.
from langfuse import get_client, propagate_attributes
from langfuse.openai import OpenAI

import calendarmate.briefing as briefing_module
import calendarmate.email as email_module
import calendarmate.scheduler as scheduler_module
from calendarmate.pipeline import run_orchestrator
from judge_openai import judge_response

_CASES_PATH = Path(__file__).parent / "baseline_cases.json"
_RESULTS_PATH = Path(__file__).parent / "eval_results.json"

# This is a capstone eval-harness run, not a real user session -- there
# is no real account behind it (the mocked fixtures stand in for
# Alice's calendar/inbox), so every trace it produces gets this fixed
# placeholder rather than either leaving user_id unset or inventing a
# fake-looking real identifier.
_TEST_USER_ID = "capstone-test-user"

# baseline_cases.json's date-dependent requests are written against fixed
# June 2025 dates matching calendar.json/inbox.json (see CLAUDE.md's
# "Evaluation harness" section) because run_orchestrator's uniform
# (request, client) signature has no way to inject a fixed `today` the
# way agent-level pytest fixtures do. That was fine as long as real wall-
# clock time stayed close to the fixture window -- but scheduler.py's
# already-passed-time booking guard compares any requested time against
# `now`, which defaults to midnight of `date.today()` when nothing else
# pins it. Once the real date drifted well past June 2025, every one of
# those June-2025 requests started looking like a request to book in the
# past, and the guard correctly-but-unhelpfully refused all of them --
# not a product bug, just this reproducibility mechanism no longer
# reproducing anything. Pinning `date.today()` for the three modules
# that call it directly restores the same guarantee pytest's explicit
# `today=` injection already has, scoped to this process only.
_FIXTURE_TODAY = date(2025, 6, 17)


class _PinnedDate(date):
    @classmethod
    def today(cls) -> date:
        return _FIXTURE_TODAY


def _pin_today_to_fixture_window() -> None:
    for module in (briefing_module, email_module, scheduler_module):
        module.date = _PinnedDate


class SpyingChatClient:
    """Wraps a real chat client and records each tool call's name,
    arguments, and eventual result across a run -- so the judge can check
    process and grounding criteria ("checks availability for everyone
    named", "never invents email content") against what the agent
    actually did and actually got back, not just guess from its final
    reply. A trace of tool *names* alone isn't enough: e.g.
    `propose_alternative_slots` inherently checks every attendee passed
    to it, but a judge can't know that without seeing the arguments, and
    a judge can't verify "never invents content" without seeing what the
    tool actually returned. Agents only need `.create(**kwargs) ->
    response`, so this is a transparent drop-in wherever a `ChatClient`
    is expected."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self._calls: list[dict] = []  # [{"id", "tool", "arguments"}]
        self._results: dict[str, str] = {}  # tool_call_id -> result content

    def create(self, **kwargs: object) -> object:
        for message in kwargs.get("messages", []) or []:  # type: ignore[union-attr]
            if isinstance(message, dict) and message.get("role") == "tool":
                self._results[message["tool_call_id"]] = message["content"]

        response = self._inner.create(**kwargs)  # type: ignore[attr-defined]
        message = response.choices[0].message
        for tool_call in getattr(message, "tool_calls", None) or []:
            self._calls.append(
                {"id": tool_call.id, "tool": tool_call.function.name, "arguments": tool_call.function.arguments}
            )
        return response

    @property
    def tool_calls_made(self) -> list[str]:
        return [call["tool"] for call in self._calls]

    def trace_lines(self) -> list[str]:
        lines = []
        for call in self._calls:
            result = self._results.get(call["id"], "(no result observed)")
            lines.append(f"{call['tool']}(arguments={call['arguments']}) -> {result}")
        return lines


def main() -> None:
    _pin_today_to_fixture_window()
    cases = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    # The real, per-case client: Langfuse-wrapped so every LLM call
    # run_orchestrator makes while answering a case is traced. The judge
    # reuses this same client rather than constructing its own plain
    # `openai.OpenAI()` -- there's no way to keep a SEPARATE client
    # genuinely untraced once `langfuse.openai` has been imported
    # anywhere in this process: that import monkey-patches
    # `Completions.create` globally (confirmed empirically), so even an
    # independently-constructed plain client picks it up. Since the
    # judge's call was ending up traced either way -- just anonymously,
    # as an untagged, unnamed "OpenAI-generation" -- the actual fix is
    # giving it a real name (`judge_openai.py`'s own `name="judge-verdict"`)
    # and tagging its trace explicitly below, not pretending it's excluded.
    raw_client = OpenAI().chat.completions
    judge_client = raw_client
    langfuse = get_client()

    results = []
    passed_count = 0

    for case in cases:
        print(f"\n=== {case['id']} (Story {case['story']}) ===")
        print(f"Request: {case['request']}")

        spy = SpyingChatClient(raw_client)
        try:
            with propagate_attributes(tags=[case["id"]]):
                response = run_orchestrator(case["request"], spy, user_id=_TEST_USER_ID)
        except Exception as exc:  # noqa: BLE001 -- one bad case must not kill the whole eval run
            print(f"ERROR: {exc!r}")
            results.append(
                {
                    "id": case["id"],
                    "story": case["story"],
                    "request": case["request"],
                    "response": None,
                    "error": repr(exc),
                    "verdict": {"passed": False, "criteria_results": [], "overall_reasoning": f"Crashed: {exc!r}"},
                }
            )
            continue
        print(f"Response:\n{response}\n")
        if spy.tool_calls_made:
            print(f"Tools called: {spy.tool_calls_made}")

        with propagate_attributes(tags=[case["id"], "judge"], user_id=_TEST_USER_ID):
            verdict = judge_response(
                case["request"], case["criteria"], response, judge_client, tool_trace=spy.trace_lines()
            )
        status = "PASS" if verdict["passed"] else "FAIL"
        print(f"Judge: {status} -- {verdict['overall_reasoning']}")
        for cr in verdict["criteria_results"]:
            mark = "x" if cr["met"] else " "
            print(f"  [{mark}] {cr['criterion']} -- {cr['reasoning']}")

        if verdict["passed"]:
            passed_count += 1

        results.append(
            {
                "id": case["id"],
                "story": case["story"],
                "request": case["request"],
                "response": response,
                "verdict": verdict,
            }
        )

    _RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")

    # Batch script, not a long-running server -- without an explicit
    # flush, buffered trace events for the last few cases can be lost
    # when the process exits right after this.
    langfuse.flush()

    print(f"\n{'=' * 40}")
    print(f"RESULT: {passed_count}/{len(cases)} passed")
    print(f"Full results written to {_RESULTS_PATH}")


if __name__ == "__main__":
    main()
