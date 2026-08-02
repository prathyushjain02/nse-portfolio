"""Tests for JSON-LD extraction, dedupe, filtering and persistence."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from hni_event_radar.dedupe import deduplicate
from hni_event_radar.models import Event, parse_date, clean_text
from hni_event_radar.pipeline import _in_horizon, _matches_city
from hni_event_radar.sources.jsonld import events_from_html
from hni_event_radar.store import Store

LISTING_HTML = """
<html><head>
<script type="application/ld+json">
[{"@context":"https://schema.org","@type":"Event",
  "name":"Family Office Summit 2026",
  "url":"https://example.com/e/fos-2026",
  "description":"For <b>family office</b> principals &amp; promoters.",
  "startDate":"2026-09-15","endDate":"2026-09-16",
  "location":{"@type":"Place","name":"The Leela Palace",
    "address":{"@type":"PostalAddress","streetAddress":"23 Old Airport Rd",
      "addressLocality":"Bengaluru","addressRegion":"KA","postalCode":"560008"}},
  "organizer":{"@type":"Organization","name":"Example Media"},
  "offers":[{"@type":"Offer","price":"12500","priceCurrency":"INR"},
            {"@type":"Offer","price":"35000","priceCurrency":"INR"}],
  "image":"https://example.com/i.jpg"}]
