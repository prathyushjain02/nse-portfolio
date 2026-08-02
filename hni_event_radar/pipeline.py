"""Run orchestration: discover -> filter -> dedupe -> score -> persist."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .dedupe import deduplicate
from .enrich import enrich
from .models import Event
from .net import Fetcher, DEFAULT_UA
from .scoring import Scorer, score_events
from .sources import build_sources
from .store import Store

log = logging.getLogger(__name__)

CITY_ALIASES = {
    # 'bengluru' is a misspelling common enough in real listings to alias.
    "bengaluru": ("bengaluru", "bangalore", "bengluru", "blr"),
    "mumbai": ("mumbai", "bombay"),
    "delhi": ("delhi", "new delhi", "gurgaon", "gurugram", "noida", "ncr"),
}

# Other Indian metros. If one of these is named and the target city is not,
# the listing is for somewhere else - portals leak neighbouring-city results.
OTHER_CITIES = (
    "mumbai",
    "new delhi",
    "hyderabad",
    "chennai",
    "kolkata",
    "pune",
    "ahmedabad",
    "jaipur",
    "kochi",
    "goa",
    "chandigarh",
    "lucknow",
    # Foreign cities: portals surface these against Indian queries, and an
    # event whose only stated location is Toronto is not a Bengaluru event.
    "toronto",
    "singapore",
    "new york",
    "san francisco",
    "abu dhabi",
    "doha",
    "colombo",
)


@dataclass
class RunResult:
    events: list[Event] = field(default_factory=list)
    new_events: list[Event] = field(default_factory=list)
    disqualified: list[Event] = field(default_factory=list)
    source_errors: dict[str, list[str]] = field(default_factory=dict)
    source_counts: dict[str, int] = field(default_factory=dict)
    fetch_stats: dict[str, int] = field(default_factory=dict)
    enrich_stats: dict[str, int] = field(default_factory=dict)
    city: str = ""
    generated_on: date = field(default_factory=date.today)

    @property
    def tier_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.events:
            tier = next((t.split(":")[1] for t in event.tags if t.startswith("tier:")), "?")
            counts[tier] = counts.get(tier, 0) + 1
        return counts


def _matches_city(event: Event, city: str) -> bool:
    """Keep the event if it plausibly happens in the target city."""
    aliases = CITY_ALIASES.get(city.lower(), (city.lower(),))
    blob = " ".join([event.city, event.venue, event.address, event.title]).lower()

    if any(alias in blob for alias in aliases):
        return True
    # Curated national entries are deliberately out-of-city.
    if "national" in event.tags:
        return True
    # No location info at all: the query itself was city-scoped, so keep it.
    if not blob.strip():
        return True
    return not any(other in blob for other in OTHER_CITIES if other not in aliases)


def _in_horizon(event: Event, today: date, horizon_days: int) -> bool:
    if event.start_date is None:
        return True  # undated recurring circuit entries stay on the radar
    if event.start_date < today:
        # Past curated editions are kept as a "next edition" reminder.
        return "curated" in event.tags
    return event.start_date <= today + timedelta(days=horizon_days)


def run(config: dict[str, Any], *, today: date | None = None, use_cache: bool = True) -> RunResult:
    today = today or date.today()
    city = config["city"]
    http = config["http"]

    contact = (http.get("contact_email") or "").strip()
    user_agent = (
        f"HNIEventRadar/1.0 (wealth-management event research; contact: {contact})"
        if contact
        else DEFAULT_UA
    )

    out_dir = Path(config["output"]["dir"])
    fetcher = Fetcher(
        cache_dir=out_dir / "cache",
        user_agent=user_agent,
        delay_seconds=float(http["delay_seconds"]),
        timeout=int(http["timeout"]),
        max_retries=int(http["max_retries"]),
        cache_ttl_seconds=int(http["cache_ttl_seconds"]),
        respect_robots=bool(http["respect_robots"]),
        use_cache=use_cache,
    )

    result = RunResult(city=city, generated_on=today)
    harvested: list[Event] = []

    for source in build_sources(config["sources"], fetcher, config.get("source_settings")):
        log.info("--- %s (%s)", source.name, source.label)
        found = source.collect(city, config["queries"])
        result.source_counts[source.name] = len(found)
        if source.errors:
            result.source_errors[source.name] = source.errors
        harvested.extend(found)

    log.info("harvested %d raw listings", len(harvested))

    # Filter before scoring - no point scoring a Chennai concert.
    filtered = [
        e
        for e in harvested
        if _matches_city(e, city)
        and _in_horizon(e, today, int(config["horizon_days"]))
        and (config["include_online"] or not e.is_online)
    ]
    log.info("%d listings after city/date/format filters", len(filtered))

    merged = deduplicate(filtered)
    log.info("%d after deduplication", len(merged))

    scorer = Scorer()
    enrich_cfg = config.get("enrich", {})
    if enrich_cfg.get("enabled", True):
        result.enrich_stats = enrich(
            merged,
            fetcher,
            limit=int(enrich_cfg.get("limit", 40)),
            min_prescore=int(enrich_cfg.get("min_prescore", 12)),
            scorer=scorer,
        )

    kept, dropped = score_events(merged, scorer, today=today)
    result.disqualified = dropped

    min_score = int(config["min_score"])
    result.events = [e for e in kept if e.lead_score >= min_score]
    log.info(
        "%d events scored >= %d (dropped %d disqualified, %d below threshold)",
        len(result.events),
        min_score,
        len(dropped),
        len(kept) - len(result.events),
    )

    with Store(config["output"]["database"]) as store:
        result.new_events = store.upsert(result.events)
        store.record_run(city, len(result.events), len(result.new_events), fetcher.stats)

    result.fetch_stats = dict(fetcher.stats)
    return result
