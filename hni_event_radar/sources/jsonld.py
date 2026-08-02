"""Generic schema.org/Event extractor.

Most Indian event portals (allevents.in, Eventbrite, Meetup, Townscript,
10times) publish JSON-LD ``Event`` blocks for SEO. Parsing that is both far
more reliable than CSS selectors and far kinder to the site than rendering
the page, so every HTML source in this package funnels through here.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterator

from ..models import Event, clean_text, parse_date

log = logging.getLogger(__name__)

_LDJSON = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.S | re.I,
)

ONLINE_HINTS = ("virtualocation", "virtuallocation", "online", "zoom", "webinar", "livestream")


def _iter_json_blocks(html: str) -> Iterator[Any]:
    for raw in _LDJSON.findall(html):
        text = raw.strip()
        if not text:
            continue
        try:
            yield json.loads(text)
        except json.JSONDecodeError:
            # Some sites emit trailing commas or concatenated objects.
            repaired = re.sub(r",\s*([}\]])", r"\1", text)
            try:
                yield json.loads(repaired)
            except json.JSONDecodeError:
                log.debug("unparseable ld+json block (%d chars)", len(text))


def _walk(node: Any) -> Iterator[dict]:
    """Yield every dict in a nested JSON-LD structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_event(node: dict) -> bool:
    node_type = node.get("@type") or node.get("type")
    if isinstance(node_type, list):
        return any("event" in str(t).lower() for t in node_type)
    return bool(node_type) and "event" in str(node_type).lower()


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _extract_location(node: dict) -> tuple[str, str, str, bool]:
    """Return (venue, address, city, is_online)."""
    loc = _first(node.get("location")) or {}
    if isinstance(loc, str):
        return clean_text(loc, 200), clean_text(loc, 300), "", False

    loc_type = str(loc.get("@type", "")).lower()
    is_online = "virtual" in loc_type

    venue = clean_text(loc.get("name"), 200)
    address = loc.get("address")
    city = ""
    if isinstance(address, dict):
        city = clean_text(address.get("addressLocality"), 80)
        parts = [
            address.get("streetAddress"),
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("postalCode"),
        ]
        address_text = clean_text(", ".join(str(p) for p in parts if p), 300)
    else:
        address_text = clean_text(address, 300)

    if not is_online:
        blob = f"{venue} {address_text}".lower()
        is_online = any(h in blob for h in ("online event", "virtual event", "zoom.us"))
    return venue, address_text, city, is_online


def _extract_offers(node: dict) -> tuple[float | None, float | None, str, bool | None]:
    """Return (min_price, max_price, currency, is_free)."""
    offers = node.get("offers")
    if offers is None:
        return None, None, "INR", None
    if isinstance(offers, dict):
        offers = [offers]
    prices: list[float] = []
    currency = "INR"
    saw_offer = False
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        saw_offer = True
        currency = offer.get("priceCurrency") or currency
        for key in ("price", "lowPrice", "highPrice"):
            raw = offer.get(key)
            if raw in (None, ""):
                continue
            try:
                prices.append(float(str(raw).replace(",", "").replace("₹", "").strip()))
            except ValueError:
                continue
    if not prices:
        return None, None, currency, (False if saw_offer else None)
    low, high = min(prices), max(prices)
    return low, high, currency, high == 0


def events_from_html(html: str, source: str, *, default_city: str = "") -> list[Event]:
    """Pull every schema.org Event out of a page of HTML."""
    found: list[Event] = []
    seen_urls: set[str] = set()

    for block in _iter_json_blocks(html):
        for node in _walk(block):
            if not _is_event(node):
                continue
            url = clean_text(_first(node.get("url")) or node.get("@id"), 500)
            name = clean_text(node.get("name"), 300)
            if not name:
                continue
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)

            venue, address, city, is_online = _extract_location(node)
            low, high, currency, is_free = _extract_offers(node)

            organizer = _first(node.get("organizer")) or {}
            if isinstance(organizer, dict):
                organizer = organizer.get("name", "")

            attendance = str(node.get("eventAttendanceMode", "")).lower()
            if any(h in attendance for h in ONLINE_HINTS):
                is_online = True

            found.append(
                Event(
                    source=source,
                    title=name,
                    url=url,
                    description=clean_text(node.get("description"), 1500),
                    start_date=parse_date(node.get("startDate")),
                    end_date=parse_date(node.get("endDate")),
                    venue=venue,
                    address=address,
                    city=city or default_city,
                    is_online=is_online,
                    organizer=clean_text(organizer, 200),
                    price_min=low,
                    price_max=high,
                    currency=currency,
                    is_free=is_free,
                    image=clean_text(_first(node.get("image")), 500),
                )
            )
    return found
