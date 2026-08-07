# Liquidity Event Prediction Model — Build & Backtest Results

**Built from:** `liquidityeventmodelframework.md`
**Data:** real NSE exchange data, ingested live (no simulation anywhere in this build)
**Run date:** 2026-08-07
**Reproduce:** `python3 -m liquidity.ingest && python3 run_all.py`

---

## 0. Read this first — what was and was not built

The framework specifies six event tracks. **This build covers Track D end to end
on real data, plus the deterministic part of Track A (post-listing lock-in).
Tracks A stage 1–2, B, C, E and F are not backtested, because the data they
require is not reachable from this environment.**

| Track | Status | Why |
|---|---|---|
| **D — Listed block/bulk & promoter sell-down** | **Fully built and backtested** | NSE deal tape, shareholding patterns, PIT Reg 7(2), SAST, pledge all accessible |
| **A stage 3 — post-listing lock-in** | **Built** (calendar + rules engine) | IPO listing dates accessible; ICDR schedule is deterministic |
| A stage 1–2 — pre-IPO readiness | Specified, not built | Needs MCA21 (MGT-14, PAS-3, ADT-1, DIR-12). `mca.gov.in` returns 403 |
| B — Private M&A | Specified, not built | Needs MCA AOC-4 financials + rating rationales |
| C — Private secondary | Specified, not built | Needs Tracxn / VCCEdge / Venture Intelligence (blocked) |
| E — Structural | Partial (announcement subjects only) | Needs NCLT scheme filings |
| F — Distress | Not built | Needs IBBI/NCLT |

The framework says the edge lives in **A (pre-DRHP) and C**, and that **D is
"the most crowded and lowest-lead-time"** track. This build has therefore
backtested the track the framework itself rates as *least* differentiated. That
is a data-access outcome, not a judgement about where value is. Read every
number below in that light.

---

## 1. Data actually ingested

| Source | Rows | Span | Notes |
|---|---:|---|---|
| Bulk + block deals | 157,029 | 2015-01 → 2026-08 | after cross-tape dedup |
| Shareholding patterns | 36,408 | 2015-12 → 2026-07 | 1,982 symbols; **4,631 are restatements** |
| PIT Reg 7(2) insider disclosures | 24,146 | 2008-10 → 2026-08 | 1,593 symbols; person-level, categorised |
| SAST Reg 29 | 40,980 | 2018 → 2026 | |
| IPO history | 1,346 | | listing dates → lock-in calendar |
| Trading-plan disclosures | 136 | 2021 → 2026 | 46 symbols |
| Equity universe | 2,075 | | NSE EQ series |

### A trap worth recording

The NSE bulk/block-deal **JSON endpoint silently truncates every query to 70
rows**, regardless of date range. Using it would have produced ~9,800 deals
instead of 157,029 — a 94% loss of the label set, with no error and no warning.
Only `&csv=true` returns the full set. Anyone rebuilding this should verify row
counts against a known-busy month before trusting any exchange API.

---

## 2. Event register (§8.2)

**4,749 cash-dated liquidity events** from three independent sources, kept
separable so label quality can be cross-checked:

| Source | Events | Span | Gross (₹ cr) | Unit |
|---|---:|---|---:|---|
| `deal_tape` — insider SELL in bulk/block | 1,344 | 2015–2026 | 355,532 | person |
| `pit_reg7` — Reg 7(2) insider sell disclosure | 1,911 | 2015–2026 | 598,610 | person |
| `holding_drop` — promoter % fell ≥0.5pp QoQ | 1,494 | 2016–2026 | (not valued) | company |

Median deal-tape event ₹28.2 cr; p90 ₹562 cr. 992 distinct beneficiaries.

Labelled on **cash date**, not announcement date (§1.1). Non-events are included
by construction — the panel spans every listed company every month.

**Entity resolution** (§8.6) resolved 50,347 insider/promoter identities across
1,957 symbols, from two sources: shareholding-pattern XBRL (promoter group
members) and PIT Reg 7(2) (adds directors and KMP, and is genuinely
point-in-time). Spot-checking matched sellers returns Jamnalal Sons → BAJAJFINSV,
Pastel Ltd → BHARTIARTL, APL Infrastructure → APLAPOLLO, FILA → DOMS — all
correct promoter-group entities.

---

## 3. Panel and base rates

100,630 entity-months, 2,054 entities, **2021-07 → 2026-07**.

The window is bounded by promoter-identity knowability, not by deal history:
shareholding-pattern coverage only broadens to >1,400 symbols in 2021.

