"""Command line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from .config import load_config
from .models import Event
from .pipeline import run
from .report import write_all
from .scoring import Scorer
from .sources import REGISTRY
from .store import Store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname).1s %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _print_table(events: list[Event], limit: int) -> None:
    if not events:
        print("No events matched. Try lowering --min-score or widening --horizon.")
        return
    print(f"\n{'SCORE':>5}  {'T':1}  {'DATE':<12} {'PASS':<16} TITLE")
    print("-" * 100)
    for event in events[: (limit or None)]:
        tier = next((t.split(":")[1] for t in event.tags if t.startswith("tier:")), "?")
        title = event.title[:52]
        print(
            f"{event.lead_score:>5}  {tier:1}  {event.date_label[:12]:<12} "
            f"{event.price_label[:16]:<16} {title}"
        )
        print(f"{'':>5}     -> {event.play}")


def cmd_scrape(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.city:
        config["city"] = args.city
    if args.min_score is not None:
        config["min_score"] = args.min_score
    if args.horizon is not None:
        config["horizon_days"] = args.horizon
    if args.sources:
        config["sources"] = args.sources
    if args.include_online:
        config["include_online"] = True
    if args.out:
        config["output"]["dir"] = args.out
        config["output"]["database"] = str(Path(args.out) / "events.db")

    result = run(config, use_cache=not args.no_cache)

    out_dir = Path(config["output"]["dir"])
    written = write_all(result, out_dir, config=config)

    _print_table(result.events, args.limit)

    print(
        f"\n{len(result.events)} qualified events "
        f"({len(result.new_events)} new) | tiers {result.tier_counts} | "
        f"fetches {result.fetch_stats.get('fetches', 0)}, "
        f"cache hits {result.fetch_stats.get('cache_hits', 0)}"
    )
    for source, errors in result.source_errors.items():
        print(f"  ! {source}: {len(errors)} issue(s) - first: {errors[0][:90]}")
    print("\nWritten:")
    for kind, path in written.items():
        print(f"  {kind:<9} {path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Re-render outputs from the database without touching the network."""
    from .pipeline import RunResult

    config = load_config(args.config)
    with Store(config["output"]["database"]) as store:
        events = store.all_events(min_score=args.min_score or 0)
        runs = store.run_count()

    result = RunResult(events=events, city=config["city"], generated_on=date.today())
    out_dir = Path(args.out or config["output"]["dir"])
    written = write_all(result, out_dir, config=config)
    print(f"Re-rendered {len(events)} stored events from {runs} run(s).")
    for kind, path in written.items():
        print(f"  {kind:<9} {path}")
    return 0


def cmd_sources(_: argparse.Namespace) -> int:
    print("Available sources:\n")
    for name, cls in REGISTRY.items():
        print(f"  {name:<12} {cls.label}")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    """Score an ad-hoc event description - useful for tuning the taxonomy."""
    event = Event(
        source="manual",
        title=args.title,
        url="",
        description=args.description or "",
        venue=args.venue or "",
        price_min=args.price,
        price_max=args.price,
    )
    breakdown = Scorer().score(event)
    print(f"\n{event.title}")
    print(f"  lead {breakdown.lead}/100  tier {breakdown.tier}")
    print(
        f"  prospect {breakdown.prospect}  access {breakdown.access}  peer {breakdown.peer}"
    )
    print(f"  play: {breakdown.play}\n")
    for signal in breakdown.signals:
        print(f"    {signal}")
    if breakdown.warnings:
        print("\n  warnings: " + "; ".join(breakdown.warnings))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hni_event_radar",
        description="Find events where a wealth manager can meet HNI/UHNI prospects.",
    )
    parser.add_argument("-c", "--config", help="path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape", help="run the full discovery pipeline")
    scrape.add_argument("--city", help="target city (default from config)")
    scrape.add_argument("--min-score", type=int, help="drop events below this lead score")
    scrape.add_argument("--horizon", type=int, help="how many days ahead to look")
    scrape.add_argument("--sources", nargs="+", choices=list(REGISTRY), help="limit sources")
    scrape.add_argument("--include-online", action="store_true")
    scrape.add_argument("--no-cache", action="store_true", help="bypass the HTTP cache")
    scrape.add_argument("--out", help="output directory")
    scrape.add_argument("--limit", type=int, default=20, help="rows to print")
    scrape.set_defaults(func=cmd_scrape)

    report = sub.add_parser("report", help="re-render reports from stored events")
    report.add_argument("--min-score", type=int)
    report.add_argument("--out")
    report.set_defaults(func=cmd_report)

    sources = sub.add_parser("sources", help="list available sources")
    sources.set_defaults(func=cmd_sources)

    explain = sub.add_parser("explain", help="score a hypothetical event")
    explain.add_argument("title")
    explain.add_argument("--description")
    explain.add_argument("--venue")
    explain.add_argument("--price", type=float)
    explain.set_defaults(func=cmd_explain)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
