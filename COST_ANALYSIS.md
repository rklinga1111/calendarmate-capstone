# Cost Analysis & Production Metrics

CalendarMate — pulled directly from real Langfuse trace data, not estimated.

Source: Langfuse Metrics API (`langfuse.api.metrics.metrics()`), project traces to date — dev, eval-harness, and live-account traffic combined unless noted.

## At a glance

| | |
|---|---|
| **Total traced spend** | $0.199 (808,383 tokens · 1,299 traced steps) |
| **Cost per live request** | $0.0005 (real account usage only) |
| **Error rate** | 0.85% (11 of 1,299 — all the intentional fallback path, zero real crashes) |
| **Eval quality** | 18/18 harness cases · 162/162 unit tests |

## Where the cost goes

Every named agent/tool span, ranked by total spend across all traced runs to date:

| Component | Cost | Calls |
|---|---|---|
| `answer-email` | $0.0647 | 82 |
| `handle-scheduling` | $0.0353 | 246 |
| `classify-emails-batch` | $0.0276 | 5 |
| `judge-verdict` | $0.0160 | 72 |
| `answer-briefing` | $0.0120 | 175 |
| `classify-intent` | $0.0054 | 255 |
| `run-followup` | $0.0010 | 52 |

Excludes $0.037 (91 calls) recorded under the generic label "OpenAI-generation" — unnamed generations from before this project's generation-naming fix, already resolved and not representative of current behavior. `judge-verdict` is the eval harness's own LLM-judge cost, not a product-serving cost.

> **The Email Agent alone accounts for ~33% of all traced spend** despite being 1 of 5 agents — the direct, measured cost of the deliberate `gpt-4o` upgrade for its action-required/FYI classification accuracy (see Model comparison below). Known and accepted, not a leak.

## Model comparison

**`gpt-4o-2024-08-06`** (Email Agent only)
- Total cost: $0.1106
- Tokens: 55,570
- Calls: 25
- Effective $/1K tokens: $1.991

**`gpt-4o-mini-2024-07-18`** (every other agent)
- Total cost: $0.0885
- Tokens: 752,813
- Calls: 373
- Effective $/1K tokens: $0.118

`gpt-4o` runs ~16.9x more expensive per token than `gpt-4o-mini` in this project's own measured usage — consistent with the public pricing ratio, confirmed empirically rather than assumed.

## Latency

**Eval harness (`run-orchestrator`)**
- Average: 3.56s
- P95: 8.80s
- Sample size: 129 runs

**Live account (`handle-request`)**
- Average: 4.89s
- P95: 6.09s
- Sample size: 6 runs

Harness latency includes multi-round tool loops (Scheduler/Follow-Up can call several tools per request) and the LLM judge's own grading call is excluded from this figure. The small live-account sample reflects actual personal usage, not a load test.

## Production metrics tracked

Four metrics, pulled from real trace data rather than estimated, covering cost, speed, reliability, and quality:

| Metric | Value | Why it matters |
|---|---|---|
| Cost per request | $0.00046 (live) / $0.00154 (blended dev+eval avg) | Direct unit economics — what one real request actually costs to serve. |
| Latency (P95) | 8.80s (eval) / 6.09s (live) | The slow-tail experience, not just the average — what a real user actually waits through. |
| Error / fallback rate | 0.85% (11 / 1,299 traced steps) | How often the system hits its designed rejection path vs. crashing — every one of the 11 is `route_request`'s intentional guard, not a real failure. |
| Evaluation pass rate | 18/18 harness · 162/162 unit tests | Functional correctness against both natural-language acceptance criteria and deterministic per-agent assertions. |

## Environment split

| Environment | Cost | Traced steps | Share of spend |
|---|---|---|---|
| Dev / eval harness | $0.1963 | 1,265 | 98.6% |
| Production (live account) | $0.0028 | 34 | 1.4% |

Almost all traced spend is development and evaluation cost, not production cost — expected for a capstone project still in active iteration. Real per-request cost (above) is what would scale with actual usage.

## Methodology

All figures pulled live via Langfuse's Metrics API (`langfuse.api.metrics.metrics()`) against this project's own traces — not sampled, not modeled. Three leftover diagnostic traces from earlier development (`should-fail-if-unpatched`, `test-inner-userid`, `test-outer-userid`) were found and deleted before pulling these final numbers, the same cleanup discipline already documented for this project's other leftover-trace incidents. `pytest` is excluded throughout, since it never traces (`LANGFUSE_TRACING_ENABLED=false`).
