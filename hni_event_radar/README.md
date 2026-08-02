# HNI Event Radar

A bot that scrapes the web for events, conferences and expos in Bengaluru where
a private wealth manager can meet HNI / UHNI prospects — scores each one on how
likely the room is to contain clients, whether you can buy your way in, and how
many rival advisers will be working the same floor.

It answers three questions per event:

| Score | Question |
|---|---|
| **Prospect density** | Does this room actually contain people who could become clients? |
| **Access** | Can I get in — delegate pass, stall, sponsorship, membership? |
| **Peer competition** | How many other wealth managers will be there? |

These stay separate on purpose. A wealth-management summit scores high on peer
and low on prospect: still worth attending, but for referral partnerships and
product intel, not for AUM. Collapsing that into one number hides the decision.

Every event also gets a **play** — the action to take, not just a rank:
*buy the delegate pass*, *sponsor for a speaking slot*, *exhibit, don't attend*,
*join as a member*, *find a host*, *skip*.

## Install

```bash
pip install -r requirements.txt
```

## Use

```bash
python -m hni_event_radar scrape                  # full run, writes to out/
python -m hni_event_radar scrape --min-score 40   # tighter shortlist
python -m hni_event_radar scrape --sources curated allevents
python -m hni_event_radar scrape --city Mumbai --horizon 120
python -m hni_event_radar report                  # re-render from the DB, no network
python -m hni_event_radar sources                 # list adapters
python -m hni_event_radar explain "Family Office Summit" \
    --description "Delegate pass. Promoters and principals." \
    --venue "The Leela Palace" --price 25000
```

`scrape` writes four files to `out/`:

- `report.html` — the briefing: ranked, tiered, with score bars and reasoning
- `digest.md` — same content as markdown, for pasting into email or Slack
- `events.csv` — for a spreadsheet or CRM import
- `events.json` — full records including every scoring signal
- `events.db` — SQLite; tracks what was already seen so each run reports what's **new**

## Sources

| Source | What it covers |
|---|---|
| `curated` | Hand-maintained circuit: Equalifi, CFA Society, VCCircle, PMS AIF World, TiE, plot expos |
| `allevents` | allevents.in — broadest Indian listing site, strong on expos |
| `eventbrite` | Eventbrite — best signal on paid delegate passes and ticket tiers |
| `meetup` | Meetup — small-format founder/investor circles |
| `10times` | 10times.com — the best B2B conference index in India, but bot-protected |

**On 10times:** it returns HTTP 403 to automated clients. The adapter stays in
the rotation and degrades gracefully — when it does answer, the data quality is
the highest of any free source. Check
`https://10times.com/bangalore/finance-banking` by hand.

**On Eventbrite:** its "About this event" body is rendered client-side, so only
the one-line summary is machine-readable. Prices, venue and dates come through
fine. Events sourced there score on thinner evidence than allevents or Meetup
listings — that is a data limit, not a bug.

### Why the curated list exists

The events that matter most to a wealth manager are run by organisers on their
own websites and never touch the consumer ticketing portals. A pure scraper
misses the entire top of the market, so `data/curated_bengaluru.yaml` tracks the
recurring circuit by hand and merges it into every run. Entries marked
`confidence: verified` were checked against the organiser's page; entries marked
`confidence: recurring` are real annual series whose next date is an
expectation, not a confirmed booking. **Confirm dates before booking travel.**

## Tuning the model

All signals live in `data/taxonomy.yaml` — keyword groups, weights, hard
disqualifiers, venue prestige tiers and delegate-price bands. Retune it without
touching code, then check the effect with `explain`.

Scoring mechanics worth knowing:

- **Saturating curves.** Evidence maps onto 0–100 with diminishing returns, so a
  keyword-stuffed listing cannot outrank a genuinely senior event.
- **Penalties multiply, they don't subtract.** A flat subtraction let one soft
  negative ("meetup") cancel a real positive ("investor networking") and drive
  the score to zero.
- **Access multiplies prospect, it isn't added to it.** Otherwise a free, easy
  to attend event with nobody worth meeting outranks a mid-tier conference.
- **Peer density discounts, never vetoes.** A room of competitors keeps its
  intel value.
- **Direct targeting softens the peer discount.** A listing that names HNIs and
  NRIs as its audience is a prospect room even though advisers also attend.

Tier cut-offs (A ≥ 68, B ≥ 48, C ≥ 30) are calibrated against the observed
Bengaluru distribution: the strongest genuinely-purchasable event of a season
lands in the low-to-mid 70s.

## Being a good citizen

The fetcher respects `robots.txt`, rate-limits per host (2s default), caches to
disk for 6h, retries with backoff, and identifies itself. **Set
`contact_email` in `config.yaml`** — anonymous scrapers get blocked first.

Detail-page enrichment is capped (40 pages/run by default) and only spends
requests on candidates that already show a prospect signal.

## Tests

```bash
python -m pytest tests/ -q
```

The suite pins the behaviours that were actually wrong during development —
including the bug where the search term used to find an event was fed back into
its own score, making every result look like a match for every query.

## Limits

- Keyword scoring cannot read an agenda or a speaker list. It estimates from the
  words a listing uses; a discreet, well-written invite-only event will score
  lower than a loud one.
- Attendance quality is unobservable. "Prospect density" is an informed guess
  from audience language, venue and price — not a delegate list.
- Coverage is only as good as the portals. Genuinely exclusive events are not
  listed anywhere public, which is what the curated file is for.
- Treat the output as a shortlist to qualify, not a decision.
