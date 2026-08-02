"""Curated circuit source.

The organisers that matter most to a private wealth manager - Equalifi, CFA
Society, VCCircle, PMS AIF World, TiE - publish on their own sites and never
touch the consumer ticketing portals. Those are tracked by hand in
``data/curated_bengaluru.yaml`` and merged into every run so the report is a
complete picture of the circuit rather than only whatever Eventbrite knows.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Iterable

import yaml

from ..models import Event, clean_text, parse_date
from .base import Source

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class CuratedSource(Source):
    """Loads the hand-maintained anchor list; optionally re-checks live pages."""

    name = "curated"
    label = "hand-maintained HNI circuit (organiser sites)"

    def __init__(self, fetcher, settings: dict | None = None) -> None:
        super().__init__(fetcher, settings)
        self.path = Path(self.settings.get("path") or DATA_DIR / "curated_bengaluru.yaml")
        self.include_national = bool(self.settings.get("include_national", True))

    def discover(self, city: str, queries: Iterable[str]) -> list[Event]:
        if not self.path.exists():
            self.errors.append(f"curated file missing: {self.path}")
            return []

        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        raw_events = data.get("events") or []
        events: list[Event] = []

        for row in raw_events:
            if row.get("national") and not self.include_national:
                continue

            start = parse_date(row.get("start_date"))
            notes_bits = [
                f"Audience: {clean_text(row.get('audience'), 400)}" if row.get("audience") else "",
                f"Access: {clean_text(row.get('access'), 300)}" if row.get("access") else "",
                clean_text(row.get("notes"), 400),
            ]
            notes = " | ".join(bit for bit in notes_bits if bit)

            tags = ["curated", f"role:{row.get('role', 'prospecting')}"]
            if row.get("confidence"):
                tags.append(f"confidence:{row['confidence']}")
            if row.get("national"):
                tags.append("national")
            if row.get("typical_month"):
                tags.append(f"cadence:{row['typical_month']}")

            event = Event(
                source=self.name,
                title=clean_text(row.get("title"), 300),
                url=clean_text(row.get("url"), 500),
                description=" ".join(
                    clean_text(row.get(k), 600) for k in ("audience", "access", "notes")
                ).strip(),
                start_date=start,
                end_date=parse_date(row.get("end_date")),
                venue=clean_text(row.get("venue"), 200),
                city=clean_text(row.get("city"), 80) or city,
                organizer=clean_text(row.get("organizer"), 200),
                price_min=row.get("price_min"),
                price_max=row.get("price_max"),
                tags=tags,
                notes=notes,
            )

            if start and start < date.today():
                event.warnings.append(
                    "Last known edition has passed - recurring series, confirm the next date"
                )
            elif not start:
                event.warnings.append(
                    f"No fixed date ({row.get('typical_month', 'cadence unknown')}) - confirm with organiser"
                )
            if row.get("confidence") == "recurring":
                event.warnings.append("Details not verified against a live organiser page")

            events.append(event)

        log.info("[%s] loaded %d curated circuit entries", self.name, len(events))
        return events
