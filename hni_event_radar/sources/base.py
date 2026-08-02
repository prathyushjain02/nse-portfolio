"""Source adapter contract and shared plumbing."""

from __future__ import annotations

import logging
from typing import Iterable

from ..models import Event
from ..net import Fetcher, FetchError

log = logging.getLogger(__name__)


class Source:
    """A place to look for events.

    Adapters implement :meth:`discover`. Failures are always contained: one
    dead portal must never take down the whole run, so ``collect`` swallows
    per-URL errors and records them on ``self.errors``.
    """

    name = "base"
    #: Human-readable note shown in the report so a user knows what was searched.
    label = ""

    def __init__(self, fetcher: Fetcher, settings: dict | None = None) -> None:
        self.fetcher = fetcher
        self.settings = settings or {}
        self.errors: list[str] = []

    def discover(self, city: str, queries: Iterable[str]) -> list[Event]:
        raise NotImplementedError

    # -- helpers -------------------------------------------------------------

    def fetch(self, url: str) -> str | None:
        """Fetch a URL, logging and recording failure instead of raising."""
        try:
            return self.fetcher.get(url)
        except FetchError as exc:
            log.warning("[%s] %s", self.name, exc)
            self.errors.append(str(exc))
            return None

    def collect(self, city: str, queries: Iterable[str]) -> list[Event]:
        try:
            return self.discover(city, list(queries))
        except Exception as exc:  # noqa: BLE001 - a bad adapter must not kill the run
            log.exception("[%s] adapter crashed", self.name)
            self.errors.append(f"adapter crashed: {exc}")
            return []
