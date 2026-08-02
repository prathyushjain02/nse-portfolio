# nse-portfolio

Two independent tools live in this repository.

## NSE Portfolio Dashboard — `index.html`

A single-file browser dashboard for analysing an NSE portfolio from an uploaded
spreadsheet. Open the file directly; no build step.

## HNI Event Radar — `hni_event_radar/`

A bot that scrapes the web for events, conferences and expos in Bengaluru where
a private wealth manager can meet HNI / UHNI prospects, scores each on prospect
density, access and peer competition, and recommends what to do about it.

```bash
pip install -r requirements.txt
python -m hni_event_radar scrape
```

Outputs a ranked HTML briefing, a markdown digest, CSV and JSON to `out/`.
See [`hni_event_radar/README.md`](hni_event_radar/README.md) for the scoring
model, source coverage and tuning.
