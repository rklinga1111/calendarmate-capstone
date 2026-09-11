"""Deletes Langfuse traces older than a configurable age, run manually.

Langfuse's configurable data-retention (auto-delete) feature requires a
paid plan; this is the Hobby-plan substitute -- CLAUDE.md's "Observability
(Langfuse)" section has the full context on why this exists. Defaults to
a dry run (counts what WOULD be deleted, deletes nothing) since deleting
trace data is irreversible; pass --confirm to actually delete.

Usage:
    python cleanup_old_traces.py                 # dry run, 30-day default
    python cleanup_old_traces.py --confirm        # actually delete
    python cleanup_old_traces.py --days 7 --confirm
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from langfuse import get_client

_DEFAULT_MAX_AGE_DAYS = 30
_DELETE_BATCH_SIZE = 100


def _find_old_trace_ids(langfuse: object, cutoff: datetime) -> list[str]:
    trace_ids: list[str] = []
    page = 1
    while True:
        result = langfuse.api.trace.list(to_timestamp=cutoff, page=page, limit=100)  # type: ignore[attr-defined]
        trace_ids.extend(t.id for t in result.data)
        if page >= result.meta.total_pages:
            break
        page += 1
    return trace_ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--days", type=int, default=_DEFAULT_MAX_AGE_DAYS, help="delete traces older than this many days (default: 30)"
    )
    parser.add_argument("--confirm", action="store_true", help="actually delete (default: dry run only)")
    args = parser.parse_args()

    langfuse = get_client()
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    trace_ids = _find_old_trace_ids(langfuse, cutoff)
    if not trace_ids:
        print(f"No traces older than {args.days} days found. Nothing to do.")
        return

    if not args.confirm:
        print(f"DRY RUN: {len(trace_ids)} trace(s) older than {args.days} days would be deleted.")
        print("Re-run with --confirm to actually delete them.")
        return

    deleted = 0
    for start in range(0, len(trace_ids), _DELETE_BATCH_SIZE):
        batch = trace_ids[start : start + _DELETE_BATCH_SIZE]
        langfuse.api.trace.delete_multiple(trace_ids=batch)  # type: ignore[attr-defined]
        deleted += len(batch)
        print(f"Deleted {deleted}/{len(trace_ids)}...")

    print(f"Done. Deleted {deleted} trace(s) older than {args.days} days.")


if __name__ == "__main__":
    main()
