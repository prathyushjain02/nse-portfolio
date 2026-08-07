"""
Event register (section 8.2) and the SEBI lock-in calendar (section 4.1 stage 3).

EVENT REGISTER
--------------
Unit of analysis is person x event (section 1.2), labelled on the CASH DATE
(section 1.1), not the announcement date.

Two independent label sources, deliberately kept separable so label quality can
be cross-checked:

  D1  Deal-tape events   - a bulk/block deal with side=SELL whose counterparty
                           resolves to a promoter/promoter-group member of that
                           symbol. Cash date = trade date. Person-level, exact
                           value. This is the primary label.

  D2  Holding-drop events - promoter+promoter-group % falls by >= threshold
                           between consecutive quarterly shareholding patterns.
                           Cash date = quarter end (the true cash date is
                           somewhere inside the quarter - a known imprecision).
                           Company-level. Used as corroboration and to measure
                           D1's recall.

Non-events are included by construction: the panel spans every listed company
in every month, whether or not anything happened (section 8.2).

LOCK-IN CALENDAR
----------------
Under SEBI ICDR 2018 (as amended 2021) every listed company has a known,
calendarable set of future dates on which specific classes of holder become
able to sell. We generate it from the IPO listing date. Because the offer
document (which discloses whether the 18m or 36m MPC applies) is not machine
readable here, we calendar the standard assumption and sensitivity-test it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from liquidity.config import EVENT_DEF, LOCKIN, LOCKIN_MPC_ASSUMPTION
from liquidity.l0_entities import PromoterRegistry, classify_holder, normalize_name


# ---------------------------------------------------------------- registry ---

def build_promoter_registry(
    promoter_names: pd.DataFrame, insider_pit: pd.DataFrame | None = None
) -> PromoterRegistry:
    """Promoter/promoter-group members per symbol, with a known_from date.

    Sourced from the earliest retained shareholding-pattern XBRL per symbol, so
    membership is knowable from that filing's broadcast date onward. Promoter
    identity is slow-moving; changes WITHIN the panel window are not tracked.
    That is a documented limitation, and it biases toward MISSING events
    (newly-added promoter-group entities look like strangers), not inventing
    them - the conservative direction for a precision-oriented model.
    """
    reg = PromoterRegistry()
    if promoter_names is not None and len(promoter_names):
        p = promoter_names[promoter_names["is_promoter_ctx"]]
        for sym, name, kf in zip(p["symbol"], p["name"], p["known_from"]):
            reg.add(sym, name, kf, "promoter_shp")

    # PIT Reg 7(2) disclosures name the insider AND state their relationship to
    # the company, with a broadcast date. That makes membership genuinely
    # point-in-time (unlike the SHP snapshot) and extends the registry to
    # directors and KMP, who never appear in the promoter table.
    if insider_pit is not None and len(insider_pit):
        q = insider_pit[insider_pit["is_insider_cat"]]
        for sym, name, kf, cat in zip(q["symbol"], q["person"], q["known_from"], q["category"]):
            reg.add(sym, name, kf, cat)
    return reg


# ------------------------------------------------------------ deal events ---

def label_deals(deals: pd.DataFrame, reg: PromoterRegistry) -> pd.DataFrame:
    """Tag every deal with counterparty type and insider status."""
    d = deals.copy()
    d["holder_type"] = [classify_holder(c) for c in d["client"]]
    d["client_norm"] = [normalize_name(c) for c in d["client"]]

    # Vectorised-ish insider match: exact normalised hit first (fast path),
    # then fuzzy only for the residual sells.
    exact = {}
    for sym, names in reg._by_symbol.items():  # noqa: SLF001 - internal by design
        exact[sym] = {n for n, _, _ in names}

    def hit(sym, cn, raw):
        s = exact.get(sym)
        if not s:
            return False
        if cn in s:
            return True
        return reg.is_insider(sym, raw)

    d["is_insider"] = [
        hit(s, cn, raw) for s, cn, raw in zip(d["symbol"], d["client_norm"], d["client"])
    ]
    return d


def deal_events(labelled_deals: pd.DataFrame) -> pd.DataFrame:
    """D1: promoter/insider SELL events, cash-dated on the trade date."""
    d = labelled_deals
    m = (
        (d["side"] == "SELL")
        & d["is_insider"]
        & (d["value_cr"] >= EVENT_DEF["min_deal_value_cr"])
    )
    e = d.loc[m].copy()
    return pd.DataFrame(
        {
            "symbol": e["symbol"],
            "beneficiary": e["client"],
            "beneficiary_key": e["client_norm"],
            "track": "D",
            "event_source": "deal_tape",
            "cash_date": e["trade_date"],
            "known_from": e["known_from"],
            "gross_proceeds_cr": e["value_cr"],
            "qty": e["qty"],
            "price": e["price"],
            "deal_type": e["deal_type"],
            "holder_type": e["holder_type"],
        }
    ).reset_index(drop=True)


# --------------------------------------------------------- holding events ---

def holding_drop_events(shp: pd.DataFrame, threshold: float | None = None) -> pd.DataFrame:
    """D2: promoter % declines >= threshold between consecutive quarters.

    Uses the LATEST-KNOWN version of each quarter (revisions supersede), but
    the resulting event carries known_from = the broadcast date of the quarter
    that revealed the drop, so it can never be used before it was observable.
    """
    threshold = threshold or EVENT_DEF["promoter_selldown_pp_threshold"]
    s = shp.sort_values(["symbol", "quarter_end", "known_from"])
    s = s.groupby(["symbol", "quarter_end"], as_index=False).tail(1)
    s = s.sort_values(["symbol", "quarter_end"])
    s["prev_pct"] = s.groupby("symbol")["promoter_pct"].shift(1)
    s["prev_q"] = s.groupby("symbol")["quarter_end"].shift(1)
    s["delta"] = s["promoter_pct"] - s["prev_pct"]
    # only consecutive-ish quarters (<=100 days apart) to avoid gap artefacts
    gap = (s["quarter_end"] - s["prev_q"]).dt.days
    m = (s["delta"] <= -threshold) & gap.between(60, 200)
    e = s.loc[m].copy()
    return pd.DataFrame(
        {
            "symbol": e["symbol"],
            "beneficiary": "PROMOTER GROUP (aggregate)",
            "beneficiary_key": "PROMOTER GROUP",
            "track": "D",
            "event_source": "holding_drop",
            "cash_date": e["quarter_end"],
            "known_from": e["known_from"],
            "pp_drop": -e["delta"],
            "promoter_pct_after": e["promoter_pct"],
        }
    ).reset_index(drop=True)


def pit_insider_events(insider_pit: pd.DataFrame, min_cr: float | None = None) -> pd.DataFrame:
    """D3: PIT Reg 7(2) insider SELL disclosures.

    Person-level and exactly dated, and it sees the sub-threshold trades the
    deal tape cannot. A minimum value applies because a Rs 5 lakh director sale
    is a disclosure event, not a private-banking liquidity event.
    """
    if insider_pit is None or not len(insider_pit):
        return pd.DataFrame()
    min_cr = EVENT_DEF["min_deal_value_cr"] if min_cr is None else min_cr
    d = insider_pit[
        insider_pit["is_insider_cat"]
        & insider_pit["txn_type"].str.lower().eq("sell")
        & (insider_pit["value_cr"] >= min_cr)
    ]
    if not len(d):
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "symbol": d["symbol"],
            "beneficiary": d["person"],
            "beneficiary_key": [normalize_name(x) for x in d["person"]],
            "track": "D",
            "event_source": "pit_reg7",
            "cash_date": d["trade_date"],
            "known_from": d["known_from"],
            "gross_proceeds_cr": d["value_cr"],
            "person_category": d["category"],
        }
    ).reset_index(drop=True)


def build_event_register(
    labelled_deals: pd.DataFrame,
    shp: pd.DataFrame,
    insider_pit: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """The full cash-dated event register - a standalone asset (section 8.2)."""
    a = deal_events(labelled_deals)
    b = holding_drop_events(shp)
    c = pit_insider_events(insider_pit)
    parts = [x for x in (a, b, c) if x is not None and len(x)]
    reg = pd.concat(parts, ignore_index=True, sort=False)
    # The same sale can surface on the deal tape AND in a Reg 7(2) disclosure.
    # Collapse near-duplicates: same symbol + beneficiary within 5 days.
    reg = reg.sort_values(["symbol", "beneficiary_key", "cash_date"])
    dup = (
        (reg["symbol"] == reg["symbol"].shift())
        & (reg["beneficiary_key"] == reg["beneficiary_key"].shift())
        & ((reg["cash_date"] - reg["cash_date"].shift()).dt.days.abs() <= 5)
    )
    reg = reg[~dup]
    reg["event_month"] = reg["cash_date"].dt.to_period("M").dt.to_timestamp()
    return reg.sort_values("cash_date").reset_index(drop=True)


# ------------------------------------------------------- lock-in calendar ---

def lockin_calendar(ipos: pd.DataFrame, mpc_assumption: str = LOCKIN_MPC_ASSUMPTION) -> pd.DataFrame:
    """Every future date on which a class of holder becomes free to sell."""
    rows = []
    for sym, ld, sme in zip(ipos["symbol"], ipos["listing_date"], ipos["is_sme"]):
        if pd.isna(ld):
            continue
        rows += [
            (sym, ld, "anchor_30d", ld + pd.Timedelta(days=30), "anchor"),
            (sym, ld, "anchor_90d", ld + pd.Timedelta(days=90), "anchor"),
            (sym, ld, "non_promoter_6m", ld + pd.DateOffset(months=LOCKIN["non_promoter_months"]), "non_promoter"),
            (sym, ld, "promoter_excess_6m", ld + pd.DateOffset(months=LOCKIN["promoter_excess_months"]), "promoter"),
            (sym, ld, "aif_vcf_6m", ld + pd.DateOffset(months=LOCKIN["aif_vcf_months"]), "investor"),
            (sym, ld, "mpc", ld + pd.DateOffset(months=LOCKIN[mpc_assumption]), "promoter"),
        ]
    cal = pd.DataFrame(rows, columns=["symbol", "listing_date", "lockin_type", "expiry_date", "holder_class"])
    # The calendar is knowable the day the company lists.
    cal["known_from"] = cal["listing_date"]
    return cal


def months_to_next_expiry(cal: pd.DataFrame, symbol_months: pd.DataFrame) -> pd.DataFrame:
    """For each (symbol, month) attach days to the nearest FUTURE lock-in expiry
    and whether an expiry falls inside the next 90 days."""
    out = symbol_months.copy()
    if cal.empty:
        out["days_to_lockin"] = np.nan
        out["lockin_90d"] = 0
        out["lockin_promoter_90d"] = 0
        return out

    # Precompute per-symbol sorted expiry arrays; then a vectorised
    # searchsorted per symbol group instead of a filter per panel row.
    c = cal.sort_values("expiry_date")
    all_exp = {s: g["expiry_date"].values for s, g in c.groupby("symbol")}
    prom = c[c["holder_class"] == "promoter"]
    prom_exp = {s: g["expiry_date"].values for s, g in prom.groupby("symbol")}

    days = np.full(len(out), np.nan)
    l90 = np.zeros(len(out), dtype=np.int8)
    l90p = np.zeros(len(out), dtype=np.int8)
    # H1 as actually stated ("selling WITHIN 90 days of expiry") is about the
    # window AFTER the lock-in falls away, not before it. Both are computed:
    # `lockin_90d`      - an expiry falls in the next 90 days (pre-expiry)
    # `lockin_expired_*`- an expiry fell in the LAST 90/180 days (post-expiry)
    # Conflating the two inverts the test, because pre-expiry is precisely the
    # window in which the holder is legally unable to sell.
    exp90 = np.zeros(len(out), dtype=np.int8)
    exp180 = np.zeros(len(out), dtype=np.int8)
    exp90p = np.zeros(len(out), dtype=np.int8)

    for sym, idx in out.groupby("symbol").indices.items():
        arr = all_exp.get(sym)
        if arr is None:
            continue
        m = out["month"].values[idx]
        j = np.searchsorted(arr, m, side="left")
        ok = j < len(arr)
        d = np.full(len(idx), np.nan)
        d[ok] = (arr[j[ok]] - m[ok]) / np.timedelta64(1, "D")
        days[idx] = d
        l90[idx] = ((d >= 0) & (d <= 90)).astype(np.int8)

        # most recent PAST expiry
        jb = j - 1
        okb = jb >= 0
        db = np.full(len(idx), np.nan)
        db[okb] = (m[okb] - arr[jb[okb]]) / np.timedelta64(1, "D")
        exp90[idx] = ((db >= 0) & (db <= 90)).astype(np.int8)
        exp180[idx] = ((db >= 0) & (db <= 180)).astype(np.int8)

        parr = prom_exp.get(sym)
        if parr is not None:
            jp = np.searchsorted(parr, m, side="left")
            okp = jp < len(parr)
            dp = np.full(len(idx), np.nan)
            dp[okp] = (parr[jp[okp]] - m[okp]) / np.timedelta64(1, "D")
            l90p[idx] = ((dp >= 0) & (dp <= 90)).astype(np.int8)
            jpb = jp - 1
            okpb = jpb >= 0
            dpb = np.full(len(idx), np.nan)
            dpb[okpb] = (m[okpb] - parr[jpb[okpb]]) / np.timedelta64(1, "D")
            exp90p[idx] = ((dpb >= 0) & (dpb <= 90)).astype(np.int8)

    out["days_to_lockin"] = days
    out["lockin_90d"] = l90
    out["lockin_promoter_90d"] = l90p
    out["lockin_expired_90d"] = exp90
    out["lockin_expired_180d"] = exp180
    out["lockin_promoter_expired_90d"] = exp90p
    return out