| Horizon | Base rate |
|---|---:|
| 3 months | 6.54% |
| 6 months | 11.24% |
| 12 months | 18.23% |

These are **well above** the framework's assumed "2–5% per entity-year" (§8.4).
The reason is that the register counts every disclosed insider sale above ₹1 cr,
including routine director sell-downs, not only the large promoter events a
private bank would work. A higher base rate makes lift harder to achieve, so
the numbers below are not flattered by it.

---

## 4. Backtest (§8.3) — walk-forward, expanding window, 12-month embargo

Two separate leakage controls: training stops at `test_start − horizon` (so
training labels cannot resolve inside the test period), and then a further
12-month embargo on top. Ranking is done **within each test month**, because
precision@50 means "the 50 names an RM team can work this month".

### Headline (6-month horizon, the T2 tier)

| Tier | folds | p@25 | p@50 | p@100 | lift@50 | AUC | cal. error |
|---|---:|---:|---:|---:|---:|---:|---:|
| T1 Radar | 3 | 0.342 | **0.312** | 0.251 | 2.83 | 0.646 | 0.063 |
| T2 Pipeline | 3 | 0.347 | 0.302 | 0.251 | 2.78 | 0.645 | 0.062 |
| T3 Imminent | 3 | 0.342 | 0.294 | 0.245 | 2.68 | 0.642 | 0.060 |

At the 3-month horizon: p@50 ≈ 0.216 (T1), lift 3.48. At 12 months: p@50 ≈ 0.46
but only one valid fold, so treat it as indicative only.

### Against the mandated baselines (§8.5), same test rows

| Model / baseline | p@25 | p@50 | p@100 | lift@50 | **wallet p@50** | AUC |
|---|---:|---:|---:|---:|---:|---:|
| **Hazard model (T2, 6m)** | **0.347** | **0.302** | **0.251** | **2.78** | 0.068 | 0.645 |
| 2 — largest 100 by size | 0.243 | 0.228 | 0.183 | 1.95 | **0.307** | 0.545 |
| 6 — promoter stake declining | 0.194 | 0.183 | 0.195 | 1.58 | 0.087 | 0.579 |
| 3 — listed in last 24m *(substitute for "Series C+")* | 0.160 | 0.177 | 0.169 | 1.56 | 0.063 | 0.547 |
| 5 — lock-in expiring in 90 days | 0.160 | 0.162 | 0.157 | 1.42 | 0.090 | 0.509 |
| 5b — promoter-class lock-in in 90 days | 0.172 | 0.157 | 0.137 | 1.39 | 0.079 | 0.516 |
| 4 — confirmatory disclosure (trading plan / OFS) | 0.151 | 0.140 | 0.125 | 1.21 | 0.052 | 0.507 |
| 1 — random | 0.105 | 0.109 | 0.118 | 0.96 | 0.036 | 0.497 |

**The model beats every baseline on hit rate.** It loses decisively to
"just call the 100 biggest companies" on rupees captured — see §6.

### Three results that should temper enthusiasm

1. **More features made it worse, monotonically.** T1 (15 features) ≥ T2 (24) ≥
   T3 (30) at every horizon. The late confirmatory signals the framework
   reserves for T3 — trading plans, OFS announcements — add nothing. On this
   data the tier structure earns its keep as a *governance* device (T1 provably
   excludes late signals), not as a performance ladder.

2. **The model is mostly one feature.** Standardised coefficients are dominated
   by `months_since_last_event` (−4.70) and `ever_sold` (−4.26); everything else
   is an order of magnitude smaller. This is H8 (repeat events), not lock-in and
   not cap-table structure. A bank could reproduce most of this with a SQL query
   over past sell-downs.

3. **Calibration is poor at the top.** Predicted 0.369 vs realised 0.217 in the
   top decile — the model over-predicts by ~70% exactly where it matters. §8.4
   is explicit that "a well-calibrated 30% must mean 30%, or wallet-weighted
   prioritisation is garbage". Cause is regime shift between train and test
   periods; the market-regime control is constant within a month and cannot
   extrapolate. **Needs isotonic recalibration on a held-out fold before any
   production use.**

### Lead-time distribution (§8.4 — "the metric the business actually cares about")

| Threshold | Events with a prior flag | Median lead | IQR |
|---|---:|---:|---|
| Top decile | 468 | **6.0 months** | 3.4 – 11.0 |
| Top 5% | 325 | 5.9 months | 3.0 – 9.3 |
| Top 1% | 137 | 6.5 months | 4.0 – 10.8 |

