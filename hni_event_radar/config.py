"""Configuration loading with sane defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "city": "Bengaluru",
    "horizon_days": 240,
    "min_score": 25,
    "include_online": False,
    "sources": ["curated", "allevents", "eventbrite", "meetup", "10times"],
    "queries": [
        "family office",
        "wealth management",
        "investment summit",
        "private equity",
        "venture capital",
        "angel investors",
        "startup funding",
        "founders networking",
        "entrepreneur summit",
        "luxury",
        "real estate investment",
        "property expo",
        "nri investment",
        "cxo summit",
        "business networking",
        "manufacturing expo",
        "industrial exhibition",
        "trade fair",
        "b2b conference",
        "msme summit",
        "export promotion",
        "pharma conference",
        "ai summit",
        "technology summit",
        "logistics summit",
    ],
    "enrich": {"enabled": True, "limit": 40, "min_prescore": 12},
    "http": {
        "contact_email": "",
        "delay_seconds": 2.0,
        "timeout": 30,
        "max_retries": 3,
        "cache_ttl_seconds": 6 * 3600,
        "respect_robots": True,
    },
    "source_settings": {},
    "output": {"dir": "out", "database": "out/events.db"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif value is not None:
            merged[key] = value
    return merged


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load ``config.yaml`` merged over the built-in defaults."""
    if path is None:
        candidate = Path("config.yaml")
        path = candidate if candidate.exists() else None
    if path is None:
        return dict(DEFAULTS)
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return _deep_merge(DEFAULTS, data)
