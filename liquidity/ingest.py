"""
L1 Ingestion driver -> data/raw/*.parquet

Run:  python3 -m liquidity.ingest

Idempotent and resumable (every HTTP response is disk-cached).
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd

from liquidity.config import CACHE_DIR, RAW_DIR
from liquidity.sources.nse import NSEClient

DEALS_START = date(2015, 1, 1)
END = date(2026, 8, 7)
# Announcements are only needed over the panel window + trading-plan regime
ANN_START = date(2021, 1, 1)

TRADING_PLAN_SUBJECTS = [
    "Trading Plan under PIT",
    "Trading Plan under SEBI (PIT) Reg., 2015",
    "Trading Plan under SEBI (PIT) Regulations",
]

# Late-stage confirmatory Track-D signals. Kept OUT of T1/T2 features
# by construction (section 8.6 label leakage) - used for T3 and diagnostics.
DEAL_SUBJECTS = [
    "Offer for sale",
    "Announcement: Offer For Sale - Stock Exchange Mechanism",
    "Offer For Sale-Stock Exchange Mechanism",
    "Sale of stake",
    "Divestment of Shares",
    "Buyback - Open Market",
    "Buyback - Tender offer",
    "Increase in Promoter Group Stake",
]

# XBRL contexts that denote promoter / promoter-group members (Table II of the
# shareholding pattern), as opposed to public shareholders.
PROMOTER_CONTEXTS = {
    "D_IndividualsOrHUF_Context",
    "D_OthersIndianShareholders_Context",
    "D_BodiesCorporate_Context",
    "D_CentralGovernmentOrStateGovernment_Context",
    "D_FinancialInstitutionsOrBanks_Context",
    "D_AnyOtherSpecify_Context",
    "D_IndividualsNonResidentIndividualsOrForeignIndividuals_Context",
    "D_ForeignPortfolioInvestor_Context",
    "D_GovernmentPromoter_Context",
    "D_ForeignCompanies_Context",
    "D_Institutions_Context",
}


def month_spans(start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        nxt = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
        yield d, min(nxt - timedelta(days=1), end)
        d = nxt


def save(obj, name):
    df = obj if isinstance(obj, pd.DataFrame) else pd.DataFrame(obj)
    os.makedirs(RAW_DIR, exist_ok=True)
    path = os.path.join(RAW_DIR, f"{name}.parquet")
    df = df.astype({c: "string" for c in df.columns[df.dtypes == "object"]}, errors="ignore")
    df.to_parquet(path, index=False)
    print(f"  -> {name}: {len(df):,} rows x {df.shape[1]} cols", flush=True)
    return df


def parallel(fn, items, workers, label):
    out, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for f in as_completed(futs):
            try:
                r = f.result()
                if r:
                    out.extend(r)
            except Exception as e:
                print(f"    ! {label} {futs[f]}: {e}", flush=True)
            done += 1
            if done % 100 == 0:
                print(f"    {label} {done}/{len(items)} rows={len(out):,}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None

    def want(step):
        return only is None or step in only

    c = NSEClient(CACHE_DIR, min_interval=0.35)
    os.makedirs(RAW_DIR, exist_ok=True)

    # ---- 1. universe + IPO history ----
    if want("universe"):
        print("[universe] equity master + IPO history", flush=True)
        eq = c.equity_list()
        save(eq, "equity_master")
        save(c.past_ipos(), "ipos")

    eqm = pd.read_parquet(f"{RAW_DIR}/equity_master.parquet")
    symbols = sorted(eqm.loc[eqm["SERIES"].astype(str).str.strip() == "EQ", "SYMBOL"].dropna().unique())
    print(f"    universe: {len(symbols)} EQ symbols", flush=True)

    # ---- 2. bulk & block deals (CSV: the JSON endpoint truncates at 70) ----
    if want("deals"):
        spans = list(month_spans(DEALS_START, END))
        for kind in ("bulk_deals", "block_deals"):
            print(f"[deals] {kind} ({len(spans)} months)", flush=True)
            frames = []

            def grab(sp, kind=kind):
                df = c.deals_csv(kind, sp[0], sp[1])
                return [df] if len(df) else []

            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                for r in ex.map(grab, spans):
                    frames.extend(r)
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            df = df.drop_duplicates()
            save(df, kind)

    # ---- 3. shareholding patterns, per symbol ----
    if want("shp"):
        print(f"[shp] per-symbol shareholding for {len(symbols)} symbols", flush=True)
        def grab(sym):
            r = c.shareholding_symbol(sym)
            for x in r:
                x["symbol"] = x.get("symbol") or sym
                x["_query_symbol"] = sym
            return r
        rows = parallel(grab, symbols, args.workers, "shp")
        save(rows, "shareholding")

    # ---- 4. promoter registry from SHP XBRL (person-level entity graph) ----
    if want("xbrl"):
        shp = pd.read_parquet(f"{RAW_DIR}/shareholding.parquet")
        shp["_dt"] = pd.to_datetime(shp["date"], format="%d-%b-%Y", errors="coerce")
        shp = shp.dropna(subset=["_dt"])
        # earliest retained filing per symbol -> promoter identity known from
        # that filing's broadcast date, so it is PIT-valid for the whole panel
        first = shp.sort_values("_dt").groupby("_query_symbol", as_index=False).head(1)
        first = first[first["xbrl"].notna() & (first["xbrl"].astype(str).str.startswith("http"))]
        print(f"[xbrl] promoter names from {len(first)} earliest SHP filings", flush=True)
        recs = first[["_query_symbol", "xbrl", "broadcastDate", "date"]].to_dict("records")

        def grab(rec):
            names = c.shp_xbrl_names(rec["xbrl"])
            out = []
            for n in names:
                out.append(
                    {
                        "symbol": rec["_query_symbol"],
                        "name": n["name"],
                        "context": n["context"],
                        "is_promoter_ctx": n["context"] in PROMOTER_CONTEXTS,
                        "known_from": rec["broadcastDate"],
                        "quarter": rec["date"],
                    }
                )
            return out

        rows = parallel(grab, recs, args.workers, "xbrl")
        save(rows, "promoter_names")

    # ---- 5. SAST Reg 29 + pledge ----
    if want("sast"):
        spans = list(month_spans(date(2018, 1, 1), END))
        print(f"[sast] Reg 29 ({len(spans)} months)", flush=True)
        rows = parallel(lambda sp: c.sast_reg29(sp[0], sp[1]), spans, args.workers, "sast")
        save(rows, "sast_reg29")

        print(f"[pledge] Reg 31 ({len(spans)} months)", flush=True)
        rows = parallel(lambda sp: c.pledge(sp[0], sp[1]), spans, args.workers, "pledge")
        save(rows, "pledge")

    # ---- 6. announcements: trading plans + deal subjects ----
    if want("ann"):
        spans = list(month_spans(ANN_START, END))
        jobs = [(s, sp) for s in TRADING_PLAN_SUBJECTS + DEAL_SUBJECTS for sp in spans]
        print(f"[ann] {len(jobs)} subject-months", flush=True)

        def grab(job):
            subj, sp = job
            r = c.announcements(sp[0], sp[1], subject=subj) or []
            for x in r:
                x["_subject_query"] = subj
            return r

        rows = parallel(grab, jobs, args.workers, "ann")
        save(rows, "announcements")

    # ---- 7. insider PIT Reg 7(2) per symbol (recent window only) ----
    if want("pit"):
        print(f"[pit] insider disclosures for {len(symbols)} symbols", flush=True)
        rows = parallel(lambda s: c.insider_pit(s), symbols, args.workers, "pit")
        save(rows, "insider_pit")

    print("INGEST COMPLETE", flush=True)


if __name__ == "__main__":
    main()