A median ~6 months of warning is commercially real — it lands inside the
framework's T2 "active coverage, tax/estate conversations" window.

### Embargo sensitivity

| Embargo | folds | p@50 | lift@50 | AUC |
|---:|---:|---:|---:|---:|
| 0 months | 5 | 0.350 | 2.83 | 0.653 |
| 6 months | 4 | 0.308 | 2.66 | 0.640 |
| 12 months | 3 | 0.302 | 2.78 | 0.645 |

Dropping the embargo inflates p@50 by ~16% relative. Anyone reporting results
without an embargo is quoting the 0.350, not the 0.302.

---

## 5. Point-in-time discipline (§8.1) — an honest null, and why it is still not optional

Built the same model twice: `known_from` = broadcast date (PIT) vs `known_from`
= quarter end (naive).

| Join | p@50 | lift@50 | AUC |
|---|---:|---:|---:|
| PIT (broadcast date) | 0.302 | 2.78 | 0.645 |
| **Naive (quarter end)** | **0.307** | **2.81** | **0.646** |

**The naive join did not inflate the backtest.** That is a real result and I am
not going to dress it up.

But it would be the wrong lesson to conclude PIT discipline is optional here,
and the reason is measurable. Backtest scores are a *weak instrument* for
detecting leakage: if the leaked feature carries little weight, a leaky backtest
looks identical to a clean one. So the leak was also measured directly, at the
feature level:

- **11.3%** of entity-months get a *different* promoter-% value under the naive join
- **6.6%** of entity-months get a value the naive join knows and PIT does not
- the shareholding feed contains **4,631 restatements (12.7%)**, median lag
  **102 days**, p90 **749 days**, **max 2,941 days** — a restatement broadcast
  eight years after the quarter it describes

So the time machine is fully loaded; this particular model just does not ride
it, because promoter-% features are swamped by prior-event history. In a Track-A
model built mostly *from* MCA filings — where the features **are** the filings
and the filing lag is 30–180 days — the same shortcut would be devastating.
**The PIT layer is insurance whose premium this backtest happened not to claim.**

---

## 6. The ranking objective is wrong by default (§9 / L6)

The single most commercially consequential finding. Ranking by probability
alone loses to a trivial size baseline on rupees captured. Re-ranking on
**expected proceeds = P(event) × E[size | event]** (estimated point-in-time)
fixes it:

| Ranking objective | p@25 | p@50 | wallet p@25 | wallet p@50 | wallet p@100 |
|---|---:|---:|---:|---:|---:|
| Probability only | 0.372 | 0.318 | 0.053 | 0.077 | 0.109 |
| Expected proceeds (P × size) | 0.225 | 0.195 | **0.321** | 0.345 | 0.441 |
| **P × √size** (damped) | 0.268 | 0.225 | 0.291 | **0.368** | **0.442** |

The damped version captures **4.8× the rupees** of probability ranking for a
~30% reduction in hit rate, and beats the largest-100 baseline (0.307) as well.
Since RM capacity is the binding constraint and one ₹500 cr event is worth
twenty ₹10 cr ones, **P × √size is the objective the list should ship on.**

---

## 7. Hypothesis tests (§10) — stated as the framework asked

### H1 — lock-in expiry raises P(sell within 90 days). Prior: **High**. Verdict: **not supported as an independent signal**

| Test | n | rate | 95% CI | control | lift | p |
|---|---:|---:|---|---:|---:|---:|
| Post-expiry, any class (last 90d) | 3,753 | 8.31% | [7.5, 9.2] | 6.47% | 1.29 | 1.4e-05 |
| Post-expiry, promoter-class only | 1,931 | 9.79% | [8.5, 11.2] | 6.47% | **1.51** | 4.7e-08 |
| Post-expiry (last 180d) | 5,579 | 8.80% | [8.1, 9.6] | 6.40% | 1.37 | 1.7e-11 |
| **Controlled: post-expiry within recent-IPO cohort (≤36m)** | 3,720 | 8.39% | [7.5, 9.3] | 9.77% | **0.86** | 0.0099 |

Raw, it looks like confirmation: a promoter-class lock-in expiring in the last
90 days carries 1.5× the base event rate, hugely significant.

**It does not survive the obvious control.** Lock-in expiries cluster at 6 and
18 months after listing, so "lock-in just expired" is substantially a proxy for
"recently listed" — and recently-listed companies have elevated promoter selling
for many reasons. Comparing recent IPOs *with* a just-expired lock-in against
other recent IPOs, the lift is **0.86 — significantly below 1**.

