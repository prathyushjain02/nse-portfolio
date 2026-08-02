"""Adapters for the public event portals that list Bengaluru business events.

All four portals publish schema.org JSON-LD on their listing pages, so the
adapters differ only in how a city + keyword becomes a URL.
"""

from __future__ import annotations

import logging
from typing import Iterable
from urllib.parse import quote_plus

from ..models import Event
from .base import Source
from .jsonld import events_from_html

log = logging.getLogger(__name__)

# Portal city slugs differ from the display name; Bengaluru is a repeat offender.
CITY_SLUGS = {
    "bengaluru": {
        "allevents": "bangalore",
        "eventbrite": "india--bangalore",
        "meetup": "in--Bangalore",
        "tenxtimes": "bangalore",
    },
    "mumbai": {
        "allevents": "mumbai",
        "eventbrite": "india--mumbai",
        "meetup": "in--Mumbai",
        "tenxtimes": "mumbai",
    },
    "delhi": {
        "allevents": "new-delhi",
        "eventbrite": "india--new-delhi",
        "meetup": "in--New-Delhi",
        "tenxtimes": "new-delhi",
    },
}


def city_slug(city: str, portal: str) -> str:
    return CITY_SLUGS.get(city.lower(), {}).get(portal, city.lower().replace(" ", "-"))


class AllEventsSource(Source):
    """allevents.in — the broadest Indian listing site, strong on expos."""

    name = "allevents"
    label = "allevents.in city + keyword listings"

    def discover(self, city: str, queries: Iterable[str]) -> list[Event]:
        slug = city_slug(city, "allevents")
        events: list[Event] = []
        for query in queries:
            url = f"https://allevents.in/{slug}/{quote_plus(query)}"
            html = self.fetch(url)
            if not html:
                continue
            batch = events_from_html(html, self.name, default_city=city)
            log.info("[%s] %-28s -> %d events", self.name, query, len(batch))
            for event in batch:
                event.tags.append(f"query:{query}")
            events.extend(batch)
        return events


class EventbriteSource(Source):
    """Eventbrite — best signal on paid delegate passes and ticket tiers."""

    name = "eventbrite"
    label = "eventbrite.com city + keyword discovery pages"

    def discover(self, city: str, queries: Iterable[str]) -> list[Event]:
        slug = city_slug(city, "eventbrite")
        events: list[Event] = []
        for query in queries:
            url = f"https://www.eventbrite.com/d/{slug}/{quote_plus(query.replace(' ', '-'))}/"
            html = self.fetch(url)
            if not html:
                continue
            batch = events_from_html(html, self.name, default_city=city)
            log.info("[%s] %-28s -> %d events", self.name, query, len(batch))
            for event in batch:
                event.tags.append(f"query:{query}")
            events.extend(batch)
        return events


class MeetupSource(Source):
    """Meetup — small-format founder/investor circles; low cost, high intimacy."""

    name = "meetup"
    label = "meetup.com event search"

    def discover(self, city: str, queries: Iterable[str]) -> list[Event]:
        slug = city_slug(city, "meetup")
        events: list[Event] = []
        for query in queries:
            url = (
                "https://www.meetup.com/find/?source=EVENTS"
                f"&location={slug}&keywords={quote_plus(query)}"
            )
            html = self.fetch(url)
            if not html:
                continue
            batch = events_from_html(html, self.name, default_city=city)
            log.info("[%s] %-28s -> %d events", self.name, query, len(batch))
            for event in batch:
                event.tags.append(f"query:{query}")
            events.extend(batch)
        return events


class TenTimesSource(Source):
    """10times.com — the best B2B conference index in India.

    It sits behind aggressive bot protection, so this adapter is expected to
    come back empty on many runs. It is kept in the rotation because when it
    does answer, the data is the highest quality of any free source.
    """

    name = "10times"
    label = "10times.com B2B conference index (bot-protected, best effort)"

    CATEGORY_PATHS = (
        "finance-banking",
        "business-services",
        "investment",
    )

    def discover(self, city: str, queries: Iterable[str]) -> list[Event]:
        slug = city_slug(city, "tenxtimes")
        events: list[Event] = []
        for path in self.CATEGORY_PATHS:
            html = self.fetch(f"https://10times.com/{slug}/{path}")
            if not html:
                continue
            batch = events_from_html(html, self.name, default_city=city)
            log.info("[%s] %-28s -> %d events", self.name, path, len(batch))
            events.extend(batch)
        if not events and self.errors:
            self.errors.append(
                "10times returned no events (usually bot protection) - "
                "browse https://10times.com/bangalore/finance-banking manually"
            )
        return events
