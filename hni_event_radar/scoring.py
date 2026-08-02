"""HNI relevance scoring.

The model answers three separate questions about every event, because a
wealth manager needs all three and they do not collapse into one number:

    prospect_score  Does this room contain people who could become clients?
    access_score    Can I actually get in and work it - pass, stall, sponsorship?
    peer_density    How many rival advisers will be in the same room?

``lead_score`` combines them, but the components are kept on the record so the
report can explain itself and the user can re-rank on their own judgement.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .models import Event

TAXONOMY_PATH = Path(__file__).resolve().parent / "data" / "taxonomy.yaml"

# Thresholds are calibrated against the observed Bengaluru distribution: the
# strongest genuinely-purchasable event of a season lands in the low 70s, so a
# 75 cut-off would leave tier A permanently empty.
TIER_LABELS = (
    (68, "A"),
    (48, "B"),
    (30, "C"),
)

# Calibration. Each scale is the raw signal total that maps to ~63/100, chosen
# so a realistic best-case event lands in the 80s rather than pinning at 100.
PROSPECT_SCALE = 45.0
PEER_SCALE = 25.0
#: Raw access total when every access signal fires; used to normalise to 100.
#: Sum of the positive access group weights plus the public-ticketing bonus.
ACCESS_FULL = 82.0


def load_taxonomy(path: Path | str | None = None) -> dict[str, Any]:
    return yaml.safe_load(Path(path or TAXONOMY_PATH).read_text(encoding="utf-8"))


def _term_pattern(term: str) -> re.Pattern[str]:
    """Word-boundary match, tolerant of plurals.

    The boundary stops 'ria' firing inside 'material'; the optional suffix
    means 'promoter' still matches 'promoters', which listings always use.
    """
    return re.compile(rf"(?<![a-z0-9]){re.escape(term.lower())}(?:s|es)?(?![a-z0-9])")


def _saturate(raw: float, scale: float) -> int:
    """Map unbounded evidence onto 0-100 with diminishing returns.

    Keeps the score interpretable: a single strong signal already moves the
    needle, while a listing stuffed with twenty keywords cannot run away with
    the ranking. ``scale`` is the raw value that lands at roughly 63/100.
    """
    if raw <= 0:
        return 0
    return round(100 * (1 - math.exp(-raw / scale)))


class _Matcher:
    """Compiled keyword group with memoised patterns."""

    def __init__(self, terms: list[str]) -> None:
        self.patterns = [(t, _term_pattern(t)) for t in terms]

    def hits(self, text: str) -> list[str]:
        return [term for term, pattern in self.patterns if pattern.search(text)]


@dataclass
class ScoreBreakdown:
    prospect: int
    access: int
    peer: int
    lead: int
    tier: str
    play: str
    signals: list[str]
    warnings: list[str]
    disqualified_by: str | None = None


class Scorer:
    """Keyword-and-heuristic scorer. Deterministic and fully explainable."""

    def __init__(self, taxonomy: dict[str, Any] | None = None) -> None:
        tax = taxonomy or load_taxonomy()
        self.tax = tax
        self.prospect_groups = {
            name: (group["weight"], _Matcher(group["terms"]))
            for name, group in tax["prospect"].items()
        }
        self.peer_weight = tax["peer"]["weight"]
        self.peer_matcher = _Matcher(tax["peer"]["terms"])
        self.access_groups = {
            name: (group["weight"], _Matcher(group["terms"]))
            for name, group in tax["access"].items()
        }
        self.penalty_groups = {
            name: (group["weight"], _Matcher(group["terms"]))
            for name, group in tax["penalty"].items()
        }
        self.venue_groups = {
            name: (group["weight"], _Matcher(group["terms"]))
            for name, group in tax["venue_prestige"].items()
        }
        self.disqualifiers = _Matcher(tax["disqualify"])
        self.price_tiers = tax["price_tiers"]

    # -- text ----------------------------------------------------------------

    @staticmethod
    def _haystack(event: Event) -> str:
        # `query:` tags record which search term surfaced the event. They must
        # never reach the scorer: an event found by the "family office" query
        # would otherwise score as if its own listing said "family office",
        # which makes every result look like a match for every query.
        tags = [t for t in event.tags if not t.startswith("query:")]
        parts = [
            event.title,
            event.description,
            event.organizer,
            event.venue,
            event.address,
            event.notes,
            " ".join(tags),
        ]
        return " ".join(p for p in parts if p).lower()

    def _price_points(self, event: Event) -> tuple[int, str | None]:
        price = event.price_max if event.price_max is not None else event.price_min
        if price is None:
            return 0, None
        for low, high, points in self.price_tiers:
            if price >= low and (high is None or price < high):
                label = f"price ₹{price:,.0f}"
                return points, label
        return 0, None

    # -- scoring -------------------------------------------------------------

    def score(self, event: Event, *, today: date | None = None) -> ScoreBreakdown:
        text = self._haystack(event)
        signals: list[str] = []
        warnings: list[str] = list(event.warnings)

        blocked = self.disqualifiers.hits(text)
        # A curated entry is trusted over a stray keyword collision.
        if blocked and "curated" not in event.tags:
            return ScoreBreakdown(
                prospect=0,
                access=0,
                peer=0,
                lead=0,
                tier="X",
                play="Skip",
                signals=[f"disqualified: {blocked[0]}"],
                warnings=warnings,
                disqualified_by=blocked[0],
            )

        # -- prospect density
        prospect = 0.0
        hit_groups: set[str] = set()
        # True when the listing names the wealth segment itself as the
        # audience, rather than us inferring it from proxies.
        direct_targeting = False
        for name, (weight, matcher) in self.prospect_groups.items():
            hits = matcher.hits(text)
            if not hits:
                continue
            hit_groups.add(name)
            if name in ("wealth_segment", "capital_export"):
                direct_targeting = True
            # Diminishing returns within a group: the second and third keyword
            # add less than the first, so keyword-stuffing cannot game it.
            group_points = sum(weight / (1 + i * 1.5) for i in range(len(hits)))
            group_points = min(group_points, weight * 2)
            prospect += group_points
            signals.append(f"+{group_points:.0f} {name} ({', '.join(hits[:3])})")

        # Breadth bonus: an event that reads as wealth AND liquidity AND luxury
        # is a better room than one that repeats a single theme.
        if len(hit_groups) >= 2:
            breadth = 4 * (len(hit_groups) - 1)
            prospect += breadth
            signals.append(f"+{breadth} breadth ({len(hit_groups)} distinct signal groups)")

        venue_points = 0
        for name, (weight, matcher) in self.venue_groups.items():
            hits = matcher.hits(f"{event.venue} {event.address}".lower())
            if hits:
                venue_points = max(venue_points, weight)
                signals.append(f"+{weight} venue:{name} ({hits[0]})")
        prospect += venue_points

        price_points, price_label = self._price_points(event)
        if price_points:
            prospect += price_points
            signals.append(f"{price_points:+d} {price_label}")

        # Penalties scale the evidence rather than subtracting from it. A flat
        # subtraction lets one soft negative ("meetup") cancel a genuine
        # positive ("investor networking") and drive the score to zero; a
        # multiplier demotes the event without erasing what it has going for it.
        for name, (weight, matcher) in self.penalty_groups.items():
            if name == "online" and not event.is_online:
                # Only apply the online penalty on real online events, not on a
                # stray "webinar" in the description of a physical conference.
                continue
            hits = matcher.hits(text)
            if hits:
                factor = max(0.4, 1 - abs(weight) / 100)
                prospect *= factor
                signals.append(f"x{factor:.2f} {name} ({', '.join(hits[:2])})")
        if event.is_online:
            warnings.append("Online event - networking value is limited")

        prospect = _saturate(prospect, PROSPECT_SCALE)

        # -- peer density
        peer_hits = self.peer_matcher.hits(text)
        peer = _saturate(
            sum(self.peer_weight / (1 + i) for i in range(len(peer_hits))), PEER_SCALE
        )
        if peer_hits:
            signals.append(f"peer density {peer} ({', '.join(peer_hits[:3])})")

        # -- access
        access = 0
        access_flags: set[str] = set()
        for name, (weight, matcher) in self.access_groups.items():
            hits = matcher.hits(text)
            if not hits:
                continue
            access += weight
            access_flags.add(name)
            signals.append(f"{weight:+d} access:{name} ({hits[0]})")
        if event.price_min or event.price_max or event.is_free is False:
            access += 12
            access_flags.add("delegate_pass")
            signals.append("+12 access: ticketing is public")
        if event.is_free:
            access += 6
            access_flags.add("free_entry")
        access = max(0, min(100, round(access * 100 / ACCESS_FULL)))

        # -- composite
        # Peer density is a discount on prospecting value, never a veto: a room
        # of competitors is still worth intel and referral partnerships.
        peer_weight = 0.45
        if direct_targeting:
            # The listing names HNIs/NRIs as the audience. Advisers being in
            # the room too is then a co-attendee fact, not evidence that this
            # is an advisers-only conference - so discount the discount.
            peer_weight = 0.20
        peer_discount = 1 - peer_weight * (peer / 100)

        # Access multiplies the value of the room rather than adding to it.
        # Adding them independently rewards an event that is trivially easy to
        # attend but contains nobody worth meeting - a free game night scored
        # as highly as a mid-tier conference. Zero prospects is zero value,
        # however open the door is.
        lead = prospect * peer_discount * (0.55 + 0.5 * access / 100)

        days = event.days_away(today)
        if days is not None:
            if days < 0:
                # A curated entry with a past date is a recurring series whose
                # next edition is not announced yet - the series is still the
                # asset, so demote it gently instead of burying it.
                if "curated" in event.tags:
                    lead *= 0.80
                    warnings.append("Last edition has passed - confirm next edition date")
                else:
                    lead *= 0.25
                    warnings.append("Date has passed")
            elif days <= 10:
                lead *= 0.85
                warnings.append(f"Only {days} days away - sponsorship deadlines likely closed")
        lead = max(0, min(100, round(lead)))

        tier = "D"
        for threshold, label in TIER_LABELS:
            if lead >= threshold:
                tier = label
                break

        return ScoreBreakdown(
            prospect=prospect,
            access=access,
            peer=peer,
            lead=lead,
            tier=tier,
            play=self._play(
                event, prospect, access, peer, access_flags, hit_groups, today=today
            ),
            signals=signals,
            warnings=warnings,
        )

    # -- recommendation ------------------------------------------------------

    @staticmethod
    def _play(
        event: Event,
        prospect: int,
        access: int,
        peer: int,
        access_flags: set[str],
        hit_groups: set[str] | None = None,
        today: date | None = None,
    ) -> str:
        """Turn the numbers into the action a wealth manager should take."""
        hit_groups = hit_groups or set()
        text = f"{event.title} {event.description}".lower()
        is_expo = any(w in text for w in ("expo", "exhibition", "trade fair", "property show"))

        days = event.days_away(today)
        if days is not None and days < 0:
            return "Diarise the next edition - this one has run; ask the organiser for the delegate list"
        if "membership" in access_flags and prospect >= 30:
            return "Join as a member - recurring access to the same cohort beats a one-off pass"
        if peer >= 55 and prospect < 55:
            return "Peer room - go for referral partnerships and product intel, not AUM"
        if "closed" in access_flags and prospect >= 50:
            return "Invite-only - find a member to host you, or sponsor to buy your way in"
        if is_expo and "industry_owners" in hit_groups:
            # The wealth is standing behind the stalls, not walking the aisles.
            return (
                "Work the exhibitor list - the stall owners are the promoters. "
                "Get the exhibitor directory from the organiser and walk the aisles"
            )
        if is_expo:
            return "Exhibit - take a stall; a visitor badge converts poorly at expos"
        # Any industry room gets industry advice, even a middling one - "low
        # priority" is useless guidance for a hall full of factory owners.
        if "industry_owners" in hit_groups and prospect >= 30:
            return (
                "Industry room - sponsor or speak. Business owners here rarely have "
                "a private banker, and you are not competing with other advisers"
            )
        if prospect >= 60 and "commercial" in access_flags:
            return "Sponsor for a speaking slot - highest-value access at this event"
        if prospect >= 60:
            return "Buy the delegate pass and work the room"
        if prospect >= 40 and "networking" in access_flags:
            return "Attend the networking session only - skip the main agenda"
        if event.is_online:
            return "Low priority - online format, attend passively if free"
        if prospect >= 40:
            return "Worth a scan - qualify the delegate list before committing"
        return "Low priority"


def score_events(events: list[Event], scorer: Scorer | None = None, *, today: date | None = None):
    """Score in place and return (kept, disqualified)."""
    scorer = scorer or Scorer()
    kept: list[Event] = []
    dropped: list[Event] = []
    for event in events:
        result = scorer.score(event, today=today)
        event.prospect_score = result.prospect
        event.access_score = result.access
        event.peer_density = result.peer
        event.lead_score = result.lead
        event.play = result.play
        event.signals = result.signals
        event.warnings = result.warnings
        event.tags.append(f"tier:{result.tier}")
        (dropped if result.disqualified_by else kept).append(event)
    kept.sort(key=lambda e: (-e.lead_score, e.start_date or date.max))
    return kept, dropped