The mechanism in §4.1 is real and the calendar is genuinely deterministic. But
as a *ranking* signal on its own it adds nothing over "this company listed
recently", which is why baseline 5 (p@50 0.162) barely beats baseline 3
(p@50 0.177). Consistent with this, the fitted model gives
`lockin_expired_180d` an odds ratio of just 1.14.

*Caveat that cuts the other way:* the lock-in calendar here is **assumed** from
listing date using the standard ICDR schedule, because offer documents were not
parsed. The 18m-vs-36m MPC distinction (capex-funded issues) is unresolved, and
mis-timed expiry dates would attenuate a real effect. A build with offer-document
parsing could rescue H1.

### H2 — a disclosed trading plan is followed by actual selling "in the great majority of cases". Prior: **High**. Verdict: **not supported at that strength**

| Measure | n plans | Follow-through | 95% CI |
|---|---:|---:|---|
| Deal tape, insider-matched | 95 | 1.1% | [0.2, 5.7] |
| Deal tape, any large sale | 95 | 57.9% | [47.8, 67.3] |
| Reg 7(2), uncorrected | 95 | 20.0% | [13.2, 29.1] |
| **Reg 7(2), coverage-corrected** | **24** | **25.0%** | **[12.0, 44.9]** |

Three measurement limits, all pushing the estimate down, all stated rather than
buried:

1. The deal tape only sees trades above bulk (0.5% of equity) / block
   thresholds. Trading plans are typically filed by directors and KMP whose
   sales fall far below those — hence the near-zero deal-tape number, which is
   an artefact, not a finding.
2. The Reg 7(2) endpoint **retains only the last ~20 disclosures per symbol**.
   For an actively-traded company those records may not reach back to a given
   plan's execution window at all. Counting those as failures roughly halves
   the estimate, so plans whose window is not spanned were excluded — leaving
   only 24 usable plans, hence the wide interval.
3. Reg 7(2) itself only triggers above ₹10 lakh per quarter.

So 25% is still a **lower bound**. But even the upper end of the confidence
interval (45%) falls short of "the great majority". The framework's own logic —
plans are irrevocable and carry a mandatory 120-day clock — argues execution
should be near-certain; the observable data does not show it. Most likely
explanation is that much plan execution is small enough to be invisible in both
feeds. **This needs a licensed insider-trade feed with full history to settle;
it should not be treated as validated in either direction.**

What *is* certain and needs no statistics: 136 disclosures gave a legally-mandated
**120-day** advance warning naming the insider. That lead time is real whether or
not the sale follows.

### H8 — prior liquidity history predicts repeat events. Prior: **Medium**. Verdict: **supported**

| Test | n | rate | 95% CI | control | lift | p |
|---|---:|---:|---|---:|---:|---:|
| Any prior sell-down | 46,801 | 22.12% | [21.8, 22.5] | 14.84% | 1.49 | 2.8e-195 |
| Exactly 1 prior | 14,746 | 20.76% | [20.1, 21.4] | 14.84% | 1.40 | 5.1e-64 |
| 2–3 prior | 15,635 | 22.27% | [21.6, 22.9] | 14.84% | 1.50 | 1.2e-101 |
| 4+ prior | 16,420 | 23.21% | [22.6, 23.9] | 14.84% | 1.56 | 2.9e-131 |

Monotonic in prior count and overwhelmingly significant. This is the framework's
Rule-144 dribble-out intuition, and in this panel **it is the single strongest
entity-level signal available** — it dominates the fitted model. The framework
rated it "Medium" and rated H1/H2 "High"; on this evidence that ordering should
be inverted.

Caveat: partly mechanical. Entities that sell are entities with sellable stakes
and a disposition to sell, so this is closer to "persistent type" than
"causal trigger". It is still the most useful thing in the model.

### Not testable with accessible data

H3, H4, H5, H6, H7, H9, H10 — all require MCA21, private-market, rating-agency,
or registry data that is not reachable here. **Reported as NOT_TESTABLE rather
than estimated.** Notably this includes H3–H5, which the framework identifies as
"where genuine, defensible edge would live". The tests that could be run are the
ones the framework predicted would need the least machine learning, and one of
the two (H1) did not survive.

---

## 8. Wallet sizing (§9)

Deterministic arithmetic for listed Track D — the deal tape gives quantity ×
price, so gross proceeds are known, not modelled.

