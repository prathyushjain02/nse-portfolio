"""Tests for the relevance model.

These pin the behaviours that were actually wrong during development, so a
future taxonomy edit cannot silently reintroduce them.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from hni_event_radar.models import Event
from hni_event_radar.scoring import Scorer, score_events

TODAY = date(2026, 8, 2)
SOON = TODAY + timedelta(days=60)


@pytest.fixture(scope="module")
def scorer() -> Scorer:
    return Scorer()


def make(**kwargs) -> Event:
    base = dict(source="test", title="Untitled", url="https://example.com/e", start_date=SOON)
    base.update(kwargs)
    return Event(**base)


# ---------------------------------------------------------------- archetypes


def test_flagship_hni_summit_is_tier_a(scorer):
    event = make(
        title="Family Office & UHNI Wealth Summit 2026",
        description=(
            "A closed-door gathering of family office principals and promoters. "
            "Delegate pass includes networking dinner. Sponsorship available."
        ),
        venue="The Leela Palace, Bengaluru",
        price_min=25000,
        price_max=45000,
    )
    result = scorer.score(event, today=TODAY)
    assert result.tier == "A"
    assert result.prospect >= 70
    assert result.access >= 70


def test_retail_trading_seminar_is_disqualified(scorer):
    event = make(
        title="Free Webinar: Intraday Trading for Beginners",
        description="Learn to trade with guaranteed returns. Free seminar for students.",
    )
    result = scorer.score(event, today=TODAY)
    assert result.disqualified_by is not None
    assert result.lead == 0
    assert result.play == "Skip"


def test_peer_room_is_repriced_not_discarded(scorer):
    """A wealth-management summit is competitors, but still worth attending."""
    event = make(
        title="Indian Wealth Management Summit",
        description=(
            "For private bankers, relationship managers, mutual fund distributors "
            "and financial advisors. Delegate pass required."
        ),
        venue="Taj Lands End, Mumbai",
        price_min=15000,
    )
    result = scorer.score(event, today=TODAY)
    assert result.peer >= 55
    assert result.lead > 0, "a peer room still has referral value"
    assert "referral" in result.play.lower()


def test_hni_targeted_event_is_not_punished_for_admitting_advisers(scorer):
    """Regression: 'HNIs, NRIs and advisors welcome' is a prospect room."""
    targeted = make(
        title="Alpha Investments Summit",
        description="For HNIs, NRIs, financial advisors and distributors. PMS and AIF strategies.",
        venue="Taj MG Road, Bengaluru",
    )
    advisers_only = make(
        title="Distributor Meet",
        description="For financial advisors, mutual fund distributors and relationship managers.",
        venue="Taj MG Road, Bengaluru",
    )
    assert scorer.score(targeted, today=TODAY).lead > scorer.score(advisers_only, today=TODAY).lead


# ------------------------------------------------------------------ mechanics


def test_query_tags_are_excluded_from_scoring(scorer):
    """Regression: the search term that found an event must not score it.

    Otherwise every result of the 'family office' query looks like a family
    office event, which made a deer-feeding walk rank as an HNI room.
    """
    plain = make(title="Little Peak Seekers hike", description="A morning walk.")
    tagged = make(title="Little Peak Seekers hike", description="A morning walk.")
    tagged.tags = ["query:family office", "query:cxo summit"]
    assert scorer.score(plain, today=TODAY).prospect == scorer.score(tagged, today=TODAY).prospect


def test_plural_terms_match(scorer):
    singular = make(title="Meet the promoter", description="")
    plural = make(title="Meet the promoters", description="")
    assert scorer.score(plural, today=TODAY).prospect == scorer.score(singular, today=TODAY).prospect
    assert scorer.score(plural, today=TODAY).prospect > 0


def test_word_boundaries_prevent_substring_matches(scorer):
    """'ria' (registered investment adviser) must not fire inside 'material'."""
    event = make(title="Material Science Expo", description="Industrial materials.")
    assert scorer.score(event, today=TODAY).peer == 0


def test_access_cannot_carry_a_zero_prospect_event(scorer):
    """Regression: an easy-to-attend event with nobody worth meeting scores 0."""
    event = make(
        title="Thursday Game Night",
        description="Board games. Tickets available, networking, register now.",
    )
    result = scorer.score(event, today=TODAY)
    assert result.prospect == 0
    assert result.lead == 0


def test_penalties_demote_without_erasing(scorer):
    """A soft negative must not cancel a genuine positive outright."""
    event = make(
        title="Founders Meetup: Investor Networking",
        description="Meet investors and founders raising capital.",
    )
    result = scorer.score(event, today=TODAY)
    assert 0 < result.prospect, "the 'meetup' penalty should not zero a real signal"


def test_premium_venue_lifts_score(scorer):
    plain = make(title="Investment Summit", venue="Community Hall")
    posh = make(title="Investment Summit", venue="The Leela Palace, Bengaluru")
    assert scorer.score(posh, today=TODAY).prospect > scorer.score(plain, today=TODAY).prospect


def test_higher_delegate_price_signals_seniority(scorer):
    cheap = make(title="Investment Summit", price_min=200, price_max=200)
    dear = make(title="Investment Summit", price_min=30000, price_max=30000)
    assert scorer.score(dear, today=TODAY).prospect > scorer.score(cheap, today=TODAY).prospect


def test_online_events_are_penalised_and_flagged(scorer):
    physical = make(title="Family Office Forum", description="Networking with promoters.")
    online = make(
        title="Family Office Forum",
        description="Networking with promoters. Online event via zoom.",
        is_online=True,
    )
    result = scorer.score(online, today=TODAY)
    assert result.prospect < scorer.score(physical, today=TODAY).prospect
    assert any("online" in w.lower() for w in result.warnings)


# -------------------------------------------------------------------- dating


def test_past_scraped_event_is_buried(scorer):
    event = make(
        title="Family Office Summit",
        description="Family office principals and promoters. Delegate pass.",
        start_date=TODAY - timedelta(days=3),
    )
    result = scorer.score(event, today=TODAY)
    assert any("passed" in w.lower() for w in result.warnings)
    assert "next edition" in result.play.lower()


def test_past_curated_series_is_only_gently_demoted(scorer):
    """A recurring series is still an asset after its last edition."""
    scraped = make(
        title="Family Office Summit",
        description="Family office principals. Delegate pass.",
        start_date=TODAY - timedelta(days=3),
    )
    curated = make(
        title="Family Office Summit",
        description="Family office principals. Delegate pass.",
        start_date=TODAY - timedelta(days=3),
        tags=["curated"],
    )
    assert scorer.score(curated, today=TODAY).lead > scorer.score(scraped, today=TODAY).lead * 2


def test_imminent_event_is_flagged(scorer):
    event = make(
        title="Family Office Summit",
        description="Delegate pass. Networking.",
        start_date=TODAY + timedelta(days=5),
    )
    assert any("days away" in w for w in scorer.score(event, today=TODAY).warnings)


# --------------------------------------------------------------------- plays


def test_membership_play_beats_single_pass_advice(scorer):
    event = make(
        title="TiE Bangalore",
        description="Charter Membership gives access to successful entrepreneurs and promoters.",
    )
    assert "member" in scorer.score(event, today=TODAY).play.lower()


def test_industry_expo_advice_targets_exhibitors_not_visitors(scorer):
    """At a manufacturing fair the promoters run the stalls, not the aisles."""
    event = make(
        title="IMTEX - Machine Tool and Manufacturing Technology Exhibition",
        description="1,100 exhibitors. Trade fair for machine tool manufacturers. Registration.",
        venue="Bangalore International Exhibition Centre",
    )
    play = scorer.score(event, today=TODAY).play.lower()
    assert "exhibitor list" in play
    assert "take a stall" not in play


def test_property_expo_advice_is_still_to_exhibit(scorer):
    """At a property expo the buyers are the visitors, so a stall converts."""
    event = make(
        title="Bengaluru Plot Expo",
        description="Premium plotted development for land investment buyers. Tickets.",
    )
    assert "stall" in scorer.score(event, today=TODAY).play.lower()


def test_industry_room_beats_finance_industry_room(scorer):
    """A manufacturers' conclave should outrank a CFA Society conference.

    Both are 'industry' events, but one is full of prospects and the other is
    full of competitors.
    """
    manufacturers = make(
        title="MSME Manufacturing Conclave",
        description=(
            "Entrepreneurs, exporters and captains of industry. Manufacturing "
            "business owners. Delegate registration and sponsorship available."
        ),
        venue="Palace Grounds, Bengaluru",
    )
    finance_body = make(
        title="CFA Society India Private Markets Conference",
        description=(
            "For investment professionals, financial advisors and CFA charter "
            "holders. Delegate registration."
        ),
        venue="Taj MG Road, Bengaluru",
    )
    assert scorer.score(manufacturers, today=TODAY).lead > scorer.score(finance_body, today=TODAY).lead


def test_cfa_society_is_detected_as_a_peer_body(scorer):
    event = make(
        title="CFA Society India Annual Conference",
        description="For CFA charter holders and investment professionals.",
    )
    assert scorer.score(event, today=TODAY).peer > 0


def test_ai_summit_scores_as_a_prospect_room(scorer):
    event = make(
        title="Bengaluru AI Summit 2026",
        description="Deeptech founders and technology leaders. Delegate pass, networking.",
        venue="Taj MG Road",
    )
    result = scorer.score(event, today=TODAY)
    assert result.prospect >= 40
    assert result.lead >= 30


def test_expo_advice_is_to_exhibit(scorer):
    event = make(
        title="Bengaluru Plot Expo",
        description="Premium plotted development expo for land investment buyers. Tickets.",
    )
    assert "exhibit" in scorer.score(event, today=TODAY).play.lower()


# ------------------------------------------------------------------ batch API


def test_score_events_sorts_and_splits():
    good = make(
        title="UHNI Family Office Forum",
        description="Promoters, delegate pass, networking, sponsorship.",
        venue="Taj MG Road",
        price_min=20000,
    )
    junk = make(title="Kids Carnival", description="Fun for children and toddlers.")
    weak = make(title="Local Trade Meet", description="Business owners.")

    kept, dropped = score_events([weak, junk, good], today=TODAY)
    assert [e.title for e in dropped] == ["Kids Carnival"]
    assert kept[0].title == "UHNI Family Office Forum"
    assert kept[0].lead_score >= kept[-1].lead_score
    assert any(t.startswith("tier:") for t in kept[0].tags)
