"""Source registry."""

from __future__ import annotations

from .base import Source
from .curated import CuratedSource
from .portals import (
    AllEventsSource,
    EventbriteSource,
    MeetupSource,
    TenTimesSource,
)

REGISTRY: dict[str, type[Source]] = {
    cls.name: cls
    for cls in (
        AllEventsSource,
        EventbriteSource,
        MeetupSource,
        TenTimesSource,
        CuratedSource,
    )
}


def build_sources(names, fetcher, settings: dict | None = None) -> list[Source]:
    """Instantiate the requested sources, skipping unknown names."""
    settings = settings or {}
    built = []
    for name in names:
        cls = REGISTRY.get(name)
        if cls is None:
            continue
        built.append(cls(fetcher, settings.get(name, {})))
    return built


__all__ = ["REGISTRY", "Source", "build_sources"]
