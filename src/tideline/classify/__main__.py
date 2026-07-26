"""Classification CLI: `python -m tideline.classify [estimate|run]`.

`estimate` measures real token counts against a sample and projects the backfill cost
without classifying anything. Run it before `run` — the backfill is the single largest
spend in this project, and PLAN §7 sets a $15/month ceiling worth checking against.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from tideline.classify.classifier import MODEL, estimate_cost, run_classification
from tideline.db import DEFAULT_DB_PATH, connect, init_schema, unclassified_jobs

log = logging.getLogger("tideline.classify")


def _client():
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        sys.exit("anthropic SDK not installed. Run: uv sync --extra classify")
    return anthropic.Anthropic()


def cmd_estimate(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    init_schema(conn)
    pending = unclassified_jobs(conn)
    if not pending:
        print("Nothing pending — all jobs are classified.")
        return 0

    sample = pending[: args.sample]
    stats = estimate_cost(_client(), sample, batch_discount=not args.no_batch)
    total = stats["cost_per_job"] * len(pending)

    print(f"model                 {MODEL}")
    print(f"pending jobs          {len(pending):,}")
    print(f"sampled               {len(sample)}")
    print()
    print(f"system prompt         {stats['system_prompt_tokens']:,.0f} tokens")
    print(
        f"cache eligible        {'yes' if stats['cache_eligible'] else 'NO — under the 4096 floor'}"
    )
    print(f"mean per-job tokens   {stats['mean_job_tokens']:,.0f}")
    print()
    pricing = "sync" if args.no_batch else "batch"
    print(f"cost per job          ${stats['cost_per_job']:.5f}")
    print(f"backfill total        ${total:,.2f}   ({pricing} pricing)")
    print()
    print("Note: the first request writes the cache at 1.25x; every later request in the")
    print("same 5-minute window reads it at 0.1x. Long batches re-write periodically, so")
    print("treat this as a floor rather than an exact figure.")
    conn.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    init_schema(conn)
    result = run_classification(conn, _client(), limit=args.limit, force_sync=args.sync)
    conn.commit()
    conn.close()
    log.info(
        "collected %d, submitted %d, classified inline %d",
        result["collected"],
        result["submitted"],
        result["classified_sync"],
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tideline.classify")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    est = sub.add_parser("estimate", help="Measure tokens and project backfill cost.")
    est.add_argument("--sample", type=int, default=25, help="Jobs to measure (default 25).")
    est.add_argument("--no-batch", action="store_true", help="Price at sync rates.")
    est.set_defaults(func=cmd_estimate)

    run = sub.add_parser("run", help="Collect finished batches, then dispatch pending jobs.")
    run.add_argument("--limit", type=int, help="Only process the first N pending jobs.")
    run.add_argument("--sync", action="store_true", help="Force the synchronous path.")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