</script>
<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[]}</script>
</head><body></body></html>
"""


class TestJsonLd:
    def test_extracts_event_fields(self):
        events = events_from_html(LISTING_HTML, "test")
        assert len(events) == 1
        event = events[0]
        assert event.title == "Family Office Summit 2026"
        assert event.start_date == date(2026, 9, 15)
        assert event.end_date == date(2026, 9, 16)
        assert event.venue == "The Leela Palace"
        assert event.city == "Bengaluru"
        assert event.organizer == "Example Media"
        assert event.price_min == 12500
        assert event.price_max == 35000
        assert event.is_free is False

    def test_strips_markup_and_entities_from_description(self):
        event = events_from_html(LISTING_HTML, "test")[0]
        assert "<b>" not in event.description
        assert "family office principals & promoters" in event.description

    def test_ignores_non_event_nodes(self):
        assert len(events_from_html(LISTING_HTML, "test")) == 1

    def test_survives_malformed_json(self):
        broken = '<script type="application/ld+json">{"@type": "Event", oops}</script>'
        assert events_from_html(broken, "test") == []

    def test_handles_page_with_no_jsonld(self):
        assert events_from_html("<html><body>nothing</body></html>", "test") == []

    def test_detects_online_events(self):
        html = """<script type="application/ld+json">{"@type":"Event","name":"X",
        "url":"https://e.com/x","eventAttendanceMode":"https://schema.org/OnlineEventAttendanceMode",
        "location":{"@type":"VirtualLocation","url":"https://zoom.us/j/1"}}</script>"""
        assert events_from_html(html, "test")[0].is_online is True

    def test_free_event_offers(self):
        html = """<script type="application/ld+json">{"@type":"Event","name":"Free One",
        "url":"https://e.com/f","offers":{"@type":"Offer","price":"0","priceCurrency":"INR"}}
        </script>"""
        event = events_from_html(html, "test")[0]
        assert event.is_free is True
        assert event.price_label == "Free"


class TestModels:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2026-09-15", date(2026, 9, 15)),
            ("2026-09-15T18:30:00+05:30", date(2026, 9, 15)),
            ("2026-09-15T13:00:00Z", date(2026, 9, 15)),
            ("15 Sep 2026", date(2026, 9, 15)),
            ("September 15, 2026", date(2026, 9, 15)),
            ("", None),
            (None, None),
            ("not a date", None),
        ],
    )
    def test_parse_date(self, raw, expected):
        assert parse_date(raw) == expected

    def test_clean_text_collapses_whitespace(self):
        assert clean_text("  a\n\n  b  ") == "a b"

    def test_fingerprint_is_stable_across_title_noise(self):
        a = Event(source="x", title="Family Office Summit", url="", start_date=date(2026, 9, 15))
        b = Event(source="y", title="FAMILY  OFFICE  SUMMIT!", url="", start_date=date(2026, 9, 15))
        assert a.fingerprint == b.fingerprint

    def test_fingerprint_separates_different_dates(self):
        a = Event(source="x", title="Summit", url="", start_date=date(2026, 9, 15))
        b = Event(source="x", title="Summit", url="", start_date=date(2027, 9, 15))
        assert a.fingerprint != b.fingerprint

    def test_round_trip_serialisation(self):
        event = Event(
            source="x",
            title="T",
            url="u",
            start_date=date(2026, 9, 15),
            price_min=100.0,
            tags=["a"],
        )
        restored = Event.from_dict(event.to_dict())
        assert restored.start_date == event.start_date
        assert restored.title == event.title
        assert restored.tags == ["a"]

    def test_price_label_formats_range(self):
        event = Event(source="x", title="T", url="", price_min=1000, price_max=5000)
        assert event.price_label == "₹1,000–₹5,000"


class TestDedupe:
    def test_merges_same_event_from_two_portals(self):
        a = Event(
            source="eventbrite",
            title="Family Office Summit 2026",
            url="https://eb.com/1",
            start_date=date(2026, 9, 15),
        )
        b = Event(
            source="allevents",
            title="Family Office Summit",
            url="https://ae.in/1",
            start_date=date(2026, 9, 15),
            venue="The Leela",
            price_min=12500,
        )
        merged = deduplicate([a, b])
        assert len(merged) == 1
        assert merged[0].venue == "The Leela", "gaps should be filled from the duplicate"
        assert merged[0].price_min == 12500
        assert "also:allevents" in merged[0].tags

    def test_curated_record_wins_over_scraped(self):
        scraped = Event(
            source="eventbrite", title="Alpha Summit", url="x", start_date=date(2026, 8, 22)
        )
        curated = Event(
            source="curated",
            title="Alpha Summit",
            url="y",
            start_date=date(2026, 8, 22),
            notes="hand-checked",
            tags=["curated"],
        )
        merged = deduplicate([scraped, curated])
        assert len(merged) == 1
        assert merged[0].source == "curated"
        assert merged[0].notes == "hand-checked"

    def test_keeps_distinct_events_apart(self):
        a = Event(source="s", title="Startup Pitches", url="a", start_date=date(2026, 9, 1))
        b = Event(source="s", title="Family Office Summit", url="b", start_date=date(2026, 9, 1))
        assert len(deduplicate([a, b])) == 2

    def test_same_series_different_dates_stays_separate(self):
        a = Event(source="s", title="Monthly Founders Meet", url="a", start_date=date(2026, 9, 1))
        b = Event(source="s", title="Monthly Founders Meet", url="b", start_date=date(2026, 10, 1))
        assert len(deduplicate([a, b])) == 2


class TestFilters:
    @pytest.mark.parametrize(
        "city_field,venue,expected",
        [
            ("Bengaluru", "", True),
            ("", "Taj MG Road, Bangalore", True),
            ("Mumbai", "Taj Lands End", False),
            ("", "", True),
        ],
    )
    def test_city_matching(self, city_field, venue, expected):
        event = Event(source="s", title="T", url="", city=city_field, venue=venue)
        assert _matches_city(event, "Bengaluru") is expected

    def test_misspelled_bengaluru_is_accepted(self):
        event = Event(source="s", title="Meet VCs | Bengluru", url="")
        assert _matches_city(event, "Bengaluru") is True

    def test_national_curated_entries_bypass_city_filter(self):
        event = Event(source="curated", title="T", url="", city="Mumbai", tags=["national"])
        assert _matches_city(event, "Bengaluru") is True

    def test_horizon(self):
        today = date(2026, 8, 2)
        assert _in_horizon(Event(source="s", title="T", url=""), today, 90) is True
        soon = Event(source="s", title="T", url="", start_date=today + timedelta(days=30))
        assert _in_horizon(soon, today, 90) is True
        far = Event(source="s", title="T", url="", start_date=today + timedelta(days=400))
        assert _in_horizon(far, today, 90) is False

    def test_past_scraped_events_dropped_but_curated_kept(self):
        today = date(2026, 8, 2)
        past = today - timedelta(days=5)
        scraped = Event(source="s", title="T", url="", start_date=past)
        curated = Event(source="curated", title="T", url="", start_date=past, tags=["curated"])
        assert _in_horizon(scraped, today, 90) is False
        assert _in_horizon(curated, today, 90) is True


class TestStore:
    def test_new_events_reported_once(self, tmp_path):
        events = [
            Event(source="s", title="A", url="a", start_date=date(2026, 9, 1), lead_score=50),
            Event(source="s", title="B", url="b", start_date=date(2026, 9, 2), lead_score=40),
        ]
        db = tmp_path / "events.db"
        with Store(db) as store:
            assert len(store.upsert(events)) == 2
        with Store(db) as store:
            assert store.upsert(events) == [], "a second run sees nothing new"
            extra = Event(source="s", title="C", url="c", start_date=date(2026, 9, 3))
            assert len(store.upsert([extra])) == 1

    def test_events_survive_a_round_trip(self, tmp_path):
        event = Event(
            source="s",
            title="A",
            url="a",
            start_date=date(2026, 9, 1),
            lead_score=61,
            play="Sponsor",
        )
        with Store(tmp_path / "e.db") as store:
            store.upsert([event])
            loaded = store.all_events()
        assert loaded[0].title == "A"
        assert loaded[0].lead_score == 61
        assert loaded[0].play == "Sponsor"

    def test_min_score_filter(self, tmp_path):
        with Store(tmp_path / "e.db") as store:
            store.upsert(
                [
                    Event(source="s", title="hi", url="a", lead_score=80),
                    Event(source="s", title="lo", url="b", lead_score=10),
                ]
            )
            assert [e.title for e in store.all_events(min_score=50)] == ["hi"]
