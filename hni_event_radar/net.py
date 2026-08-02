"""Polite HTTP layer: shared session, rate limiting, on-disk cache, robots.txt."""

from __future__ import annotations

import hashlib
import logging
import random
import time
import urllib.robotparser as robotparser
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "HNIEventRadar/1.0 (research bot for wealth-management event discovery; "
    "contact: set contact_email in config.yaml)"
)

# Sites that reject the honest bot UA outright. We fall back to a browser UA
# rather than hammering them with retries; robots.txt is still respected.
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class FetchError(RuntimeError):
    """Raised when a URL could not be retrieved after retries."""


class Fetcher:
    """HTTP client that is deliberately slow and well-behaved.

    One instance is shared by every source so the per-host rate limit is
    actually global rather than per-adapter.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        user_agent: str = DEFAULT_UA,
        delay_seconds: float = 2.0,
        timeout: int = 30,
        max_retries: int = 3,
        cache_ttl_seconds: int = 6 * 3600,
        respect_robots: bool = True,
        use_cache: bool = True,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_ttl_seconds = cache_ttl_seconds
        self.respect_robots = respect_robots
        self.use_cache = use_cache

        self._session = requests.Session()
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self.stats = {"cache_hits": 0, "fetches": 0, "errors": 0, "robots_blocked": 0}

    # -- cache ---------------------------------------------------------------

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        host = urlparse(url).netloc.replace(":", "_") or "unknown"
        return self.cache_dir / f"{host}-{digest}.html"

    def _read_cache(self, url: str) -> str | None:
        if not self.use_cache:
            return None
        path = self._cache_path(url)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl_seconds:
            return None
        self.stats["cache_hits"] += 1
        log.debug("cache hit %s", url)
        return path.read_text(encoding="utf-8", errors="ignore")

    def _write_cache(self, url: str, body: str) -> None:
        if not self.use_cache:
            return
        try:
            self._cache_path(url).write_text(body, encoding="utf-8")
        except OSError as exc:  # pragma: no cover - disk full etc.
            log.warning("could not cache %s: %s", url, exc)

    # -- robots --------------------------------------------------------------

    def _robots_allows(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = robotparser.RobotFileParser()
            parser.set_url(f"{origin}/robots.txt")
            try:
                resp = self._session.get(
                    f"{origin}/robots.txt",
                    timeout=self.timeout,
                    headers={"User-Agent": self.user_agent},
                )
                if resp.status_code == 200:
                    parser.parse(resp.text.splitlines())
                else:
                    parser = None  # no usable robots.txt -> allow
            except requests.RequestException:
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        if parser is None:
            return True
        # Check the generic agent token; most sites key rules off "*".
        return parser.can_fetch("*", url)

    # -- fetch ---------------------------------------------------------------

    def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.delay_seconds - (time.time() - last)
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.4))
        self._last_hit[host] = time.time()

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> str:
        """Return the body of ``url``, using cache and honouring robots.txt."""
        cached = self._read_cache(url)
        if cached is not None:
            return cached

        if not self._robots_allows(url):
            self.stats["robots_blocked"] += 1
            raise FetchError(f"robots.txt disallows {url}")

        host = urlparse(url).netloc
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            self._throttle(host)
            # First attempt is honest; later attempts try a browser UA because
            # several event portals blanket-403 unknown agents.
            agent = self.user_agent if attempt == 0 else BROWSER_UA
            request_headers = {
                "User-Agent": agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
            }
            if headers:
                request_headers.update(headers)
            try:
                resp = self._session.get(
                    url, headers=request_headers, timeout=self.timeout, allow_redirects=True
                )
                self.stats["fetches"] += 1
                if resp.status_code == 200:
                    self._write_cache(url, resp.text)
                    return resp.text
                if resp.status_code in (404, 410):
                    raise FetchError(f"{resp.status_code} for {url}")
                last_error = FetchError(f"HTTP {resp.status_code} for {url}")
            except requests.RequestException as exc:
                last_error = exc
            time.sleep(2 ** attempt)

        self.stats["errors"] += 1
        raise FetchError(f"failed to fetch {url}: {last_error}")
