"""
Build the PDF results report.

Run: python3 make_pdf.py  ->  reports/Liquidity_Event_Model_Results.pdf
"""

from __future__ import annotations

import json
import os

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

R = "reports"
OUTPDF = f"{R}/Liquidity_Event_Model_Results.pdf"

INK = colors.HexColor("#0b0b0b")
INK2 = colors.HexColor("#52514e")
MUTED = colors.HexColor("#8a8983")
S1 = colors.HexColor("#2a78d6")
S2 = colors.HexColor("#eb6834")
RULE = colors.HexColor("#d8d7d2")
BAND = colors.HexColor("#f4f3f0")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=20,
                    leading=24, textColor=INK, alignment=TA_LEFT, spaceAfter=2)
H2 = ParagraphStyle("H2", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=13,
                    leading=16, textColor=INK, spaceBefore=14, spaceAfter=6)
H3 = ParagraphStyle("H3", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=10.5,
                    leading=13, textColor=INK, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontName="Helvetica", fontSize=9,
                      leading=12.6, textColor=INK, spaceAfter=5)
SMALL = ParagraphStyle("SMALL", parent=BODY, fontSize=7.8, leading=10.4, textColor=INK2)
LEDE = ParagraphStyle("LEDE", parent=BODY, fontSize=10, leading=14, textColor=INK2)
CELL = ParagraphStyle("CELL", parent=BODY, fontSize=7.4, leading=9.2, spaceAfter=0)
CELLB = ParagraphStyle("CELLB", parent=CELL, fontName="Helvetica-Bold")


def p(t, s=BODY):
    return Paragraph(t, s)


def table(data, widths, align=None, fontsize=7.6, header=True, zebra=True):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    st = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", fontsize),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
    ]
    if header:
        st += [
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", fontsize),
            ("TEXTCOLOR", (0, 0), (-1, 0), INK2),
            ("LINEBELOW", (0, 0), (-1, 0), 0.9, INK2),
        ]
    if zebra:
        for i in range(1 + (1 if header else 0), len(data), 2):
            st.append(("BACKGROUND", (0, i), (-1, i), BAND))
    for col, a in (align or {}).items():
        st.append(("ALIGN", (col, 0), (col, -1), a))
    t.setStyle(TableStyle(st))
    return t


def fig(path, width=170 * mm):
    from PIL import Image as PILImage
    iw, ih = PILImage.open(path).size
    return Image(path, width=width, height=width * ih / iw)


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 12 * mm,
                      "Liquidity Event Prediction Model — Track D backtest — built on NSE public data")
    canvas.drawRightString(190 * mm, 12 * mm, f"{doc.page}")
    canvas.setStrokeColor(RULE)
    canvas.line(20 * mm, 15 * mm, 190 * mm, 15 * mm)
    canvas.restoreState()


def fmt(x, n=2):
    if pd.isna(x):
        return "—"
    return f"{x:,.{n}f}"


