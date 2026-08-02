"""Cross-source deduplication.

The same summit is routinely listed on Eventbrite, allevents.in and the
organiser's own site. Collapsing those into one row - while keeping the
richest field from each copy - is what makes the report readable.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import Event

# Boilerplate that inflates title similarity without carrying meaning.
_NOISE = re.compile(
    r"\b(20\d{2}|edition|the|and|in|at|of|for|india|bangalore|bengaluru|"
    r"summit|conference|event|tickets?|registration)\b"
)

# Preference order when the same event comes from several sources: the curated
# record carries hand-checked audience notes, so it always wins.
SOURCE_PRIORITY = {"curated": 0, "10times": 1, "eventbrite": 2, "allevents": 3, "meetup": 4}


def _normalise(title: str) -> str:
    text = re.sub(r"[^a-z0-9\s]", " ", title.lower())
    text = _NOISE.sub(" ", text)
    return " ".join(text.split())


def _similar(a: Event, b: Event) -> bool:
    if a.start_date and b.start_date and a.start_date != b.start_date:
        return False
    na, nb = _normalise(a.title), _normalise(b.title)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.88


def _merge(primary: Event, other: Event) -> Event:
    """Fold ``other`` into ``primary``, filling gaps rather than overwriting."""
    for field_name in (
        "description",
        "venue",
        "address",
        "organizer",
        "image",
        "city",
        "notes",
    ):
        if not getattr(primary, field_name) and getattr(other, field_name):
            setattr(primary, field_name, getattr(other, field_name))
    if primary.price_min is None and other.price_min is not None:
        primary.price_min = other.price_min
        primary.currency = other.currency
    if primary.price_max is None and other.price_max is not None:
        primary.price_max = other.price_max
    if primary.is_free is None:
        primary.is_free = other.is_free
    if primary.end_date is None:
        primary.end_date = other.end_date
    if primary.start_date is None:
        primary.start_date = other.start_date

    for tag in other.tags:
        if tag not in primary.tags:
            primary.tags.append(tag)
    marker = f"also:{other.source}"
    if marker not in primary.tags:
        primary.tags.append(marker)
    return primary


def deduplicate(events: list[Event]) -> list[Event]:
    """Collapse duplicate events, keeping the highest-priority source record."""
    ordered = sorted(events, key=lambda e: SOURCE_PRIORITY.get(e.source, 9))
    kept: list[Event] = []
    by_fingerprint: dict[str, Event] = {}

    for event in ordered:
        existing = by_fingerprint.get(event.fingerprint)
        if existing is not None:
            _merge(existing, event)
            continue

        match = next((k for k in kept if _similar(k, event)), None)
        if match is not None:
            _merge(match, event)
            continue

        by_fingerprint[event.fingerprint] = event
        kept.append(event)

    return kept
