"""
L1 Ingestion: rate-limited, cached NSE client.

Every response is cached to disk keyed by URL so re-runs are free and the
backtest is reproducible from a frozen snapshot.

Two traps discovered the hard way and handled here:
  1. The bulk/block deal JSON endpoint silently truncates to 70 rows for ANY
     date range. `&csv=true` returns the full set. Using the JSON endpoint
     would have silently deleted ~95% of the label set.
  2. The per-symbol shareholding endpoint only retains ~4.5 years of history,
     and the PIT Reg 7(2) endpoint returns only the last ~20 disclosures.
     Both bound the usable panel window - see reports/.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
from datetime import datetime, date
from typing import Any

import pandas as pd
import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

BASE = "https://www.nseindia.com"
ARCHIVES = "https://nsearchives.nseindia.com"

WARMUP = [
    "/companies-listing/corporate-filings-insider-trading",
    "/companies-listing/corporate-filings-announcements",
    "/companies-listing/corporate-shareholdings-master",
    "/companies-listing/corporate-filings-bulk-deals",
    "/market-data/all-upcoming-issues-ipo",
]


class NSEClient:
    """Thread-safe-ish: each thread gets its own session via thread-local."""

    def __init__(self, cache_dir: str, min_interval: float = 0.35, verbose: bool = True):
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.verbose = verbose
        self._local = threading.local()
        self._lock = threading.Lock()
        os.makedirs(cache_dir, exist_ok=True)

    # ---------------- plumbing ----------------

    @property
    def s(self) -> requests.Session:
        if not hasattr(self._local, "s"):
            s = requests.Session()
            s.headers.update(
                {
                    "User-Agent": UA,
                    "Accept": "*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Connection": "keep-alive",
                }
            )
            self._local.s = s
            self._local.last = 0.0
            self._warm()
        return self._local.s

    def _warm(self):
        for p in WARMUP:
            try:
                self._local.s.get(BASE + p, timeout=30)
            except Exception:
                pass

    def _throttle(self):
        dt = time.time() - getattr(self._local, "last", 0.0)
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._local.last = time.time()

    def _cache_path(self, url: str, tag: str = "") -> str:
        h = hashlib.sha256((tag + url).encode()).hexdigest()[:24]
        return os.path.join(self.cache_dir, f"{h}.json")

    def _fetch(self, url: str, referer: str | None, retries: int, want: str):
        """want in {json, text}"""
        cp = self._cache_path(url, want)
        if os.path.exists(cp):
            try:
                with open(cp) as f:
                    return json.load(f)["payload"]
            except Exception:
                pass
        headers = {"Referer": referer or (BASE + WARMUP[1])}
        last_err = None
        for attempt in range(retries):
            self._throttle()
            try:
                r = self.s.get(url, headers=headers, timeout=120)
                if r.status_code in (401, 403):
                    self._warm()
                    raise requests.HTTPError(f"auth {r.status_code}")
                r.raise_for_status()
                payload = r.json() if want == "json" else r.text
                tmp = cp + f".{threading.get_ident()}.tmp"
                with open(tmp, "w") as f:
                    json.dump(
                        {"url": url, "fetched_at": datetime.utcnow().isoformat(), "payload": payload},
                        f,
                    )
                os.replace(tmp, cp)
                return payload
            except Exception as e:
                last_err = e
                time.sleep(min(2 ** attempt, 8))
                if attempt >= 1:
                    self._warm()
        if self.verbose:
            print(f"    ! give up {url[-90:]}: {last_err}", flush=True)
        return None

    def get_json(self, path: str, referer: str | None = None, retries: int = 4):
        return self._fetch(BASE + path, referer, retries, "json")

    def get_text(self, url: str, referer: str | None = None, retries: int = 3):
        return self._fetch(url, referer, retries, "text")

    # ---------------- endpoint wrappers ----------------

    @staticmethod
    def _d(d: date) -> str:
        return d.strftime("%d-%m-%Y")

    def equity_list(self) -> pd.DataFrame:
        """Full NSE equity master: symbol, name, series, listing date, ISIN."""
        t = self.get_text(ARCHIVES + "/content/equities/EQUITY_L.csv", referer=BASE + "/")
        if not t:
            return pd.DataFrame()
        df = pd.read_csv(io.StringIO(t))
        df.columns = [c.strip() for c in df.columns]
        return df

    def deals_csv(self, kind: str, frm: date, to: date) -> pd.DataFrame:
        """kind in {bulk_deals, block_deals}. MUST use csv=true (JSON caps at 70)."""
        url = (
            f"{BASE}/api/historicalOR/bulk-block-short-deals?optionType={kind}"
            f"&from={self._d(frm)}&to={self._d(to)}&csv=true"
        )
        t = self.get_text(url, referer=BASE + "/companies-listing/corporate-filings-bulk-deals")
        if not t or "," not in t:
            return pd.DataFrame()
        try:
            df = pd.read_csv(io.StringIO(t))
        except Exception:
            return pd.DataFrame()
        df.columns = [c.strip().strip('"') for c in df.columns]
        return df

    def shareholding_symbol(self, symbol: str):
        """Full retained shareholding-pattern history for one symbol.
        Carries quarter-end `date`, `submissionDate`, `broadcastDate`,
        revision flags and the XBRL URL."""
        p = f"/api/corporate-share-holdings-master?index=equities&symbol={requests.utils.quote(symbol)}"
        r = self.get_json(p, referer=BASE + "/companies-listing/corporate-shareholdings-master")
        return r if isinstance(r, list) else []

    def shareholding_window(self, frm: date, to: date):
        p = (
            f"/api/corporate-share-holdings-master?index=equities"
            f"&from_date={self._d(frm)}&to_date={self._d(to)}"
        )
        r = self.get_json(p, referer=BASE + "/companies-listing/corporate-shareholdings-master")
        return r if isinstance(r, list) else []

    def shp_xbrl_names(self, url: str) -> list[dict]:
        """Extract shareholder names + context category from an SHP XBRL.

        Promoter-group members appear under the promoter sub-category contexts;
        public shareholders under institutional ones. This is the person-level
        entity graph the framework demands (section 1.2).
        """
        import re

        t = self.get_text(url, referer=BASE + "/")
        if not t:
            return []
        pat = re.compile(
            r'<in-bse-shp:NameOfTheShareholder[^>]*contextRef="([^"]+)"[^>]*>([^<]+)<'
        )
        out = []
        for ctx, name in pat.findall(t):
            out.append({"context": re.sub(r"\d+$", "", ctx), "name": name.strip()})
        return out

    def sast_reg29(self, frm: date, to: date):
        p = (
            f"/api/corporate-sast-reg29?index=equities"
            f"&from_date={self._d(frm)}&to_date={self._d(to)}"
        )
        r = self.get_json(p, referer=BASE + "/companies-listing/corporate-filings-insider-trading")
        return (r or {}).get("data", []) if isinstance(r, dict) else []

    def pledge(self, frm: date, to: date):
        p = (
            f"/api/corporate-pledgedata?index=equities"
            f"&from_date={self._d(frm)}&to_date={self._d(to)}"
        )
        r = self.get_json(p, referer=BASE + "/companies-listing/corporate-filings-pledged-data")
        return (r or {}).get("data", []) if isinstance(r, dict) else []

    def announcements(self, frm: date, to: date, subject: str | None = None):
        p = (
            f"/api/corporate-announcements?index=equities"
            f"&from_date={self._d(frm)}&to_date={self._d(to)}"
        )
        if subject:
            p += "&subject=" + requests.utils.quote(subject)
        r = self.get_json(p, referer=BASE + "/companies-listing/corporate-filings-announcements")
        return r if isinstance(r, list) else []

    def past_ipos(self):
        r = self.get_json("/api/public-past-issues", referer=BASE + "/market-data/all-upcoming-issues-ipo")
        return r if isinstance(r, list) else []

    def insider_pit(self, symbol: str):
        """PIT Reg 7(2) continual disclosures. Per-symbol only, recent window."""
        p = f"/api/corporates-pit?index=equities&symbol={requests.utils.quote(symbol)}"
        r = self.get_json(p, referer=BASE + "/companies-listing/corporate-filings-insider-trading")
        return (r or {}).get("data", []) if isinstance(r, dict) else []
