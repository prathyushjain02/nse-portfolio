"""
T3 Imminent - a RULES ENGINE, not a model (section 7.2).

"T3 can and should be a rules engine, not a model - the signals are
near-deterministic. Ship T3 first; it delivers value in weeks and generates the
labelled outcomes you need to train T1 and T2."

Each rule emits an alert with a deterministic expiry/eligibility date, the
holder class it applies to, and a confidence tier. No fitting, no training.
"""

from __future__ import annotations

import pandas as pd

from liquidity.config import TRADING_PLAN


def rule_trading_plan(announcements: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """SEBI PIT Reg 5: trading cannot commence until 120 calendar days after
    public disclosure of the plan, and the plan is irrevocable except in
    defined exceptional circumstances.

    This is a legally-enforced lead time, not a statistical estimate - the
    crown jewel of Track D (section 4.4).
    """
    if announcements is None or not len(announcements):
        return pd.DataFrame()
    tp = announcements[announcements["is_trading_plan"] & (announcements["known_from"] <= as_of)].copy()
    if not len(tp):
        return pd.DataFrame()
    cool = pd.Timedelta(days=TRADING_PLAN["cooling_off_days"])
    tp["execution_opens"] = tp["known_from"] + cool
    tp["days_to_execution"] = (tp["execution_opens"] - as_of).dt.days
    tp = tp[tp["days_to_execution"].between(-365, 400)]
    return pd.DataFrame(
        {
            "symbol": tp["symbol"],
            "rule": "trading_plan_disclosed",
            "signal_date": tp["known_from"],
            "eligible_date": tp["execution_opens"],
            "days_to_eligible": tp["days_to_execution"],
            "holder_class": "insider",
            "confidence": "very_high",
            "basis": "SEBI PIT Reg 5 - 120-day cooling off, plan irrevocable",
        }
    ).reset_index(drop=True)


def rule_lockin_expiry(lockin_cal: pd.DataFrame, as_of: pd.Timestamp, horizon_days: int = 90) -> pd.DataFrame:
    """SEBI ICDR lock-in expiries falling in the next `horizon_days`.

    Lock-in expiry does not compel a sale - it is the necessary condition. It
    converts a prediction problem into a prioritisation problem (section 4.1).
    """
    if lockin_cal is None or not len(lockin_cal):
        return pd.DataFrame()
    c = lockin_cal[lockin_cal["known_from"] <= as_of].copy()
    c["days_to_eligible"] = (c["expiry_date"] - as_of).dt.days
    c = c[c["days_to_eligible"].between(0, horizon_days)]
    return pd.DataFrame(
        {
            "symbol": c["symbol"],
            "rule": "lockin_expiry_" + c["lockin_type"],
            "signal_date": c["listing_date"],
            "eligible_date": c["expiry_date"],
            "days_to_eligible": c["days_to_eligible"],
            "holder_class": c["holder_class"],
            "confidence": "high",
            "basis": "SEBI ICDR 2018 lock-in schedule from allotment",
        }
    ).reset_index(drop=True)


def rule_promoter_trend(shp: pd.DataFrame, as_of: pd.Timestamp, min_pp: float = 0.5) -> pd.DataFrame:
    """Promoter stake trending down across consecutive quarters - a sell-down
    already in progress usually continues (section 4.4)."""
    if shp is None or not len(shp):
        return pd.DataFrame()
    s = shp[shp["known_from"] <= as_of].sort_values(["symbol", "quarter_end", "known_from"])
    s = s.groupby(["symbol", "quarter_end"], as_index=False).tail(1)
    s = s.sort_values(["symbol", "quarter_end"])
    g = s.groupby("symbol")
    last = g.tail(1).set_index("symbol")["promoter_pct"]
    prev = g.nth(-2)
    prev = prev.set_index("symbol")["promoter_pct"] if len(prev) else pd.Series(dtype=float)
    delta = (last - prev.reindex(last.index)).dropna()
    hits = delta[delta <= -min_pp]
    if not len(hits):
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "symbol": hits.index,
            "rule": "promoter_stake_declining",
            "signal_date": as_of,
            "eligible_date": as_of,
            "days_to_eligible": 0,
            "holder_class": "promoter",
            "confidence": "medium",
            "basis": f"promoter+group down {-hits.values.round(2)} pp QoQ",
        }
    ).reset_index(drop=True)


def run_t3(
    as_of: pd.Timestamp,
    announcements: pd.DataFrame | None = None,
    lockin_cal: pd.DataFrame | None = None,
    shp: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Daily alert list. Everything here is deterministic and explainable to
    compliance: each row cites the regulation that creates the lead time."""
    parts = [
        rule_trading_plan(announcements, as_of),
        rule_lockin_expiry(lockin_cal, as_of),
        rule_promoter_trend(shp, as_of),
    ]
    parts = [p for p in parts if p is not None and len(p)]
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    order = {"very_high": 0, "high": 1, "medium": 2}
    out["_o"] = out["confidence"].map(order).fillna(9)
    return out.sort_values(["_o", "days_to_eligible"]).drop(columns="_o").reset_index(drop=True)
