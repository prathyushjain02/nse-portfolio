"""Detail-page enrichment.

Portal listing pages carry a one-line description and rarely a price, which is
not enough to judge whether a room contains HNIs. The event's own page usually
carries the full agenda, the audience description and the ticket tiers - the
three things the scorer actually needs.

Fetching every detail page would be slow and rude, so this pass cheaply
pre-scores the candidates on their thin data and only spends network on the
most promising ones.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

from .models import Event, clean_text
from .net import Fetcher, FetchError
from .scoring import Scorer
from .sources.jsonld import events_from_html

log = logging.getLogger(__name__)

#: Sources whose detail pages contain the description in the delivered HTML.
#: Eventbrite renders its "About this event" body client-side, so scraping its
#: markup yields navigation chrome and related-event titles - false signal that
#: would score an event on copy belonging to a different one.
SERVER_RENDERED = {"allevents", "meetup", "10times"}

_STRIP_TAGS = ("script", "style", "nav", "footer", "header", "noscript", "svg", "form")


def _canonical(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _pick_match(candidates: list[Event], target: Event) -> Event | None:
    """Choose the JSON-LD node on the detail page that is the event itself."""
    if not candidates:
        return None
    target_url = _canonical(target.url)
    for candidate in candidates:
        if candidate.url and _canonical(candidate.url) == target_url:
            return candidate
    # Detail pages lead with their own event; related events come after.
    return candidates[0]


def extract_body_text(html: str, limit: int = 4000) -> str:
    """Pull the readable description text out of a server-rendered page."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover - bs4 is a declared dependency
        return ""

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()

    # Prefer an explicit description container; fall back to the main region.
    for selector in (
        ".event-description",
        "#event-description",
        "[class*='description']",
        "[itemprop='description']",
        "main",
        "article",
    ):
        node = soup.select_one(selector)
        if node:
            text = clean_text(node.get_text(" ", strip=True), limit)
            if len(text) > 120:
                return text
    return clean_text(soup.get_text(" ", strip=True), limit)


def _merge_detail(event: Event, detail: Event) -> list[str]:
    """Copy richer values from the detail page onto the listing record."""
    improved: list[str] = []

    if len(detail.description) > len(event.description) + 40:
        event.description = detail.description
        improved.append("description")
    for field_name in ("venue", "address", "organizer", "city", "image"):
        if not getattr(event, field_name) and getattr(detail, field_name):
            setattr(event, field_name, getattr(detail, field_name))
            improved.append(field_name)
    if event.price_min is None and detail.price_min is not None:
        event.price_min = detail.price_min
        event.currency = detail.currency
        improved.append("price")
    if event.price_max is None and detail.price_max is not None:
        event.price_max = detail.price_max
    if event.is_free is None and detail.is_free is not None:
        event.is_free = detail.is_free
    if event.end_date is None and detail.end_date is not None:
        event.end_date = detail.end_date
    if detail.is_online and not event.is_online:
        event.is_online = True
        improved.append("online-flag")
    return improved


def enrich(
    events: list[Event],
    fetcher: Fetcher,
    *,
    limit: int = 40,
    min_prescore: int = 12,
    scorer: Scorer | None = None,
) -> dict[str, int]:
    """Fetch detail pages for the most promising events and merge the data in.

    Mutates ``events`` in place. Returns counters for the run summary.
    """
    scorer = scorer or Scorer()
    stats = {"considered": 0, "fetched": 0, "improved": 0, "failed": 0}

    ranked: list[tuple[int, Event]] = []
    for event in events:
        # Curated records already carry hand-written audience notes, and their
        # URLs point at organiser homepages whose markup describes the org.
        if "curated" in event.tags or not event.url.startswith("http"):
            continue
        prescore = scorer.score(event)
        if prescore.disqualified_by:
            continue
        # Rank by prospect rather than lead score: access signals live on the
        # detail page, so judging access before fetching it is circular.
        ranked.append((prescore.prospect, event))

    ranked.sort(key=lambda pair: -pair[0])
    shortlist = [e for score, e in ranked if score >= min_prescore][:limit]
    stats["considered"] = len(ranked)

    log.info(
        "enriching %d of %d candidates (prescore >= %d)",
        len(shortlist),
        len(ranked),
        min_prescore,
    )

    for event in shortlist:
        try:
            html = fetcher.get(event.url)
        except FetchError as exc:
            log.debug("enrich failed %s: %s", event.url, exc)
            stats["failed"] += 1
            continue
        stats["fetched"] += 1
        improved: list[str] = []

        detail = _pick_match(events_from_html(html, event.source, default_city=event.city), event)
        if detail is not None:
            improved += _merge_detail(event, detail)

        if event.source in SERVER_RENDERED:
            body = extract_body_text(html)
            if len(body) > len(event.description):
                event.description = body
                improved.append("body-text")

        if improved:
            stats["improved"] += 1
            event.tags.append("enriched")

    log.info(
        "enrichment: fetched %d, improved %d, failed %d",
        stats["fetched"],
        stats["improved"],
        stats["failed"],
    )
    return stats
