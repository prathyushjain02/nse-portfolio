"""
Load raw parquet into typed, PIT-stamped frames and register them in L2.

This is where every source gets its `valid_from` (when it became true) and
`known_from` (when we could first have known it) assigned explicitly. Any
source where those two differ and we cannot observe the difference gets a
conservative assumed lag from config.BACKTEST["assumed_lag_days"].
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from liquidity.config import RAW_DIR, BACKTEST
from liquidity.l2_pit import PITStore


def _dt(s, fmt=None):
    return pd.to_datetime(s, format=fmt, errors="coerce")


def load_equity_master() -> pd.DataFrame:
    df = pd.read_parquet(f"{RAW_DIR}/equity_master.parquet")
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(
        columns={
            "SYMBOL": "symbol",
            "NAME OF COMPANY": "company",
            "SERIES": "series",
            "DATE OF LISTING": "listing_date",
            "ISIN NUMBER": "isin",
            "FACE VALUE": "face_value",
        }
    )
    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["series"] = df["series"].astype(str).str.strip()
    df["listing_date"] = _dt(df["listing_date"], "%d-%b-%Y")
    return df[df["series"] == "EQ"].reset_index(drop=True)


def load_ipos() -> pd.DataFrame:
    df = pd.read_parquet(f"{RAW_DIR}/ipos.parquet")
    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["listing_date"] = _dt(df["listingDate"], "%d-%b-%Y")
    df["ipo_end"] = _dt(df["ipoEndDate"], "%d-%b-%Y")
    df["issue_price"] = pd.to_numeric(
        df["issuePrice"].astype(str).str.replace(r"[^0-9.]", "", regex=True), errors="coerce"
    )
    df["is_sme"] = df["securityType"].astype(str).str.upper().eq("SME")
    return df.dropna(subset=["listing_date"])[
        ["symbol", "companyName", "listing_date", "ipo_end", "issue_price", "is_sme"]
    ].reset_index(drop=True)


def load_deals() -> pd.DataFrame:
    """Bulk + block deals. Trade date is both valid_from and known_from:
    exchanges publish these after market close on the trade date."""
    frames = []
    for kind in ("bulk_deals", "block_deals"):
        p = f"{RAW_DIR}/{kind}.parquet"
        if not os.path.exists(p):
            continue
        d = pd.read_parquet(p)
        # NSE CSVs carry a UTF-8 BOM and quote the first header cell
        d.columns = [
            c.replace("﻿", "").replace("ï»¿", "").strip().strip('"').strip()
            for c in d.columns
        ]
        ren = {}
        for c in d.columns:
            lc = c.lower()
            if lc.startswith("date"):
                ren[c] = "trade_date"
            elif lc.startswith("symbol"):
                ren[c] = "symbol"
            elif "security" in lc:
                ren[c] = "security"
            elif "client" in lc:
                ren[c] = "client"
            elif "buy" in lc and "sell" in lc:
                ren[c] = "side"
            elif "quantity" in lc:
                ren[c] = "qty"
            elif "price" in lc:
                ren[c] = "price"
            elif "remark" in lc:
                ren[c] = "remarks"
        d = d.rename(columns=ren)
        d["deal_type"] = kind.replace("_deals", "")
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = _dt(df["trade_date"], "%d-%b-%Y")
    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["client"] = df["client"].astype(str).str.strip()
    df["side"] = df["side"].astype(str).str.strip().str.upper().str[:4]
    # Indian digit grouping: "4,13,649" -> 413649
    for c in ("qty", "price"):
        df[c] = pd.to_numeric(
            df[c].astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce"
        )
    df["value_cr"] = df["qty"] * df["price"] / 1e7
    df = df.dropna(subset=["trade_date", "symbol"])
    # A large trade can be reported on BOTH the bulk and the block tape. Without
    # this dedup every such event is double-counted in the register and in
    # wallet sizing. Prefer the block record (block deals are the cleaner
    # negotiated-trade record) when both exist.
    df["_pref"] = (df["deal_type"] == "block").astype(int)
    df = (
        df.sort_values("_pref", ascending=False)
        .drop_duplicates(subset=["trade_date", "symbol", "client", "side", "qty", "price"], keep="first")
        .drop(columns="_pref")
    )
    df["known_from"] = df["trade_date"]  # published post-close same day
    return df.reset_index(drop=True)


def load_shareholding() -> pd.DataFrame:
    """Quarterly promoter holding. THE bitemporal table:
       valid_from = quarter end, known_from = exchange broadcast timestamp.
       Contains revisions broadcast years later - handled by L2."""
    df = pd.read_parquet(f"{RAW_DIR}/shareholding.parquet")
    df["symbol"] = df["_query_symbol"].astype(str).str.strip()
    df["quarter_end"] = _dt(df["date"], "%d-%b-%Y")
    df["submission_date"] = _dt(df["submissionDate"], "%d-%b-%Y")
    df["broadcast_date"] = _dt(
        df["broadcastDate"].astype(str).str.split(".").str[0], "%d-%b-%Y %H:%M:%S"
    )
    # known_from: broadcast if available, else submission, else quarter-end + lag
    lag = BACKTEST["assumed_lag_days"]["shareholding_pattern"]
    kf = df["broadcast_date"].fillna(df["submission_date"])
    kf = kf.fillna(df["quarter_end"] + pd.Timedelta(days=lag))
    df["known_from"] = kf.dt.normalize()
    df["promoter_pct"] = pd.to_numeric(df["pr_and_prgrp"], errors="coerce")
    df["public_pct"] = pd.to_numeric(df["public_val"], errors="coerce")
    df["is_revision"] = df["revisedStatus"].astype(str).str.contains("Revis", case=False, na=False)
    df = df.dropna(subset=["quarter_end", "symbol"])
    df = df[df["promoter_pct"].between(0, 100)]
    return df[
        [
            "symbol", "quarter_end", "submission_date", "broadcast_date", "known_from",
            "promoter_pct", "public_pct", "is_revision", "name", "isin",
        ]
    ].reset_index(drop=True)


# --------------------------------------------------------------------------
# SHP XBRL context taxonomy.
#
# The shareholding pattern has three tables: II = promoter & promoter group,
# III = public, IV = non-promoter non-public. Filers use two different context
# naming conventions (an older "DetailsOf..." style and a newer "D_..._Context"
# style), and the promoter/public distinction turns on subtle wording -
# promoter individuals are "...SharesHeldByIndividualsOrHUF" while public
# individuals are "...IndividualShareholdersHoldingNominalShareCapital...".
# Getting this wrong silently mislabels public shareholders as promoters, so
# the mapping is an explicit whitelist rather than a pattern guess.
# --------------------------------------------------------------------------
PROMOTER_STEMS = {
    "DetailsSharesHeldByIndividualsOrHUF",              # Table II Indian - individuals/HUF
    "IndividualsOrHUF",
    "DetailsOfSharesHeldByOthersIndianShareholders",    # Table II Indian - any other
    "OthersIndianShareholders",
    "DetailsOfSharesHeldByOtherForeignShareholders",    # Table II Foreign - any other
    "OtherForeignShareholders",
    "DetailsOfSharesHeldByNonResidentIndividualsOrForeignIndividuals",
    "NonResidentIndividualsOrForeignIndividuals",
    "DetailsOfSharesHeldByBodiesCorporate",
    "DetailsOfSharesHeldByForeignCompanies",
    "DetailsOfSharesHeldByCentralGovernmentOrStateGovernments",
    "DetailsOfSharesHeldByCentralGovernmentOrStateGovernmentSOrPresidentOfIndia",
    "DetailsOfSharesHeldByDirectorsAndDirectorsRelatives",  # insiders
    "CentralGovernmentOrStateGovernment",
    "ForeignCompanies",
    "BodiesCorporate",
}


def _ctx_stem(c: str) -> str:
    import re
    s = re.sub(r"\d+[DI]?$", "", str(c))
    s = re.sub(r"_Context$", "", s)
    return re.sub(r"^D_", "", s)


def load_promoter_names() -> pd.DataFrame:
    df = pd.read_parquet(f"{RAW_DIR}/promoter_names.parquet")
    df["stem"] = df["context"].map(_ctx_stem)
    df["is_promoter_ctx"] = df["stem"].isin(PROMOTER_STEMS)
    df["known_from"] = _dt(
        df["known_from"].astype(str).str.split(".").str[0], "%d-%b-%Y %H:%M:%S"
    ).dt.normalize()
    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["name"] = df["name"].astype(str).str.strip()
    return df[df["name"].str.len() > 2].reset_index(drop=True)


INSIDER_CATEGORIES = {
    "Promoters", "Promoter Group", "Director", "Key Managerial Personnel",
    "Immediate relative", "IMMEDIATE RELATIVE", "Connected Person",
    "Sponsor as per erstwhile SEBI (D & P) Regulations 1996",
}


def load_insider_pit() -> pd.DataFrame:
    """PIT Reg 7(2) continual disclosures: person-level, categorised, dated.

    This is the closest thing in Indian public data to a person-level insider
    trade register - it names the individual, states their relationship to the
    company, and gives transaction value and dates. Critically it captures
    trades BELOW the bulk/block reporting thresholds, which the deal tape
    structurally cannot see.

    Bitemporal: `acqfromDt` is when the trade happened (valid_from), the `date`
    field is the exchange broadcast timestamp (known_from). Reg 7(2) requires
    disclosure within 2 trading days, so the gap is short but non-zero.
    """
    p = f"{RAW_DIR}/insider_pit.parquet"
    if not os.path.exists(p):
        return pd.DataFrame()
    df = pd.read_parquet(p)
    out = pd.DataFrame(
        {
            "symbol": df["symbol"].astype(str).str.strip(),
            "person": df["acqName"].astype(str).str.strip(),
            "category": df["personCategory"].astype(str).str.strip(),
            "txn_type": df["tdpTransactionType"].astype(str).str.strip(),
            "mode": df.get("acqMode", pd.Series(index=df.index, dtype="object")).astype(str),
            "regulation": df.get("anex", pd.Series(index=df.index, dtype="object")).astype(str),
        }
    )
    out["trade_date"] = _dt(df["acqfromDt"], "%d-%b-%Y")
    out["known_from"] = _dt(
        df["date"].astype(str).str.split(" ").str[0], "%d-%b-%Y"
    )
    val = pd.to_numeric(df["secVal"], errors="coerce")
    out["value_cr"] = val / 1e7
    # Filers occasionally submit absurd values (paise vs rupees, or typos).
    # Anything above Rs 100,000 cr for a single insider trade is not credible;
    # drop rather than winsorise so a bad row cannot distort wallet totals.
    out.loc[out["value_cr"] > 100_000, "value_cr"] = np.nan
    out["is_insider_cat"] = out["category"].isin(INSIDER_CATEGORIES)
    # sanity: trade date must be plausible and not after we knew about it
    # Filers sometimes key a future date. A trade cannot have happened after
    # the latest date we have data for, and a future-dated "event" would create
    # a label for something that has not occurred.
    horizon = out["known_from"].max()
    ok = out["trade_date"].between(pd.Timestamp("2000-01-01"), horizon)
    out = out[ok & out["known_from"].notna()]
    # known_from can never precede the trade for a post-trade disclosure regime
    out["known_from"] = out[["known_from", "trade_date"]].max(axis=1)
    return out.reset_index(drop=True)


def load_sast() -> pd.DataFrame:
    p = f"{RAW_DIR}/sast_reg29.parquet"
    if not os.path.exists(p):
        return pd.DataFrame()
    df = pd.read_parquet(p)
    cols = {c.lower(): c for c in df.columns}
    def pick(*names):
        for n in names:
            if n in cols:
                return cols[n]
        return None
    sym, acq = pick("symbol"), pick("name", "acqname", "namofacq")
    date_c = pick("date", "dissemdt", "brdcstdt")
    out = pd.DataFrame(
        {
            "symbol": df[sym].astype(str).str.strip() if sym else np.nan,
            "acquirer": df[acq].astype(str).str.strip() if acq else np.nan,
        }
    )
    out["known_from"] = _dt(df[date_c].astype(str).str.split(" ").str[0], None) if date_c else pd.NaT
    return out.dropna(subset=["symbol"]).reset_index(drop=True)


def load_announcements() -> pd.DataFrame:
    p = f"{RAW_DIR}/announcements.parquet"
    if not os.path.exists(p):
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["symbol"] = df["symbol"].astype(str).str.strip()
    # an_dt is the broadcast timestamp -> known_from. Never use a date the
    # announcement text refers to (section 8.1).
    df["known_from"] = _dt(df["an_dt"], "%d-%b-%Y %H:%M:%S").dt.normalize()
    df["subject"] = df.get("_subject_query", pd.Series(index=df.index, dtype="object")).astype(str)
    df["desc"] = df.get("desc", pd.Series(index=df.index, dtype="object")).astype(str)
    df["is_trading_plan"] = df["subject"].str.contains("Trading Plan", case=False, na=False)
    return df.dropna(subset=["known_from"]).reset_index(drop=True)


def load_pledge() -> pd.DataFrame:
    p = f"{RAW_DIR}/pledge.parquet"
    if not os.path.exists(p):
        return pd.DataFrame()
    df = pd.read_parquet(p)
    cols = {c.lower(): c for c in df.columns}
    sym = cols.get("symbol")
    if not sym:
        return pd.DataFrame()
    dc = cols.get("date") or cols.get("dissemdt") or cols.get("brdcstdt")
    out = pd.DataFrame({"symbol": df[sym].astype(str).str.strip()})
    out["known_from"] = _dt(df[dc].astype(str).str.split(" ").str[0], None) if dc else pd.NaT
    return out.dropna(subset=["known_from"]).reset_index(drop=True)


def build_store() -> tuple[PITStore, dict]:
    """Load everything and register in the bitemporal store."""
    store = PITStore()
    frames = {}

    frames["equity"] = load_equity_master()
    frames["ipos"] = load_ipos()

    deals = load_deals()
    frames["deals"] = deals
    store.register("deals", deals, "symbol", "trade_date", "known_from")

    shp = load_shareholding()
    frames["shp"] = shp
    store.register("shp", shp, "symbol", "quarter_end", "known_from")

    try:
        pn = load_promoter_names()
        frames["promoter_names"] = pn
        store.register("promoter_names", pn, "symbol", "known_from", "known_from")
    except Exception:
        frames["promoter_names"] = pd.DataFrame()

    pit = load_insider_pit()
    if len(pit):
        frames["insider_pit"] = pit
        store.register("insider_pit", pit, "symbol", "trade_date", "known_from")

    ann = load_announcements()
    if len(ann):
        frames["announcements"] = ann
        store.register("announcements", ann, "symbol", "known_from", "known_from")

    sast = load_sast()
    if len(sast):
        frames["sast"] = sast
        store.register("sast", sast, "symbol", "known_from", "known_from")

    pl = load_pledge()
    if len(pl):
        frames["pledge"] = pl
        store.register("pledge", pl, "symbol", "known_from", "known_from")

    return store, frames
