"""
L0 Entity resolution.

Section 8.6: "the same promoter appears under three name spellings across MCA,
exchange filings and press. Budget real effort here; it silently destroys
person-level features."

We resolve two things:
  1. Company identity   -> symbol (NSE symbol is a stable-enough key; ISIN used
                           to detect symbol changes and to survive renames)
  2. Person / holder    -> a normalised name key, plus a type classification
                           (individual / promoter-corp / institution / broker)

The hard part is deciding whether the SELLER in a bulk/block deal is a
promoter/insider of that company. We do that by matching the deal's client
name against the set of names disclosed as promoter/insider for that same
symbol in SAST Reg 29 and PIT Reg 7(2) filings, using normalised tokens.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Honorifics and salutations stripped before matching
TITLES = r"\b(MR|MRS|MS|SHRI|SMT|SRI|DR|PROF|M/S|MISS|LATE|SHREE)\b"

# Corporate / legal form suffixes
CORP_SUFFIX = r"\b(PRIVATE|PVT|LIMITED|LTD|LLP|LLC|INC|CORP|CORPORATION|COMPANY|CO|PLC|AG|SA|NV|BV|GMBH|PTE|HOLDINGS?|VENTURES?|ENTERPRISES?|INVESTMENTS?|TRADING)\b"

INSTITUTION_HINTS = [
    "MUTUAL FUND", "MF ", " MF", "ASSET MANAGEMENT", "AMC", "INSURANCE",
    "LIFE INSURANCE", "PENSION", "PROVIDENT", "BANK", "SBI ", "LIC ",
    "FII", "FPI", "FOREIGN PORTFOLIO", "MAURITIUS", "SINGAPORE",
    "GOVERNMENT OF", "ABU DHABI", "MONETARY AUTHORITY", "NORGES",
    "VANGUARD", "BLACKROCK", "MORGAN STANLEY", "GOLDMAN SACHS", "NOMURA",
    "CITIGROUP", "HSBC", "CREDIT SUISSE", "UBS ", "JPMORGAN", "J P MORGAN",
    "SOCIETE GENERALE", "BNP PARIBAS", "DEUTSCHE", "BARCLAYS", "MACQUARIE",
    "COPTHALL", "ALPHA ALTERNATIVES", "AIF", "ALTERNATIVE INVESTMENT",
    "VENTURE CAPITAL", "PRIVATE EQUITY", "CAPITAL PARTNERS", "GROWTH FUND",
    "INVESTMENT FUND", "OPPORTUNITIES FUND", "MASTER FUND", "TRUSTEE",
    "ETF", "ISHARES", "SICAV", "INDEX FUND", "EMERGING MARKETS", "SCHEME",
    "FUND", "INVESTMENT TRUST", "SOVEREIGN", "ENDOWMENT", "UNIVERSITY",
]

BROKER_HINTS = [
    "SECURITIES", "BROKING", "STOCK BROKING", "CROSSEAS", "GRAVITON",
    "HRTI", "TOWER RESEARCH", "QE SECURITIES", "NK SECURITIES",
    "AMBIT", "MANSUKH", "SHAREKHAN", "ANGEL ", "ZERODHA", "JAINAM",
    "PACE STOCK", "SUNIDHI", "IIFL", "MOTILAL OSWAL", "EDELWEISS",
]


def _clean(s: str) -> str:
    s = (s or "").upper()
    s = re.sub(r"[^A-Z0-9&\s]", " ", s)
    s = re.sub(TITLES, " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@lru_cache(maxsize=200_000)
def normalize_name(name: str) -> str:
    """Aggressive normalisation used as the join key for holders."""
    return _clean(name)


@lru_cache(maxsize=200_000)
def name_core(name: str) -> str:
    """Corporate suffixes removed - used for fuzzy corporate matching."""
    s = _clean(name)
    s = re.sub(CORP_SUFFIX, " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@lru_cache(maxsize=200_000)
def name_tokens(name: str) -> frozenset:
    """Token set, minus very short tokens (initials) which cause false joins."""
    return frozenset(t for t in name_core(name).split() if len(t) > 2)


@lru_cache(maxsize=200_000)
def classify_holder(name: str) -> str:
    """individual | corporate | institution | broker"""
    u = (name or "").upper()
    for h in BROKER_HINTS:
        if h in u:
            return "broker"
    for h in INSTITUTION_HINTS:
        if h in u:
            return "institution"
    if re.search(CORP_SUFFIX, _clean(u)):
        return "corporate"
    return "individual"


def is_individual(name: str) -> bool:
    return classify_holder(name) == "individual"


def name_match(a: str, b: str, min_overlap: int = 2) -> bool:
    """Conservative match between two holder names.

    Exact on normalised form, or high token overlap. Deliberately strict:
    a false positive here creates a fake promoter sale and pollutes labels.
    """
    if not a or not b:
        return False
    na, nb = normalize_name(a), normalize_name(b)
    if na == nb:
        return True
    ca, cb = name_core(a), name_core(b)
    if ca and ca == cb:
        return True
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    inter = ta & tb
    if len(inter) >= min_overlap and len(inter) >= min(len(ta), len(tb)):
        # one name's informative tokens are a subset of the other's
        return True
    # e.g. "RAKESH JHUNJHUNWALA" vs "RAKESH RADHESHYAM JHUNJHUNWALA"
    if len(inter) >= 2 and len(inter) / max(len(ta), len(tb)) >= 0.66:
        return True
    return False


class PromoterRegistry:
    """Per-symbol set of names known to be promoter / insider, with the date
    from which we knew it (so membership itself is point-in-time)."""

    def __init__(self):
        # symbol -> list of (normalised_name, known_from, category)
        self._by_symbol: dict[str, list[tuple[str, object, str]]] = {}
        self._index: dict[str, set[str]] = {}

    def add(self, symbol: str, name: str, known_from, category: str = "promoter"):
        if not symbol or not name:
            return
        n = normalize_name(name)
        if not n:
            return
        self._by_symbol.setdefault(symbol, []).append((n, known_from, category))
        self._index.setdefault(symbol, set()).add(n)

    def names_as_of(self, symbol: str, as_of) -> list[str]:
        return [n for n, kf, _ in self._by_symbol.get(symbol, []) if kf is not None and kf <= as_of]

    def is_insider(self, symbol: str, client_name: str, as_of=None) -> bool:
        """Is this deal counterparty a known promoter/insider of `symbol`?"""
        cand = self.names_as_of(symbol, as_of) if as_of is not None else list(self._index.get(symbol, ()))
        if not cand:
            return False
        n = normalize_name(client_name)
        if n in set(cand):
            return True
        return any(name_match(client_name, c) for c in cand)

    def size(self) -> int:
        return sum(len(v) for v in self._by_symbol.values())
