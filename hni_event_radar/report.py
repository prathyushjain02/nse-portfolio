"""Output writers: JSON, CSV, a markdown digest and an HTML briefing."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from .models import Event
from .pipeline import RunResult

CSV_COLUMNS = [
    "lead_score",
    "tier",
    "title",
    "date_label",
    "days_away",
    "city",
    "venue",
    "organizer",
    "price_label",
    "prospect_score",
    "access_score",
    "peer_density",
    "play",
    "source",
    "url",
    "warnings",
]


def _tier(event: Event) -> str:
    return next((t.split(":")[1] for t in event.tags if t.startswith("tier:")), "?")


# --------------------------------------------------------------------- writers


def write_json(result: RunResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "city": result.city,
        "generated_on": result.generated_on.isoformat(),
        "counts": {
            "events": len(result.events),
            "new_since_last_run": len(result.new_events),
            "disqualified": len(result.disqualified),
            "by_tier": result.tier_counts,
            "by_source": result.source_counts,
        },
        "fetch_stats": result.fetch_stats,
        "source_errors": result.source_errors,
        "events": [e.to_dict() for e in result.events],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_csv(result: RunResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for event in result.events:
            row = event.to_dict()
            row["tier"] = _tier(event)
            row["warnings"] = "; ".join(event.warnings)
            writer.writerow(row)
    return path


def write_markdown(result: RunResult, path: Path, *, top: int = 25) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# HNI / UHNI event radar - {result.city}",
        "",
        f"Generated {result.generated_on.isoformat()} | "
        f"{len(result.events)} qualified events | "
        f"{len(result.new_events)} new since last run",
        "",
    ]

    new_fingerprints = {e.fingerprint for e in result.new_events}

    for tier in ("A", "B", "C", "D"):
        bucket = [e for e in result.events if _tier(e) == tier][:top]
        if not bucket:
            continue
        heading = {
            "A": "Tier A - book these",
            "B": "Tier B - worth the delegate pass",
            "C": "Tier C - qualify first",
            "D": "Tier D - long tail",
        }[tier]
        lines += [f"## {heading}", ""]
        for event in bucket:
            flag = " **[NEW]**" if event.fingerprint in new_fingerprints else ""
            lines.append(f"### {event.title}{flag}")
            lines.append("")
            lines.append(
                f"- **Score** {event.lead_score}/100 "
                f"(prospect {event.prospect_score}, access {event.access_score}, "
                f"peer density {event.peer_density})"
            )
            lines.append(f"- **When** {event.date_label}")
            if event.location_label:
                lines.append(f"- **Where** {event.location_label}")
            if event.organizer:
                lines.append(f"- **Organiser** {event.organizer}")
            lines.append(f"- **Pass** {event.price_label}")
            lines.append(f"- **Play** {event.play}")
            if event.notes:
                lines.append(f"- **Notes** {event.notes}")
            if event.warnings:
                lines.append(f"- **Caveats** {'; '.join(event.warnings)}")
            if event.url:
                lines.append(f"- {event.url}")
            lines.append("")

    if result.source_errors:
        lines += ["## Source issues", ""]
        for source, errors in result.source_errors.items():
            lines.append(f"- **{source}**: {errors[0]}" + (f" (+{len(errors)-1} more)" if len(errors) > 1 else ""))
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ------------------------------------------------------------------------ HTML

_TIER_COLOUR = {"A": "var(--g)", "B": "var(--b)", "C": "var(--am)", "D": "var(--t2)"}

_HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HNI Event Radar &middot; {city}</title>
<style>
:root{{--bg:#06080c;--s1:rgba(255,255,255,.022);--bd:rgba(255,255,255,.055);--t1:#c9cdd4;--t2:#6b7280;--t3:#3b4048;--w:#eef0f4;--g:#34d399;--r:#f87171;--b:#60a5fa;--p:#a78bfa;--am:#fbbf24;--m:ui-monospace,'SF Mono',Menlo,monospace;--f:system-ui,-apple-system,'Segoe UI',sans-serif}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--t1);font-family:var(--f);-webkit-font-smoothing:antialiased;line-height:1.5}}
.hdr{{padding:28px 24px 20px;border-bottom:1px solid var(--bd);background:linear-gradient(180deg,rgba(96,165,250,.03),transparent)}}
.hdr-in{{max-width:1080px;margin:0 auto}}
.badge{{font-size:10px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:var(--t2)}}
h1{{font-size:26px;font-weight:800;color:var(--w);letter-spacing:-.03em;margin:6px 0 4px}}
.sub{{font-size:12.5px;color:var(--t3)}}
.ct{{max-width:1080px;margin:0 auto;padding:22px 18px 80px}}
.mg{{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:24px}}
.mc{{background:var(--s1);border:1px solid var(--bd);border-radius:12px;padding:16px}}
.mc-l{{font-size:10px;font-weight:700;letter-spacing:.13em;color:var(--t2);text-transform:uppercase;margin-bottom:8px}}
.mc-v{{font-size:26px;font-weight:800;font-family:var(--m);line-height:1;color:var(--w)}}
.mc-s{{font-size:10.5px;color:var(--t3);margin-top:6px}}
.st{{font-size:10.5px;font-weight:700;color:var(--t3);letter-spacing:.13em;text-transform:uppercase;margin:28px 0 12px}}
.ev{{background:var(--s1);border:1px solid var(--bd);border-left-width:3px;border-radius:12px;padding:16px 18px;margin-bottom:12px}}
.ev-top{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}}
.ev-t{{font-size:15.5px;font-weight:700;color:var(--w);letter-spacing:-.01em}}
.ev-t a{{color:inherit;text-decoration:none}}
.ev-t a:hover{{color:var(--b)}}
.score{{font-family:var(--m);font-size:22px;font-weight:800;line-height:1;text-align:right}}
.score small{{display:block;font-size:9px;letter-spacing:.1em;color:var(--t3);font-weight:700;margin-top:4px}}
.meta{{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:11.5px;color:var(--t2);margin-top:10px}}
.meta b{{color:var(--t1);font-weight:600}}
.play{{margin-top:12px;font-size:12.5px;color:var(--t1);background:rgba(96,165,250,.06);border:1px solid rgba(96,165,250,.12);border-radius:8px;padding:9px 12px}}
.notes{{margin-top:9px;font-size:11.5px;color:var(--t2)}}
.warn{{margin-top:9px;font-size:11px;color:var(--am)}}
.bars{{display:flex;gap:14px;margin-top:12px;flex-wrap:wrap}}
.bar{{flex:1;min-width:120px}}
.bar-l{{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--t3);font-weight:700;display:flex;justify-content:space-between;margin-bottom:4px}}
.bar-t{{height:5px;background:rgba(255,255,255,.05);border-radius:3px;overflow:hidden}}
.bar-f{{height:100%;border-radius:3px}}
.tags{{margin-top:11px;display:flex;flex-wrap:wrap;gap:6px}}
.tag{{font-size:9.5px;font-family:var(--m);color:var(--t2);border:1px solid var(--bd);border-radius:5px;padding:2px 7px}}
.new{{color:var(--g);border-color:rgba(52,211,153,.3)}}
details{{margin-top:10px}}
summary{{font-size:11px;color:var(--t3);cursor:pointer;letter-spacing:.06em;text-transform:uppercase;font-weight:700}}
summary:hover{{color:var(--t2)}}
.sig{{font-family:var(--m);font-size:10.5px;color:var(--t2);margin-top:8px;line-height:1.7}}
.foot{{margin-top:40px;padding-top:18px;border-top:1px solid var(--bd);font-size:11px;color:var(--t3);line-height:1.7}}
</style>
</head>
<body>
<div class="hdr"><div class="hdr-in">
<div class="badge">Wealth Management &middot; Prospecting Radar</div>
<h1>HNI / UHNI Event Radar &mdash; {city}</h1>
<div class="sub">Generated {generated} &middot; scanned {sources} &middot; {queries} search terms</div>
</div></div>
<div class="ct">
"""


