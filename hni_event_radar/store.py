"""SQLite persistence.

Two jobs: remember what was already seen so each run can report only what is
new, and keep a history so a weekly cron produces a diff rather than the same
list over and over.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    fingerprint TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    url         TEXT,
    source      TEXT,
    start_date  TEXT,
    city        TEXT,
    lead_score  INTEGER,
    payload     TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_date);
CREATE INDEX IF NOT EXISTS idx_events_score ON events(lead_score);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    city        TEXT,
    found       INTEGER,
    new_count   INTEGER,
    stats       TEXT
);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- events --------------------------------------------------------------

    def known_fingerprints(self) -> set[str]:
        rows = self.conn.execute("SELECT fingerprint FROM events").fetchall()
        return {row["fingerprint"] for row in rows}

    def upsert(self, events: list[Event]) -> list[Event]:
        """Persist events; return the subset never seen before."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        known = self.known_fingerprints()
        fresh: list[Event] = []

        for event in events:
            is_new = event.fingerprint not in known
            if is_new:
                event.first_seen = now
                fresh.append(event)
            else:
                row = self.conn.execute(
                    "SELECT first_seen FROM events WHERE fingerprint = ?",
                    (event.fingerprint,),
                ).fetchone()
                event.first_seen = row["first_seen"] if row else now

            self.conn.execute(
                """
                INSERT INTO events
                    (fingerprint, title, url, source, start_date, city,
                     lead_score, payload, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    title      = excluded.title,
                    url        = excluded.url,
                    lead_score = excluded.lead_score,
                    payload    = excluded.payload,
                    last_seen  = excluded.last_seen
                """,
                (
                    event.fingerprint,
                    event.title,
                    event.url,
                    event.source,
                    event.start_date.isoformat() if event.start_date else None,
                    event.city,
                    event.lead_score,
                    json.dumps(event.to_dict()),
                    event.first_seen,
                    now,
                ),
            )
        self.conn.commit()
        return fresh

    def all_events(self, *, min_score: int = 0) -> list[Event]:
        rows = self.conn.execute(
            "SELECT payload FROM events WHERE lead_score >= ? ORDER BY lead_score DESC",
            (min_score,),
        ).fetchall()
        return [Event.from_dict(json.loads(row["payload"])) for row in rows]

    # -- runs ----------------------------------------------------------------

    def record_run(self, city: str, found: int, new_count: int, stats: dict) -> None:
        self.conn.execute(
            "INSERT INTO runs (started_at, city, found, new_count, stats) VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                city,
                found,
                new_count,
                json.dumps(stats),
            ),
        )
        self.conn.commit()

    def run_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
