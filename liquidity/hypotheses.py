"""
Section 10 - test the stated hypotheses, honestly.

"I have not run this backtest, and neither should you accept the numbers below
without running it... Treat anything presented to you as an already-validated
hit rate - by any vendor - with suspicion until you have reproduced it
point-in-time."

Each test below reports an effect size with a confidence interval and states
what it CANNOT rule out. Where the data cannot test a hypothesis at all, the
test returns status NOT_TESTABLE with the reason - rather than a number that
looks like evidence.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def _prop_ci(k: int, n: int, alpha: float = 0.05):
    """Wilson interval - honest at small n, unlike the normal approximation."""
    if n == 0:
        return (np.nan, np.nan)
    z = stats.norm.ppf(1 - alpha / 2)
    p = k / n
    d = 1 + z**2 / n
    c = (p + z**2 / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _rate_row(name, k, n, base_k, base_n):
    p = k / n if n else np.nan
    b = base_k / base_n if base_n else np.nan
    lo, hi = _prop_ci(k, n)
    blo, bhi = _prop_ci(base_k, base_n)
    if n and base_n:
        tbl = [[k, n - k], [base_k, base_n - base_k]]
        try:
            _, pval = stats.fisher_exact(tbl)
        except Exception:
            pval = np.nan
    else:
        pval = np.nan
    return dict(
        test=name,
        n_treated=n, events_treated=k, rate_treated=p, ci_treated=f"[{lo:.3f}, {hi:.3f}]",
        n_control=base_n, events_control=base_k, rate_control=b,
        ci_control=f"[{blo:.3f}, {bhi:.3f}]",
        lift=p / b if b else np.nan,
        p_value=pval,
    )


# ---------------------------------------------------------------------------
# H1: lock-in expiry substantially raises the probability of promoter/early-
#     investor selling within 90 days
# ---------------------------------------------------------------------------

def test_H1_lockin(panel: pd.DataFrame, label_col: str = "y_3m") -> pd.DataFrame:
    """Compare event rate in entity-months with a lock-in expiry in the next 90
    days against entity-months without one.

    CONFOUND, stated up front: lock-in expiries cluster at 6 and 18 months after
    listing, so "lock-in expiring" is partly a proxy for "recently listed".
    Recently-listed companies differ in many ways. We therefore report the raw
    comparison AND a comparison restricted to recently-listed companies only,
    which holds the confound roughly fixed.
    """
    rows = []
    p = panel.dropna(subset=[label_col])

    def cmp(col, name, sub=None):
        q = p if sub is None else sub
        t = q[q[col] == 1]
        c = q[q[col] == 0]
        rows.append(_rate_row(name, int(t[label_col].sum()), len(t),
                              int(c[label_col].sum()), len(c)))

    # --- the hypothesis as stated: selling in the 90 days AFTER expiry ---
    cmp("lockin_expired_90d", "H1a POST-expiry: an expiry fell in the last 90d")
    cmp("lockin_promoter_expired_90d", "H1b POST-expiry, promoter-class lock-in only")
    cmp("lockin_expired_180d", "H1c POST-expiry: an expiry fell in the last 180d")

    # --- the pre-expiry window, for contrast: the holder is legally UNABLE to
    #     sell here, so a positive result would indicate confounding ---
    cmp("lockin_90d", "H1d PRE-expiry (contrast): expiry falls in next 90d")
    cmp("lockin_promoter_90d", "H1e PRE-expiry, promoter-class only")

    # --- confound control: lock-in expiries cluster 6 and 18 months after
    #     listing, so "expiry nearby" partly proxies "recently listed".
    #     Restricting to the recent-IPO cohort holds that roughly fixed. ---
    r = p[p["months_since_listing"] <= 36]
    cmp("lockin_expired_90d", "H1f CONTROLLED post-expiry, within recent-IPO cohort (<=36m)", r)
    cmp("lockin_90d", "H1g CONTROLLED pre-expiry, within recent-IPO cohort (<=36m)", r)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# H2: a disclosed SEBI trading plan is followed by actual selling in the great
#     majority of cases
# ---------------------------------------------------------------------------

def test_H2_trading_plans(
    announcements: pd.DataFrame,
    events: pd.DataFrame,
    deals: pd.DataFrame,
    insider_pit: pd.DataFrame | None = None,
    cooling_off_days: int = 120,
    follow_window_days: int = 365,
) -> dict:
    """For each disclosed trading plan, did an insider sale follow?

    The regulation makes trading impossible for 120 days after disclosure and
    the plan is irrevocable except in defined circumstances, so the natural
    test window opens at +120 days.

    MEASUREMENT LIMIT, stated plainly: our observable "did they sell" comes
    from the bulk/block deal tape, which only captures trades above the bulk
    (0.5% of equity) or block (value) thresholds. Trading-plan sales below
    those thresholds are INVISIBLE to us. So the follow-through rate computed
    here is a LOWER BOUND, not an estimate of the true rate.
    """
    if announcements is None or not len(announcements):
        return {"status": "NOT_TESTABLE", "reason": "no announcement data"}
    tp = announcements[announcements["is_trading_plan"]].copy()
    if not len(tp):
        return {"status": "NOT_TESTABLE", "reason": "no trading-plan disclosures found"}

    sells = deals[deals["side"] == "SELL"]
    sell_by_sym = {s: np.sort(g["trade_date"].values) for s, g in sells.groupby("symbol")}
    ins = deals[(deals["side"] == "SELL") & deals["is_insider"]]
    ins_by_sym = {s: np.sort(g["trade_date"].values) for s, g in ins.groupby("symbol")}

    # The authoritative test: PIT Reg 7(2) disclosures capture insider sales
    # regardless of size, which is what a trading plan actually produces.
    # COVERAGE GUARD. The Reg 7(2) endpoint retains only the last ~20
    # disclosures per symbol. For an actively-traded company those 20 records
    # may not reach back to a given plan's execution window at all - in which
    # case "no sale observed" means "not observed", not "did not sell".
    # Counting those as failures would understate follow-through badly (it
    # roughly halves it here), so plans whose window is not spanned by the
    # retained records are EXCLUDED rather than scored as non-events.
    pit_by_sym, pit_span = {}, {}
    if insider_pit is not None and len(insider_pit):
        ps = insider_pit[
            insider_pit["is_insider_cat"] & insider_pit["txn_type"].str.lower().eq("sell")
        ]
        pit_by_sym = {s: np.sort(g["trade_date"].values) for s, g in ps.groupby("symbol")}
        # span uses ALL retained records for the symbol, not just sells:
        # a buy disclosure equally proves the window was observable
        for s, g in insider_pit.groupby("symbol"):
            pit_span[s] = (g["trade_date"].min(), g["trade_date"].max())

    rows = []
    for sym, kf in zip(tp["symbol"], tp["known_from"]):
        start = np.datetime64(pd.Timestamp(kf) + pd.Timedelta(days=cooling_off_days))
        end = np.datetime64(pd.Timestamp(kf) + pd.Timedelta(days=cooling_off_days + follow_window_days))
        def hit(d):
            a = d.get(sym)
            if a is None:
                return 0
            return int(np.searchsorted(a, end, "right") > np.searchsorted(a, start, "left"))
        span = pit_span.get(sym)
        covered = bool(
            span is not None
            and pd.notna(span[0]) and pd.notna(span[1])
            and span[0] <= pd.Timestamp(start) and span[1] >= pd.Timestamp(end)
        )
        rows.append(
            dict(
                symbol=sym,
                disclosed=pd.Timestamp(kf),
                any_sell_after_cooloff=hit(sell_by_sym),
                insider_sell_after_cooloff=hit(ins_by_sym),
                pit_insider_sell_after_cooloff=hit(pit_by_sym) if pit_by_sym else np.nan,
                pit_window_covered=covered,
                window_complete=pd.Timestamp(kf) + pd.Timedelta(days=cooling_off_days + follow_window_days)
                <= deals["trade_date"].max(),
            )
        )
    d = pd.DataFrame(rows)
    comp = d[d["window_complete"]]
    n = len(comp)
    k_ins = int(comp["insider_sell_after_cooloff"].sum())
    k_any = int(comp["any_sell_after_cooloff"].sum())
    lo_i, hi_i = _prop_ci(k_ins, n)
    lo_a, hi_a = _prop_ci(k_any, n)
    out = {
        "status": "TESTED",
        "n_plans_total": int(len(d)),
        "n_plans_complete_window": n,
        "n_symbols": int(d["symbol"].nunique()),
        "dealtape_insider_sell_LOWER_BOUND": k_ins / n if n else np.nan,
        "dealtape_insider_ci": f"[{lo_i:.3f}, {hi_i:.3f}]",
        "any_large_sell_LOWER_BOUND": k_any / n if n else np.nan,
        "any_ci": f"[{lo_a:.3f}, {hi_a:.3f}]",
        "caveat": (
            "The deal tape only observes trades above bulk (0.5% of equity) / "
            "block thresholds, and trading plans are typically filed by "
            "directors and KMP whose sales fall below them. Deal-tape figures "
            "are LOWER BOUNDS. The Reg 7(2) figure below is the meaningful one."
        ),
        "detail": d,
    }
    if pit_by_sym:
        k_pit = int(comp["pit_insider_sell_after_cooloff"].fillna(0).sum())
        lo_p, hi_p = _prop_ci(k_pit, n)
        out["PIT_uncorrected_followthrough"] = k_pit / n if n else np.nan
        out["PIT_uncorrected_ci"] = f"[{lo_p:.3f}, {hi_p:.3f}]"
        out["PIT_uncorrected_note"] = (
            "biased DOWN - counts plans the Reg 7(2) retention window never observed"
        )
        cov = comp[comp["pit_window_covered"]]
        nc = len(cov)
        kc = int(cov["pit_insider_sell_after_cooloff"].fillna(0).sum())
        lo_c, hi_c = _prop_ci(kc, nc)
        out["n_plans_with_pit_coverage"] = nc
        out["PIT_reg7_followthrough_COVERAGE_CORRECTED"] = kc / nc if nc else np.nan
        out["PIT_corrected_ci"] = f"[{lo_c:.3f}, {hi_c:.3f}]"
    return out


# ---------------------------------------------------------------------------
# H8: prior liquidity history predicts repeat events
# ---------------------------------------------------------------------------

def test_H8_repeat(panel: pd.DataFrame, label_col: str = "y_12m") -> pd.DataFrame:
    """Rule 144 dribble-out logic suggests holders sell in tranches. India has
    no equivalent volume constraint, so this is a genuine open question."""
    p = panel.dropna(subset=[label_col])
    rows = []
    t = p[p["ever_sold"] == 1]
    c = p[p["ever_sold"] == 0]
    rows.append(_rate_row("H8: any prior observed sell-down", int(t[label_col].sum()), len(t),
                          int(c[label_col].sum()), len(c)))
    for lo, hi, lbl in [(1, 1, "exactly 1 prior"), (2, 3, "2-3 prior"), (4, 999, "4+ prior")]:
        t2 = p[(p["prior_event_count"] >= lo) & (p["prior_event_count"] <= hi)]
        rows.append(_rate_row(f"H8: {lbl}", int(t2[label_col].sum()), len(t2),
                              int(c[label_col].sum()), len(c)))
    return pd.DataFrame(rows)


def test_H8_mechanism(panel: pd.DataFrame, events: pd.DataFrame, label_col: str = "y_6m") -> dict:
    """H8 refinement: is it a dribble-out process, or just persistent type?

    The headline H8 test ("has this entity ever sold?") cannot distinguish
    three very different stories, which have different consequences for how the
    feature should be used:

      (a) DRIBBLE-OUT   selling proceeds in regular tranches, so a past sale
                        predicts the next one at a predictable spacing
      (b) CLUSTERING    selling comes in bursts, so a past sale predicts only
                        the near term and the signal decays
      (c) PERSISTENT TYPE
                        some holders are simply sellers, so the flag is a
                        standing attribute with no time structure

    Three diagnostics separate them, all runnable on already-collected data:
      1. empirical hazard as a function of months since last event
         - flat => (c), decaying => (b), humped => (a)
      2. coefficient of variation of inter-event gaps
         - CV < 1 => more regular than random => (a)
         - CV > 1 => over-dispersed / bursty  => (b)
      3. the same comparison conditioned on remaining stake, since "never sold"
         partly means "has nothing left to sell"
    """
    out = {}
    q = panel[panel["ever_sold"] == 1].copy()
    base = panel.loc[panel["ever_sold"] == 0, label_col].mean()
    bins = [0, 3, 6, 9, 12, 18, 24, 36, 60, 10_000]
    q["bin"] = pd.cut(q["months_since_last_event"], bins=bins, right=False)
    haz = q.groupby("bin", observed=True).agg(n=(label_col, "size"), rate=(label_col, "mean"))
    haz["lift_vs_never_sold"] = haz["rate"] / base if base else np.nan
    out["never_sold_baseline"] = float(base)
    out["hazard_by_recency"] = haz.reset_index().astype({"bin": str})

    d = events[events["event_source"].isin(["deal_tape", "pit_reg7"])].dropna(
        subset=["beneficiary_key", "cash_date"]
    )
    d = d.sort_values(["beneficiary_key", "symbol", "cash_date"])
    g = d.groupby(["beneficiary_key", "symbol"])
    gaps = g["cash_date"].diff().dt.days.dropna()
    gaps = gaps[gaps > 0]
    out["n_repeat_seller_pairs"] = int((g.size() > 1).sum())
    if len(gaps):
        out["gap_days_median"] = float(gaps.median())
        out["gap_days_iqr"] = f"[{gaps.quantile(.25):.0f}, {gaps.quantile(.75):.0f}]"
        out["gap_cv"] = float(gaps.std() / gaps.mean())
        out["gap_interpretation"] = (
            "CV > 1: over-dispersed, i.e. bursty rather than regular. The Rule-144 "
            "dribble-out analogy does NOT transfer - India imposes no equivalent "
            "volume cap, and sellers execute in campaigns."
            if out["gap_cv"] > 1
            else "CV < 1: more regular than random - consistent with dribble-out."
        )

    rows = []
    for lo, hi, lbl in [(0, 20, "<20%"), (20, 50, "20-50%"), (50, 101, ">50%")]:
        a = panel[panel["promoter_pct"].between(lo, hi, inclusive="left")]
        if not len(a):
            continue
        t1 = a.loc[a["ever_sold"] == 1, label_col].mean()
        t0 = a.loc[a["ever_sold"] == 0, label_col].mean()
        rows.append(dict(promoter_stake=lbl, n=len(a), ever_sold_rate=t1,
                         never_sold_rate=t0, lift=t1 / t0 if t0 else np.nan))
    out["conditioned_on_remaining_stake"] = pd.DataFrame(rows)
    return out


# ---------------------------------------------------------------------------
# Hypotheses that this data CANNOT test
# ---------------------------------------------------------------------------

NOT_TESTABLE = {
    "H3 (Pvt->Public + auditor upgrade + IPO-experienced CFO -> DRHP in 24m)":
        "Requires MCA21 filings (name change, ADT-1, DIR-12) and licensed LinkedIn. "
        "MCA is not machine-accessible from this environment (403). Track A pre-IPO "
        "is entirely unbacked here.",
    "H4 (lead-investor fund vintage 8+ years raises Track-C hazard)":
        "Requires private-market fund formation / AIF registration data "
        "(Tracxn / VCCEdge / Venture Intelligence). Not accessible.",
    "H5 (rating-rationale intent language leads press by 4+ months)":
        "Requires CRISIL/ICRA/CARE rationale corpus + NLP pipeline. Agency sites "
        "reachable but the rationale corpus was not licensed or ingested here.",
    "H6 (4-6 quarters margin uplift -> Track-B sale)":
        "Requires MCA AOC-4 financials for unlisted companies. Not accessible.",
    "H7 (ESOP buyback announcement -> founder secondary within 6m)":
        "Track C is private-market; secondary transactions are not in exchange data.",
    "H9 (promoter age 58+ without next-gen board induction)":
        "Requires MCA DIR-12 director data with dates of birth. Not accessible.",
    "H10 (real-estate / philanthropic vehicle activity precedes events)":
        "Requires property registry and trust registration data. Not accessible.",
}