def _bar(label: str, value: int, colour: str) -> str:
    return (
        f'<div class="bar"><div class="bar-l"><span>{label}</span><span>{value}</span></div>'
        f'<div class="bar-t"><div class="bar-f" style="width:{value}%;background:{colour}"></div>'
        "</div></div>"
    )


def _event_html(event: Event, is_new: bool) -> str:
    tier = _tier(event)
    colour = _TIER_COLOUR.get(tier, "var(--t2)")
    esc = html.escape

    title = esc(event.title)
    if event.url:
        title = f'<a href="{esc(event.url)}" target="_blank" rel="noopener">{title}</a>'

    meta_bits = [f"<span><b>{esc(event.date_label)}</b></span>"]
    days = event.days_away()
    if days is not None and days >= 0:
        meta_bits.append(f"<span>in {days} days</span>")
    if event.location_label:
        meta_bits.append(f"<span>{esc(event.location_label)}</span>")
    if event.organizer:
        meta_bits.append(f"<span>by {esc(event.organizer)}</span>")
    meta_bits.append(f"<span>Pass: <b>{esc(event.price_label)}</b></span>")
    meta_bits.append(f"<span>via {esc(event.source)}</span>")

    tags = []
    if is_new:
        tags.append('<span class="tag new">NEW</span>')
    # `query:` tags are provenance - useful in the JSON/CSV, visual noise here.
    for tag in event.tags:
        if tag.startswith(("also:", "role:", "confidence:", "cadence:")) or tag == "enriched":
            tags.append(f'<span class="tag">{esc(tag)}</span>')

    parts = [
        f'<div class="ev" style="border-left-color:{colour}">',
        '<div class="ev-top">',
        f'<div class="ev-t">{title}</div>',
        f'<div class="score" style="color:{colour}">{event.lead_score}<small>TIER {tier}</small></div>',
        "</div>",
        f'<div class="meta">{"".join(meta_bits)}</div>',
        '<div class="bars">',
        _bar("Prospect density", event.prospect_score, "var(--g)"),
        _bar("Access", event.access_score, "var(--b)"),
        _bar("Peer competition", event.peer_density, "var(--r)"),
        "</div>",
        f'<div class="play"><b>Play:</b> {esc(event.play)}</div>',
    ]
    if event.notes:
        parts.append(f'<div class="notes">{esc(event.notes)}</div>')
    if event.warnings:
        parts.append(f'<div class="warn">&#9888; {esc("; ".join(event.warnings))}</div>')
    if tags:
        parts.append(f'<div class="tags">{"".join(tags)}</div>')
    if event.signals:
        signals = "<br>".join(esc(s) for s in event.signals)
        parts.append(f"<details><summary>Why this score</summary><div class='sig'>{signals}</div></details>")
    parts.append("</div>")
    return "".join(parts)