| Gross ₹ cr | Tax ₹ cr | Effective rate on proceeds | Net ₹ cr | Addressable AUM ₹ cr |
|---:|---:|---:|---:|---:|
| 10 | 1.34 | 13.4% | 8.66 | 3.46 |
| 100 | 13.45 | 13.5% | 86.55 | 34.62 |
| 500 | 67.27 | 13.5% | 432.73 | 173.09 |

All-in LTCG rate 12.5% × 1.15 surcharge cap × 1.04 cess = **14.95%**, applied to
gain (assumed 90% of proceeds), per §9. Realised register: 1,344 valued events,
median ₹28.2 cr, total **₹355,532 cr**.

`propensity_to_externalise` is set to 40% and is **not fitted** — the framework
is explicit it must be calibrated from a firm's own conversion history. It is
exposed as a parameter and flagged in every output rather than being presented
as an estimate.

---

## 9. T3 rules engine — shipped and live

Per §7.2, T3 is a **rules engine, not a model**. Every alert cites the regulation
that creates its lead time, so it is explainable to compliance without reference
to a fitted parameter.

**389 alerts as of 2026-08-07:**

| Rule | Count | Confidence |
|---|---:|---|
| Trading plan disclosed (PIT Reg 5, 120-day clock) | 41 | very high |
| Lock-in expiry — anchor 30d/90d | 87 | high |
| Lock-in expiry — non-promoter 6m | 53 | high |
| Lock-in expiry — promoter excess 6m | 53 | high |
| Lock-in expiry — AIF/VCF 6m | 53 | high |
| Lock-in expiry — MPC 18m | 28 | high |
| Promoter stake declining QoQ | 74 | medium |

This is the piece that delivers value without any of the above being true, and
it is what the framework says to ship first. Output: `reports/t3_alerts.csv`.

---

## 10. What I would do next, in order

1. **Recalibrate.** Isotonic regression on a held-out fold. The top decile is
   over-predicting by 70% and that breaks wallet-weighted prioritisation.
2. **Ship P × √size ranking**, not probability ranking. Biggest single
   commercial gain available (4.8× rupee capture).
3. **Get a licensed insider-trade feed with full history.** The 20-record
   retention cap is the binding constraint on settling H2, and H2 is the signal
   with the only legally-guaranteed lead time in the whole framework.
4. **Parse offer documents** for the true MPC lock-in term. H1 deserves one
   more, better-specified test before being written off.
5. **Then go get MCA21 access** (Probe42/Tofler) and build Track A. Everything
   above is the crowded, low-lead-time track the framework warned about. The
   structural-readiness signals in §4.1 stage 1 remain completely untested, and
   that is where the framework argues the edge actually is.

---

## 11. Compliance notes (§12)

- **Public sources only.** Every input is an exchange disclosure or a regulatory
  filing published by NSE. Nothing here uses or benefits from non-public
  information.
- **No trading use.** Scoped to client origination, per §12.
- Model outputs on listed names are predictions derived from public data; the
  T3 alert list on listed companies should still be handled as sensitive
  internal research.
- **DPDP Act 2023:** the event register and promoter registry contain personal
  data on identifiable individuals (50,347 named identities). Lawful basis,
  purpose limitation and retention need to be established before go-live, not
  after.
- Regulatory parameters are in `liquidity/config.py` as configuration, never
  hard-coded — but they were transcribed from the framework document and
  **have not been independently re-verified against primary sources** (§13).
  Do that before production.

---

## 12. Reproducibility

```
liquidity/
  config.py        regulatory + tax parameters (§13)
  sources/nse.py   cached, rate-limited NSE client
  ingest.py        L1 ingestion driver
  l0_entities.py   entity resolution, promoter/insider registry
  l1_load.py       typed loaders, assigns valid_from / known_from
  l2_pit.py        bitemporal store, as-of queries, PIT audit
  l3_events.py     event register, lock-in calendar
  l3_features.py   PIT-safe features, tier definitions
  l4_hazard.py     discrete-time competing-risks hazard
  l5_wallet.py     wallet sizing
  l6_ranking.py    ranking objectives
  backtest.py      walk-forward + embargo
  metrics.py       precision@K, lift, calibration, lead time, wallet-weighted
  baselines.py     the five mandated baselines
  hypotheses.py    H1–H10
  leakage.py       PIT vs naive, exposure measurement
  t3_rules.py      deterministic rules engine
run_all.py
```

All HTTP responses are disk-cached, so `run_all.py` is reproducible from a
frozen snapshot without re-hitting NSE.
