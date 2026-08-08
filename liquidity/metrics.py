"""
Section 8.4 metrics - and the ones the framework says to ignore.

"Ignore accuracy and raw AUC. Base rate is roughly 2-5% per entity-year; a
model predicting 'no event' always is ~96% accurate and useless."

We compute AUC anyway, but only as a ranking diagnostic reported alongside the
metrics that actually decide whether this is commercially usable:
  - Precision@K at K = 25, 50, 100 (the RM capacity constraint)
  - Lift over base rate by decile
  - Calibration by decile
  - Lead-time distribution   <- "the metric the business actually cares about"
  - Wallet-weighted precision
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


def precision_at_k(y: np.ndarray, score: np.ndarray, k: int) -> float:
    if len(y) == 0:
        return np.nan
    k = min(k, len(y))
    idx = np.argsort(-score)[:k]
    return float(np.mean(y[idx]))


def lift_at_k(y: np.ndarray, score: np.ndarray, k: int) -> float:
    base = np.mean(y) if len(y) else np.nan
    if not base:
        return np.nan
    return precision_at_k(y, score, k) / base


def wallet_weighted_precision(y, score, wallet, k: int) -> float:
    """Precision weighted by realised proceeds: catching one Rs 500 cr event is
    worth twenty Rs 10 cr ones (section 8.4)."""
    if len(y) == 0:
        return np.nan
    k = min(k, len(y))
    idx = np.argsort(-score)[:k]
    captured = np.nansum(wallet[idx])
    total = np.nansum(wallet)
    return float(captured / total) if total > 0 else np.nan


def decile_table(y, score, wallet=None) -> pd.DataFrame:
    df = pd.DataFrame({"y": y, "score": score})
    if wallet is not None:
        df["wallet"] = wallet
    df["decile"] = pd.qcut(df["score"].rank(method="first", ascending=False), 10, labels=False)
    g = df.groupby("decile").agg(
        n=("y", "size"),
        events=("y", "sum"),
        realised_rate=("y", "mean"),
        predicted_rate=("score", "mean"),
    )
    g["lift"] = g["realised_rate"] / df["y"].mean() if df["y"].mean() > 0 else np.nan
    if wallet is not None:
        g["wallet_cr"] = df.groupby("decile")["wallet"].sum()
    return g.reset_index()


def calibration_table(y, score, bins: int = 10) -> pd.DataFrame:
    """Predicted vs realised. 'A well-calibrated 30% must mean 30%.'"""
    df = pd.DataFrame({"y": y, "score": score})
    try:
        df["bin"] = pd.qcut(df["score"].rank(method="first"), bins, labels=False)
    except ValueError:
        df["bin"] = 0
    g = df.groupby("bin").agg(n=("y", "size"), predicted=("score", "mean"), realised=("y", "mean"))
    g["abs_error"] = (g["predicted"] - g["realised"]).abs()
    return g.reset_index()


def calibration_error(y, score, bins: int = 10) -> float:
    t = calibration_table(y, score, bins)
    return float(np.average(t["abs_error"], weights=t["n"]))


def lead_time_distribution(
    scored_panel: pd.DataFrame,
    events: pd.DataFrame,
    threshold: float,
    score_col: str = "score",
) -> pd.DataFrame:
    """Months between FIRST score-above-threshold and the cash date.

    For each event, find the earliest month in which the model flagged that
    entity above threshold, in the 36 months before the event, and with no
    intervening event. Reported as median and IQR.
    """
    p = scored_panel[scored_panel[score_col] >= threshold][["symbol", "month", score_col]]
    if p.empty:
        return pd.DataFrame(columns=["symbol", "cash_date", "first_flag", "lead_months"])
    flags = {s: np.sort(g["month"].values) for s, g in p.groupby("symbol")}

    rows = []
    for sym, cd in zip(events["symbol"], events["cash_date"]):
        arr = flags.get(sym)
        if arr is None:
            continue
        window_start = np.datetime64(pd.Timestamp(cd) - pd.DateOffset(months=36))
        cd64 = np.datetime64(pd.Timestamp(cd))
        sel = arr[(arr >= window_start) & (arr < cd64)]
        if len(sel) == 0:
            continue
        first = pd.Timestamp(sel[0])
        rows.append(
            dict(
                symbol=sym,
                cash_date=pd.Timestamp(cd),
                first_flag=first,
                lead_months=(pd.Timestamp(cd) - first).days / 30.44,
            )
        )
    return pd.DataFrame(rows)


def evaluate(
    y: np.ndarray,
    score: np.ndarray,
    wallet: np.ndarray | None = None,
    ks=(25, 50, 100),
    label: str = "",
) -> dict:
    out = {"label": label, "n": int(len(y)), "events": int(np.sum(y)), "base_rate": float(np.mean(y)) if len(y) else np.nan}
    for k in ks:
        out[f"p@{k}"] = precision_at_k(y, score, k)
        out[f"lift@{k}"] = lift_at_k(y, score, k)
        if wallet is not None:
            out[f"wallet_p@{k}"] = wallet_weighted_precision(y, score, wallet, k)
    try:
        out["auc"] = float(roc_auc_score(y, score)) if len(np.unique(y)) > 1 else np.nan
    except Exception:
        out["auc"] = np.nan
    out["cal_error"] = calibration_error(y, score) if len(np.unique(y)) > 1 else np.nan
    return out
