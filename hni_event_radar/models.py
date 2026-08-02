"""Normalised event record shared by every source and every consumer."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from typing import Any

_WS = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]+>")


def clean_text(value: Any, limit: int = 2000) -> str:
    """Collapse whitespace and strip stray markup out of scraped copy."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value)
    text = _TAGS.sub(" ", str(value))
    text = (
        text.replace("&amp;", "&")
        .replace("&nbsp;", " ")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    text = _WS.sub(" ", text).strip()
    return text[:limit]


def parse_date(value: Any) -> date | None:
    """Best-effort ISO-ish date parsing; event feeds are wildly inconsistent."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # Trim timezone designators that fromisoformat on older Pythons dislikes.
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:20].strip(), fmt).date()
        except ValueError:
            continue
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


@dataclass
class Event:
    """One discovered event, before or after scoring."""

    source: str
    title: str
    url: str
    description: str = ""
    start_date: date | None = None
    end_date: date | None = None
    venue: str = ""
    address: str = ""
    city: str = ""
    is_online: bool = False
    organizer: str = ""
    price_min: float | None = None
    price_max: float | None = None
    currency: str = "INR"
    is_free: bool | None = None
    tags: list[str] = field(default_factory=list)
    image: str = ""
    # Populated by the scoring engine.
    prospect_score: int = 0
    access_score: int = 0
    peer_density: int = 0
    lead_score: int = 0
    play: str = ""
    signals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    first_seen: str = ""
    notes: str = ""

    # -- identity ------------------------------------------------------------

    @property
    def fingerprint(self) -> str:
        """Stable ID for an event across sources and across runs.

        Keyed on normalised title + start date so the same summit listed on
        both Eventbrite and allevents.in collapses to one row.
        """
        title_key = re.sub(r"[^a-z0-9]+", "", self.title.lower())[:60]
        date_key = self.start_date.isoformat() if self.start_date else "nodate"
        return hashlib.sha1(f"{title_key}|{date_key}".encode()).hexdigest()[:16]

    @property
    def price_label(self) -> str:
        if self.price_min is None and self.price_max is None:
            return "Free" if self.is_free else "Not listed"
        # A zero ceiling is free regardless of what the source's is_free flag
        # said - merging records from two portals can leave the flag stale.
        if self.is_free or (self.price_max or self.price_min or 0) == 0:
            return "Free"
        symbol = "₹" if self.currency == "INR" else f"{self.currency} "
        if self.price_max and self.price_min and self.price_max > self.price_min:
            return f"{symbol}{self.price_min:,.0f}–{symbol}{self.price_max:,.0f}"
        amount = self.price_min if self.price_min is not None else self.price_max
        return f"{symbol}{amount:,.0f}"

    @property
    def date_label(self) -> str:
        if not self.start_date:
            return "TBC"
        out = self.start_date.strftime("%d %b %Y")
        if self.end_date and self.end_date != self.start_date:
            out += f" – {self.end_date.strftime('%d %b %Y')}"
        return out

    @property
    def location_label(self) -> str:
        """Venue and city, without repeating the city when the venue names it."""
        if self.venue and self.city and self.city.lower() in self.venue.lower():
            return self.venue
        return ", ".join(part for part in (self.venue, self.city) if part)

    def days_away(self, today: date | None = None) -> int | None:
        if not self.start_date:
            return None
        today = today or datetime.now(timezone.utc).date()
        return (self.start_date - today).days

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start_date"] = self.start_date.isoformat() if self.start_date else None
        data["end_date"] = self.end_date.isoformat() if self.end_date else None
        data["fingerprint"] = self.fingerprint
        data["price_label"] = self.price_label
        data["date_label"] = self.date_label
        data["days_away"] = self.days_away()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        known = {f for f in cls.__dataclass_fields__}  # noqa: SLF001
        payload = {k: v for k, v in data.items() if k in known}
        payload["start_date"] = parse_date(payload.get("start_date"))
        payload["end_date"] = parse_date(payload.get("end_date"))
        return cls(**payload)
