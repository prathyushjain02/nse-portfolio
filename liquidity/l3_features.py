"""
L3 Feature engineering - PIT-safe by construction.

Every feature for (symbol, month M) is built ONLY from facts with
known_from <= start of month M. The builder walks months forward and never
looks at a frame it has not first passed through PITStore.as_of().

Feature families present here (the framework lists eight; families 1-6 are
mostly MCA/private-market sourced and are stubbed with an explicit
NOT_AVAILABLE marker rather than silently omitted - see FAMILY_COVERAGE):

  3. Cap table         - promoter %, level and trend  (from shareholding)
  5. Personnel         - NOT AVAILABLE (needs MCA DIR-12 / LinkedIn)
  7. Market regime     - CONTROL, not a predictor (section 6). Without it the
                         model learns the 2024-25 small-cap boom and mistakes
                         the cycle for entity-level skill.
  D. Track-D specific  - lock-in calendar, deal-tape history, pledge, prior
                         liquidity history (H8), insider activity.

Tier discipline (section 7.2): each feature is tagged T1/T2/T3. Late
confirmatory signals (trading plans, OFS announcements) are T3 ONLY and are
excluded by construction from T1/T2 models, so they cannot leak the label.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FAMILY_COVERAGE = {
    "1_structural_readiness": "NOT AVAILABLE - requires MCA21 (MGT-14, PAS-3, ADT-1, DIR-12)",
    "2_financial_trajectory": "NOT AVAILABLE - requires MCA AOC-4 financials",
    "3_cap_table": "PARTIAL - promoter aggregate % from exchange shareholding patterns",
    "4_investor_clock": "NOT AVAILABLE - requires AIF registration / fund vintage data",
    "5_personnel": "NOT AVAILABLE - requires MCA DIR-12 / licensed LinkedIn",
    "6_advisor": "NOT AVAILABLE - requires MCA ADT-1 / RTA disclosures",
    "7_market_regime": "AVAILABLE - built from the deal tape and IPO calendar",
    "8_text_nlp": "PARTIAL - announcement subject taxonomy only, no rationale NLP",
    "D_track_d": "AVAILABLE - lock-in calendar, deal tape, pledge, insider history",
}

# tier -> features permitted. T3 gets everything including confirmatory signals.
TIER_FEATURES = {
    "T1": [
        "months_since_listing", "is_recent_ipo", "log_days_to_lockin",
        "lockin_expired_90d", "lockin_expired_180d",
        "promoter_pct", "promoter_pct_missing", "promoter_high", "promoter_low",
        "prior_event_count", "months_since_last_event", "ever_sold",
        "size_decile", "regime_event_rate", "regime_ipo_count",
    ],
    "T2": [
        "months_since_listing", "is_recent_ipo", "log_days_to_lockin",
        "lockin_expired_90d", "lockin_expired_180d",
        "promoter_pct", "promoter_pct_missing", "promoter_high", "promoter_low",
        "promoter_delta_1q", "promoter_delta_4q", "promoter_declining",
        "prior_event_count", "months_since_last_event", "ever_sold",
        "prior_event_value_cr", "deal_activity_3m", "deal_activity_12m",
        "institutional_sell_3m", "any_deal_12m",
        "pledge_disclosed_12m",
        "size_decile", "regime_event_rate", "regime_ipo_count",
    ],
    "T3": [
        "months_since_listing", "is_recent_ipo", "log_days_to_lockin",
        "lockin_90d", "lockin_promoter_90d",
        "lockin_expired_90d", "lockin_expired_180d", "lockin_promoter_expired_90d",
        "promoter_pct", "promoter_pct_missing", "promoter_high", "promoter_low",
        "promoter_delta_1q", "promoter_delta_4q", "promoter_declining",
        "prior_event_count", "months_since_last_event", "ever_sold",
        "prior_event_value_cr", "deal_activity_3m", "deal_activity_12m",
        "institutional_sell_3m", "any_deal_12m",
        "pledge_disclosed_12m",
        "trading_plan_active", "trading_plan_ever",
        "ofs_announced_3m",
        "size_decile", "regime_event_rate", "regime_ipo_count",
    ],
}


def month_index(start: str, end: str) -> pd.DatetimeIndex:
    return pd.date_range(start=start, end=end, freq="MS")


def build_panel(
    universe: pd.DataFrame,
    months: pd.DatetimeIndex,
    events: pd.DataFrame,
    deals: pd.DataFrame,
    shp: pd.DataFrame,
    lockin_cal: pd.DataFrame,
    announcements: pd.DataFrame | None,
    pledge: pd.DataFrame | None,
    verbose: bool = True,
) -> pd.DataFrame:
    """One row per (symbol, month) with PIT-safe features and forward labels.

    `universe` needs columns: symbol, listing_date.
    Entities enter the panel the month after listing and stay until the end
    (no survivorship filter - non-events are the majority of the panel).
    """
    uni = universe.dropna(subset=["symbol"]).drop_duplicates("symbol")
    sym_list = uni["symbol"].tolist()
    listing = dict(zip(uni["symbol"], uni["listing_date"]))

    # ---- pre-index the fact tables for fast as-of slicing ----
    dl = deals.sort_values("known_from")
    ev = events.sort_values("known_from")
    sh = shp.sort_values("known_from")
    ann = announcements.sort_values("known_from") if announcements is not None and len(announcements) else None
    pl = pledge.sort_values("known_from") if pledge is not None and len(pledge) else None

    # market cap proxy: trailing 12m traded value on the deal tape is a poor
    # proxy for size but it is the only size signal available from this feed.
    rows = []
    for m in months:
        m0 = pd.Timestamp(m)

        # -------- PIT slices: only what was KNOWN at the start of month m ----
        dl_k = dl[dl["known_from"] < m0]
        ev_k = ev[ev["known_from"] < m0]
        sh_k = sh[sh["known_from"] < m0]

        # active universe: listed strictly before this month
        active = [s for s in sym_list if pd.notna(listing.get(s)) and listing[s] < m0]
        if not active:
            continue
        idx = pd.Index(active, name="symbol")
        f = pd.DataFrame(index=idx)

        # ---- tenure ----
        ld = pd.Series({s: listing[s] for s in active})
        f["months_since_listing"] = ((m0 - ld).dt.days / 30.44).round(1)
        f["is_recent_ipo"] = (f["months_since_listing"] <= 24).astype(int)

        # ---- cap table (family 3) ----
        s_latest = (
            sh_k.sort_values(["quarter_end", "known_from"])
            .groupby("symbol")
            .tail(1)
            .set_index("symbol")
        )
        f["promoter_pct"] = s_latest["promoter_pct"].reindex(idx)
        f["promoter_pct_missing"] = f["promoter_pct"].isna().astype(int)
        f["promoter_high"] = (f["promoter_pct"] > 50).astype(float)
        f["promoter_low"] = (f["promoter_pct"] < 26).astype(float)

        # trend: latest vs 1 and 4 quarters back (latest-known versions only)
        hist = (
            sh_k.sort_values(["symbol", "quarter_end", "known_from"])
            .groupby(["symbol", "quarter_end"], as_index=False)
            .tail(1)
        )
        g = hist.sort_values(["symbol", "quarter_end"]).groupby("symbol")["promoter_pct"]
        d1 = (g.nth(-1) - g.nth(-2)) if len(hist) else pd.Series(dtype=float)
        last = g.last()
        # 4-quarters-back value
        back4 = g.nth(-5) if len(hist) else pd.Series(dtype=float)
        f["promoter_delta_1q"] = d1.reindex(idx)
        f["promoter_delta_4q"] = (last - back4).reindex(idx)
        f["promoter_declining"] = (f["promoter_delta_1q"] < -0.05).astype(float)

        # ---- prior liquidity history (H8) ----
        pe = ev_k.groupby("symbol").agg(
            prior_event_count=("cash_date", "size"),
            last_event=("cash_date", "max"),
            prior_event_value_cr=("gross_proceeds_cr", "sum"),
        )
        f["prior_event_count"] = pe["prior_event_count"].reindex(idx).fillna(0)
        f["prior_event_value_cr"] = np.log1p(pe["prior_event_value_cr"].reindex(idx).fillna(0))
        f["ever_sold"] = (f["prior_event_count"] > 0).astype(int)
        mse = ((m0 - pe["last_event"].reindex(idx)).dt.days / 30.44)
        f["months_since_last_event"] = mse.fillna(999).clip(upper=999)

        # ---- deal-tape activity ----
        for win, lbl in ((3, "3m"), (12, "12m")):
            w = dl_k[dl_k["known_from"] >= m0 - pd.DateOffset(months=win)]
            f[f"deal_activity_{lbl}"] = np.log1p(
                w.groupby("symbol")["value_cr"].sum().reindex(idx).fillna(0)
            )
        w3 = dl_k[dl_k["known_from"] >= m0 - pd.DateOffset(months=3)]
        insti = w3[(w3["side"] == "SELL") & (w3["holder_type"] == "institution")]
        f["institutional_sell_3m"] = np.log1p(
            insti.groupby("symbol")["value_cr"].sum().reindex(idx).fillna(0)
        )
        f["any_deal_12m"] = (f["deal_activity_12m"] > 0).astype(int)

        # size proxy: log trailing-12m traded value on the deal tape
        f["size_proxy"] = f["deal_activity_12m"]
        f["size_decile"] = pd.qcut(
            f["size_proxy"].rank(method="first"), 10, labels=False, duplicates="drop"
        ).astype(float)

        # ---- pledge (Reg 31) ----
        if pl is not None:
            plw = pl[(pl["known_from"] < m0) & (pl["known_from"] >= m0 - pd.DateOffset(months=12))]
            f["pledge_disclosed_12m"] = (
                plw.groupby("symbol").size().reindex(idx).fillna(0).clip(upper=1)
            )
        else:
            f["pledge_disclosed_12m"] = 0.0

        # ---- T3 confirmatory signals ----
        if ann is not None:
            a_k = ann[ann["known_from"] < m0]
            tp = a_k[a_k["is_trading_plan"]]
            # a plan disclosed 0-12 months ago is "active" (120-day cooling off
            # then execution window)
            tp_recent = tp[tp["known_from"] >= m0 - pd.DateOffset(months=12)]
            f["trading_plan_active"] = (
                tp_recent.groupby("symbol").size().reindex(idx).fillna(0).clip(upper=1)
            )
            f["trading_plan_ever"] = (
                tp.groupby("symbol").size().reindex(idx).fillna(0).clip(upper=1)
            )
            ofs = a_k[
                a_k["subject"].str.contains("Offer", case=False, na=False)
                & (a_k["known_from"] >= m0 - pd.DateOffset(months=3))
            ]
            f["ofs_announced_3m"] = (
                ofs.groupby("symbol").size().reindex(idx).fillna(0).clip(upper=1)
            )
        else:
            f["trading_plan_active"] = 0.0
            f["trading_plan_ever"] = 0.0
            f["ofs_announced_3m"] = 0.0

        # ---- market regime CONTROL (family 7) ----
        # trailing 12m panel-wide event rate and IPO count. Same value for every
        # entity in the month: it explains the cycle so the entity features do
        # not have to.
        w12 = ev_k[ev_k["known_from"] >= m0 - pd.DateOffset(months=12)]
        f["regime_event_rate"] = len(w12) / max(len(active), 1) * 100
        f["regime_ipo_count"] = int(
            ((ld >= m0 - pd.DateOffset(months=12)) & (ld < m0)).sum()
        )

        f["month"] = m0
        rows.append(f.reset_index())

        if verbose and m0.month == 1:
            print(f"    panel {m0:%Y-%m}: {len(f):,} entities", flush=True)

    panel = pd.concat(rows, ignore_index=True)

    # ---- lock-in calendar features ----
    from liquidity.l3_events import months_to_next_expiry

    panel = months_to_next_expiry(lockin_cal, panel)
    panel["log_days_to_lockin"] = np.log1p(panel["days_to_lockin"].fillna(3650).clip(0, 3650))

    return panel


def attach_labels(panel: pd.DataFrame, events: pd.DataFrame, horizons=(3, 6, 12, 24)) -> pd.DataFrame:
    """Forward labels: did a CASH-DATED event occur within H months of month M?

    Labels use cash_date (the truth), NOT known_from - a label is allowed to
    reference the future; that is what makes it a label. Only FEATURES are
    restricted to known_from.
    """
    p = panel.copy()
    ev = events[["symbol", "cash_date", "gross_proceeds_cr"]].dropna(subset=["cash_date"])
    by_sym = {s: g["cash_date"].sort_values().values for s, g in ev.groupby("symbol")}
    val_by_sym = {
        s: g.sort_values("cash_date")["gross_proceeds_cr"].fillna(0).values
        for s, g in ev.groupby("symbol")
    }

    months = p["month"].values
    syms = p["symbol"].values
    for H in horizons:
        end = (p["month"] + pd.DateOffset(months=H)).values
        lab = np.zeros(len(p), dtype=np.int8)
        wallet = np.zeros(len(p), dtype=float)
        t2e = np.full(len(p), np.nan)
        for i in range(len(p)):
            arr = by_sym.get(syms[i])
            if arr is None:
                continue
            lo = np.searchsorted(arr, months[i], side="left")
            hi = np.searchsorted(arr, end[i], side="right")
            if hi > lo:
                lab[i] = 1
                wallet[i] = np.nansum(val_by_sym[syms[i]][lo:hi])
                t2e[i] = (pd.Timestamp(arr[lo]) - pd.Timestamp(months[i])).days / 30.44
        p[f"y_{H}m"] = lab
        p[f"wallet_{H}m_cr"] = wallet
        p[f"months_to_event_{H}m"] = t2e
    return p