def write_html(result: RunResult, path: Path, *, config: dict | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    config = config or {}
    esc = html.escape

    out = [
        _HTML_HEAD.format(
            city=esc(result.city),
            generated=result.generated_on.isoformat(),
            sources=esc(", ".join(result.source_counts) or "no sources"),
            queries=len(config.get("queries", [])),
        )
    ]

    tiers = result.tier_counts
    cards = [
        ("Qualified events", len(result.events), "after filtering and dedupe"),
        ("New this run", len(result.new_events), "not seen in previous runs"),
        ("Tier A", tiers.get("A", 0), "book these"),
        ("Tier B", tiers.get("B", 0), "worth a delegate pass"),
        ("Filtered out", len(result.disqualified), "disqualified by hard filters"),
    ]
    out.append('<div class="mg">')
    for label, value, sub in cards:
        out.append(
            f'<div class="mc"><div class="mc-l">{esc(label)}</div>'
            f'<div class="mc-v">{value}</div><div class="mc-s">{esc(sub)}</div></div>'
        )
    out.append("</div>")

    new_fingerprints = {e.fingerprint for e in result.new_events}
    headings = {
        "A": "Tier A &mdash; book these",
        "B": "Tier B &mdash; worth the delegate pass",
        "C": "Tier C &mdash; qualify the delegate list first",
        "D": "Tier D &mdash; long tail",
    }
    for tier, heading in headings.items():
        bucket = [e for e in result.events if _tier(e) == tier]
        if not bucket:
            continue
        out.append(f'<div class="st">{heading} ({len(bucket)})</div>')
        out += [_event_html(e, e.fingerprint in new_fingerprints) for e in bucket]

    foot = [
        "Scores are heuristic, not gospel: <b>prospect density</b> estimates whether the room "
        "contains potential clients, <b>access</b> whether you can buy your way in, and "
        "<b>peer competition</b> how many rival advisers will be working the same floor.",
        "Curated entries marked <code>confidence:recurring</code> have not been verified against "
        "a live organiser page &mdash; confirm dates before booking.",
    ]
    if result.source_errors:
        issues = "; ".join(f"{k}: {v[0]}" for k, v in result.source_errors.items())
        foot.append(f"Source issues this run &mdash; {esc(issues)}")
    out.append(f'<div class="foot">{"<br><br>".join(foot)}</div>')
    out.append("</div></body></html>")

    path.write_text("".join(out), encoding="utf-8")
    return path


def write_all(result: RunResult, out_dir: Path, *, config: dict | None = None) -> dict[str, Path]:
    out_dir = Path(out_dir)
    return {
        "json": write_json(result, out_dir / "events.json"),
        "csv": write_csv(result, out_dir / "events.csv"),
        "markdown": write_markdown(result, out_dir / "digest.md"),
        "html": write_html(result, out_dir / "report.html", config=config),
    }