def build():
    res = json.load(open(f"{R}/results.json"))
    story = []

    # ---------------------------------------------------------------- cover
    story += [
        p("Liquidity Event Prediction Model", H1),
        p("Track D — listed promoter &amp; insider sell-down · build and backtest results", LEDE),
        Spacer(1, 4),
        p("Built on live NSE exchange data. No simulated data anywhere in this build. "
          "Run date 7 August 2026.", SMALL),
        Spacer(1, 10),
    ]

    panel = res["panel"]
    bl = pd.DataFrame(res["baselines"])
    model_row = bl[bl["model"].astype(str).str.startswith("MODEL")].iloc[0]
    lt = pd.DataFrame(res["lead_time"])

    kpi = [
        ["Cash-dated events in register", "4,749"],
        ["Panel", f"{panel['rows']:,} entity-months · {panel['entities']:,} companies"],
        ["Precision@50 (6m horizon)", f"{model_row['p@50']:.3f}  vs {model_row['base_rate']:.3f} base rate"],
        ["Lift over base rate", f"{model_row['lift@50']:.2f}×"],
        ["Median lead time", f"{lt.iloc[0]['median_lead_months']:.1f} months"],
        ["Best baseline beaten", "Largest-100 (p@50 0.228)"],
    ]
    story.append(table([["Headline", ""]] + kpi, [70 * mm, 100 * mm], fontsize=8.4))

    story += [
        p("What this covers — and what it does not", H2),
        p("The framework specifies six event tracks. <b>This build covers Track D end to end on real "
          "data, plus the deterministic part of Track A (post-listing lock-in calendar).</b> "
          "Tracks A stage 1–2, B, C, E and F are specified in code but <b>not backtested</b>, because "
          "they need MCA21, private-market and rating-agency data that is not reachable without a "
          "commercial contract.", BODY),
        p("This matters for how the numbers should be read: the framework itself calls Track D "
          "\"the most crowded and lowest-lead-time\" track, and locates the real edge in Track A "
          "(pre-DRHP) and Track C. <b>The track that has been backtested is the one the framework "
          "rates as least differentiated.</b> That is a data-access outcome, not a judgement about "
          "where the value is.", BODY),
    ]

    # ---------------------------------------------------------------- what's done
    story += [p("1. What is built", H2)]
    done = [
        ["Layer", "Status", "What it does"],
        ["L0 Entity resolution", "Built", "50,347 promoter/insider identities across 1,957 symbols"],
        ["L1 Ingestion", "Built", "Cached, rate-limited NSE client; 7 sources"],
        ["L2 Point-in-time store", "Built", "Bitemporal: valid_from vs known_from, revision-aware"],
        ["L3 Event register", "Built", "4,749 cash-dated events, 3 independent sources"],
        ["L3 Features", "Partial", "3 of 8 framework families available from exchange data"],
        ["L4 Hazard model", "Built", "Discrete-time competing risks, multinomial logit"],
        ["L5 Wallet sizing", "Built", "FY26-27 tax config; deterministic for listed events"],
        ["L6 Ranking", "Built", "Probability vs expected-proceeds objectives"],
        ["T3 Rules engine", "Built &amp; live", "389 current alerts, each citing its regulation"],
        ["Backtest harness", "Built", "Walk-forward, 12m embargo, per-month ranking"],
    ]
    story.append(table([[p(c, CELLB) if i == 0 else p(c, CELL) for c in row]
                        for i, row in enumerate(done)],
                       [34 * mm, 22 * mm, 114 * mm]))

    story += [p("Data ingested", H3)]
    dat = [
        ["Source", "Rows", "Span"],
        ["Bulk + block deals", "157,029", "2015-01 → 2026-08"],
        ["Shareholding patterns", "36,408", "2015-12 → 2026-07"],
        ["PIT Reg 7(2) insider disclosures", "24,146", "2008-10 → 2026-08"],
        ["SAST Reg 29", "40,980", "2018 → 2026"],
        ["IPO history (lock-in calendar)", "1,346", "—"],
        ["Trading-plan disclosures", "136", "2021 → 2026"],
    ]
    story.append(table(dat, [70 * mm, 30 * mm, 50 * mm], align={1: "RIGHT"}))
    story.append(Spacer(1, 4))
    story.append(p("<b>A trap worth recording:</b> the NSE bulk/block-deal JSON endpoint silently "
                   "truncates every query to 70 rows regardless of date range. Trusting it would have "
                   "produced ~9,800 deals instead of 157,029 — a 94% loss of the label set, with no "
                   "error raised. Only <font face='Courier'>&amp;csv=true</font> returns the full set.", SMALL))

    story.append(PageBreak())

    # ---------------------------------------------------------------- model results
    story += [
        p("2. Model results", H2),
        p("Walk-forward, expanding window, with two separate leakage controls: training stops at "
          "<i>test start − horizon</i> so training labels cannot resolve inside the test period, and "
          "then a further 12-month embargo on top. Ranking is done <b>within each test month</b>, "
          "because precision@50 means \"the 50 names an RM team can work this month\".", BODY),
    ]

    bt = pd.DataFrame(res["backtest"])
    rows = [["Tier", "Horizon", "Folds", "p@25", "p@50", "p@100", "Lift@50", "AUC", "Cal. err"]]
    for _, r in bt.iterrows():
        tier, hz = r["model"].split("_")
        rows.append([tier, hz, f"{int(r['n_folds'])}", fmt(r["p@25"], 3), fmt(r["p@50"], 3),
                     fmt(r["p@100"], 3), fmt(r["lift@50"], 2), fmt(r["auc"], 3), fmt(r["cal_error"], 3)])
    story.append(table(rows, [16 * mm] * 3 + [20 * mm] * 6,
                       align={i: "RIGHT" for i in range(2, 9)}))
    story.append(Spacer(1, 4))
    story.append(p("<b>More features made it monotonically worse.</b> T1 (15 features) ≥ T2 (24) ≥ "
                   "T3 (30) at every horizon — the late confirmatory signals reserved for T3 add "
                   "nothing. The tier structure earns its keep as a governance device (T1 provably "
                   "excludes late signals), not as a performance ladder.", SMALL))

    story += [p("Against the five mandated baselines", H3)]
    story.append(fig(f"{R}/figs/baselines.png"))
    story.append(Spacer(1, 3))
    story.append(p("The model beats every baseline on hit rate. It loses decisively to "
                   "\"just call the 100 biggest companies\" on rupees captured — see section 3.", SMALL))

    story.append(PageBreak())
    story += [p("Lead time — the metric the business actually cares about", H3)]
    r2 = [["Score threshold", "Events flagged in advance", "Median lead", "IQR"]]
    for _, r in lt.iterrows():
        r2.append([r["threshold"], f"{int(r['n_events_with_prior_flag'])}",
                   f"{r['median_lead_months']:.1f} months",
                   f"{r['p25']:.1f} – {r['p75']:.1f}"])
    story.append(table(r2, [34 * mm, 46 * mm, 32 * mm, 32 * mm], align={1: "RIGHT"}))
    story.append(Spacer(1, 4))
    story.append(p("A median ~6 months of warning is commercially real — it lands inside the "
                   "framework's T2 \"active coverage, tax and estate conversations\" window.", SMALL))

    story += [p("Where it is weak", H3)]
    story.append(fig(f"{R}/figs/calibration.png", width=112 * mm))
    story.append(Spacer(1, 3))
    story.append(p("<b>The model over-predicts by roughly 70% in the top decile</b> (predicted 0.369 "
                   "vs realised 0.217). The framework is explicit that \"a well-calibrated 30% must "
                   "mean 30%, or wallet-weighted prioritisation is garbage\". Cause is regime shift "
                   "between train and test periods. <b>This needs isotonic recalibration on a "
                   "held-out fold before any production use.</b>", SMALL))

    story.append(PageBreak())

    # ---------------------------------------------------------------- ranking
    story += [
        p("3. The ranking objective was wrong by default", H2),
        p("The single most commercially consequential finding. Ranking by probability alone loses to "
          "a trivial size baseline on rupees captured. Re-ranking on expected proceeds — "
          "P(event) × E[size | event], estimated point-in-time — fixes it.", BODY),
    ]
    story.append(fig(f"{R}/figs/ranking.png", width=150 * mm))
    story.append(Spacer(1, 3))
    story.append(p("The damped version (P × √size) captures <b>4.8× the rupees</b> of probability "
                   "ranking for a ~30% reduction in hit rate, and beats the largest-100 baseline "
                   "(0.307) as well. Since RM capacity is the binding constraint and one Rs 500 cr "
                   "event is worth twenty Rs 10 cr ones, <b>this is the objective the list should "
                   "ship on.</b>", SMALL))

    # ---------------------------------------------------------------- hypotheses
    story += [p("4. Hypothesis results", H2)]
    hyp = [
        ["#", "Hypothesis", "Prior", "Verdict", "Measured"],
        ["H1", "Lock-in expiry raises P(sell in 90d)", "High", "NOT SUPPORTED",
         "1.51× raw, but 0.86× once controlled for the recent-IPO cohort (p=0.0099)"],
        ["H2", "Trading plan followed by actual selling", "High", "NOT AT THAT STRENGTH",
         "25% follow-through [12–45%], not \"the great majority\""],
        ["H8", "Prior liquidity predicts repeat events", "Medium", "SUPPORTED",
         "1.49× lift, p=2.8e-195, monotonic in prior count"],
        ["H3–H7,\nH9, H10", "Track A / B / C hypotheses", "Mixed", "NOT TESTABLE",
         "Require MCA21, private-market and rating-agency data"],
    ]
    story.append(table([[p(c, CELLB) if i == 0 else p(c, CELL) for c in row]
                        for i, row in enumerate(hyp)],
                       [14 * mm, 44 * mm, 15 * mm, 27 * mm, 70 * mm]))
    story.append(Spacer(1, 5))
    story.append(p("<b>H1 is the cautionary result.</b> The uncontrolled test returned 1.51× at "
                   "p=4.7e-08 — significant, right sign, matching its \"High\" prior. But lock-in "
                   "expiries cluster 6 and 18 months after listing, so \"lock-in just expired\" is "
                   "largely a proxy for \"recently listed\". Comparing recent IPOs with a just-expired "
                   "lock-in against other recent IPOs, the lift falls <b>below 1</b>. The mechanism is "
                   "real and the calendar is deterministic, but as a standalone ranking signal it adds "
                   "nothing over \"this company listed recently\".", SMALL))

    story.append(PageBreak())
    story += [p("H8 refined: it is recency, not type", H2)]
    story.append(fig(f"{R}/figs/h8_decay.png", width=160 * mm))
    story.append(Spacer(1, 3))
    story.append(p("A holder who sold five years ago is <b>less</b> likely to sell than one who never "
                   "sold. So the \"ever sold\" flag is the wrong feature — this is a recency signal "
                   "with roughly a three-year half-life. Inter-event gaps are also over-dispersed "
                   "(median 101 days, CV 1.56), meaning selling is <b>bursty, not regular</b>: the "
                   "Rule-144 dribble-out analogy the framework imports from the US does not transfer, "
                   "because India imposes no equivalent volume cap. There are no predictable tranches "
                   "to calendar.", SMALL))

    story.append(PageBreak())

    # ---------------------------------------------------------------- per company
    story += [
        p("5. Model result by company — backtest", H2),
        p("Every company the model scored in a held-out test month. \"Surfaced\" means the model "
          "ranked it in the top 50 that month; \"precision when surfaced\" is how often a cash event "
          "actually followed within 6 months when it did. Sorted by realised wallet captured.", BODY),
    ]
    co = pd.read_csv(f"{R}/company_backtest_results.csv")
    co = co[co["times_surfaced"].fillna(0) > 0].copy()
    co = co.sort_values("surfaced_wallet_cr", ascending=False).head(30)
    rows = [["Symbol", "Company", "Times\nsurfaced", "Hits", "Precision\nwhen surfaced",
             "Wallet captured\n(Rs cr)", "Max\nscore"]]
    for _, r in co.iterrows():
        rows.append([
            r["symbol"], str(r["company"])[:34],
            f"{int(r['times_surfaced'])}", f"{int(r['surfaced_hits'])}",
            f"{r['precision_when_surfaced']:.0%}",
            f"{r['surfaced_wallet_cr']:,.0f}", f"{r['max_score']:.2f}",
        ])
    story.append(table([[p(c, CELLB) if i == 0 else p(str(c), CELL) for c in row]
                        for i, row in enumerate(rows)],
                       [24 * mm, 48 * mm, 16 * mm, 12 * mm, 21 * mm, 26 * mm, 14 * mm],
                       align={2: "RIGHT", 3: "RIGHT", 4: "RIGHT", 5: "RIGHT", 6: "RIGHT"},
                       fontsize=7.2))
    story.append(Spacer(1, 4))
    story.append(p(f"Full table for all {len(pd.read_csv(f'{R}/company_backtest_results.csv')):,} "
                   "scored companies: <font face='Courier'>reports/company_backtest_results.csv</font>. "
                   "Month-by-month scores for every company: "
                   "<font face='Courier'>reports/company_scores_backtest.csv</font>.", SMALL))

    story.append(PageBreak())
    story += [
        p("6. Live origination ranking", H2),
        p("Model refit on all months whose labels have fully resolved, scored forward as of "
          "July 2026. Ranked on P × √size — the objective from section 3. This is the list an RM "
          "team would work.", BODY),
        p("<b>These are model outputs on public data, not recommendations, and not a claim that any "
          "named party intends to sell.</b> Handle as sensitive internal research.", SMALL),
        Spacer(1, 4),
    ]
    lv = pd.read_csv(f"{R}/company_live_ranking.csv").head(30)
    rows = [["#", "Symbol", "Company", "P(event\n6m)", "Exp. gross\n(Rs cr)",
             "Exp. addressable\nAUM (Rs cr)", "Prom.\n%", "Mths since\nlast sale"]]
    for _, r in lv.iterrows():
        rows.append([
            f"{int(r['rank_wallet_pos'])}", r["symbol"], str(r["company"])[:30],
            f"{r['score']:.3f}", f"{r['expected_gross_cr']:,.0f}",
            f"{r['expected_addressable_aum_cr']:,.0f}",
            fmt(r["promoter_pct"], 1), fmt(r["months_since_last_event"], 1),
        ])
    story.append(table([[p(c, CELLB) if i == 0 else p(str(c), CELL) for c in row]
                        for i, row in enumerate(rows)],
                       [7 * mm, 24 * mm, 42 * mm, 15 * mm, 20 * mm, 25 * mm, 15 * mm, 18 * mm],
                       align={i: "RIGHT" for i in [0, 3, 4, 5, 6, 7]}, fontsize=7.2))
    story.append(Spacer(1, 4))
    story.append(p("Full ranked list of all 2,054 companies: "
                   "<font face='Courier'>reports/company_live_ranking.csv</font>. "
                   "Deterministic T3 alerts (389 as of 7 Aug 2026, each citing the regulation that "
                   "creates its lead time): <font face='Courier'>reports/t3_alerts.csv</font>.", SMALL))

    story.append(PageBreak())

    # ---------------------------------------------------------------- caveats
    story += [
        p("7. What to fix before this is used", H2),
        p("1. <b>Recalibrate.</b> Isotonic regression on a held-out fold. The top decile "
          "over-predicts by 70%, which breaks wallet-weighted prioritisation.", BODY),
        p("2. <b>Ship P × √size ranking</b>, not probability ranking — 4.8× the rupee capture.", BODY),
        p("3. <b>Licensed insider-trade feed.</b> The NSE Reg 7(2) endpoint retains only ~20 "
          "disclosures per symbol, which is the binding constraint on settling H2.", BODY),
        p("4. <b>Parse offer documents</b> for the true lock-in term before writing off H1.", BODY),
        p("5. <b>Then buy MCA21 access</b> and build Track A — the structural-readiness signals "
          "remain completely untested, and that is where the framework argues the edge lives.", BODY),

        p("Known limitations of these results", H3),
        p("• <b>Panel window is 2021-07 to 2026-07</b> (61 months), bounded by when promoter identity "
          "becomes knowable from exchange filings — not the framework's 2015–2025 vintage split.", SMALL),
        p("• <b>The model is mostly one feature.</b> Standardised coefficients are dominated by "
          "months-since-last-event and ever-sold; everything else is an order of magnitude smaller.", SMALL),
        p("• <b>Base rates are higher than the framework assumes</b> (11.2% at 6m vs an assumed 2–5% "
          "per entity-year) because the register counts every disclosed insider sale above Rs 1 cr, "
          "including routine director sell-downs.", SMALL),
        p("• <b>Lock-in dates are assumed</b> from the standard ICDR schedule, not read from offer "
          "documents; the 18m vs 36m MPC distinction is unresolved.", SMALL),
        p("• <b>Naive vs point-in-time joins scored the same here</b> (0.307 vs 0.302). That is an "
          "honest null, not proof that PIT discipline is optional: 11.3% of entity-months get a "
          "different value under the naive join, and the shareholding feed contains 4,631 restatements "
          "with a maximum publication lag of 2,941 days. The time machine is loaded; this particular "
          "model just does not ride it.", SMALL),
        p("• <b>propensity_to_externalise is assumed at 40%, not fitted</b> — it must be calibrated "
          "from the firm's own conversion history.", SMALL),
        p("• <b>Regulatory and tax parameters were transcribed from the framework document and have "
          "not been independently re-verified</b> against primary sources.", SMALL),

        p("Compliance", H3),
        p("Every input is an exchange disclosure or regulatory filing published by NSE — public "
          "sources only, no non-public information. Scoped to client origination, not trading. "
          "The event register and promoter registry contain personal data on 50,347 identifiable "
          "individuals, so DPDP Act 2023 lawful basis, purpose limitation and retention need to be "
          "established before go-live.", SMALL),
    ]

    doc = SimpleDocTemplate(
        OUTPDF, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=20 * mm,
        title="Liquidity Event Prediction Model — Results",
        author="Track D backtest",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print("wrote", OUTPDF)


if __name__ == "__main__":
    build()
