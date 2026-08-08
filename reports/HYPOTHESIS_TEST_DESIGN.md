# How to test H3, H4, H6, H7, H8, H9

Companion to `RESULTS.md`. Written after H1 and H2 both failed to survive
contact with a proper control, which changes how these should be approached.

---

## 0. The lesson that governs all six designs

The raw H1 test returned **1.51× lift at p = 4.7e-08**. Highly significant,
right sign, matched the framework's "High" prior. It was wrong. Restricting to
the recent-IPO cohort — the obvious confound, since lock-ins expire 6 and 18
months after listing — the lift went to **0.86**.

So the expensive failure mode is not "we couldn't test it". It is:

> run an uncontrolled test → get a confirming number → buy the data subscription
> and build the feature on the strength of it.

Every design below is therefore specified as **label + treatment + the control
that could falsify it**. If you cannot name the control, the test isn't ready to
run. For each hypothesis I've named the specific confound that will produce a
false positive, because in all six cases there is an obvious one.

A second discipline: §10 lists ten hypotheses. Testing ten at α=0.05 gives a
~40% chance of at least one false positive. **Pre-register the tests and read
the Bonferroni column** in the power table.

---

## 1. Can these even be detected? (power analysis)

Run `python3 -m liquidity.power`. Minimum detectable effect is the smallest
**relative lift** over base rate detectable at 80% power. Inputs are estimates
of what a properly built register would contain — override them in
`liquidity/power.py` and re-run before committing any spend.

| Hypothesis | Track | Panel rows | Events | Base rate | Treated n | MDE @α=.05 | MDE Bonferroni |
|---|---|---:|---:|---:|---:|---:|---:|
| H3 pre-IPO conjunction → DRHP in 24m | A | 200,000 | 1,500 | 0.75% | 4,000 | **1.55×** | 1.72× |
| H4 fund vintage 8+ yrs → Track C | C | 60,000 | 1,200 | 2.0% | 10,800 | **1.22×** | 1.28× |
| H6 margin uplift → Track B sale | B | 200,000 | 80 | 0.04% | 20,000 | **2.23×** | 2.62× |
| H7 ESOP buyback → founder secondary | C | 30,000 | 600 | 2.0% | 300 | **2.31×** | 2.70× |
| H9 promoter 58+ no next-gen → B/E | B/E | 30,000 | 300 | 1.0% | 10,500 | **1.36×** | 1.48× |
| H8 prior liquidity → repeat *(measured)* | D | 100,630 | 18,347 | 14.8% | 46,801 | 1.04× | 1.06× |

**What this says before you spend a rupee:**

- **H4 and H9 are well powered** (1.2–1.4×). Their problem is confounding, not sample size.
- **H3 is adequately powered** *if* the effect is as large as the framework claims ("far above base rate" should mean ≥3×). It cannot detect a subtle effect.
- **H6 and H7 are underpowered for any plausible effect.** H6 needs a 2.2× lift to register on ~80 events; the framework itself rates it "Medium" and flags confounding. Spending on H6 as a standalone test is buying a coin flip.

---

## 2. H4 — lead-investor fund vintage 8+ years raises Track-C hazard

**Run this one first.** Cleanest treatment, best power, and there is a design
that eliminates the main confound outright.

| | |
|---|---|
| **Label** | Founder / early-investor secondary, cash-dated. Tracxn / Venture Intelligence / VCCEdge deal records, plus FC-TRS filings where accessible, plus trade press (VCCircle, Entrackr, The Arc). |
| **Treatment** | Age of the lead investor's **fund** (not the firm) at the observation month. Domestic funds: **SEBI's AIF register is public** and gives registration date = vintage start — this is free. Offshore funds (Cayman/Singapore/Delaware vehicles) need Preqin or fund-formation data. |
| **Confound** | Old funds hold old companies. Old companies are more mature, more likely to have any liquidity event. A naive test will confirm and be measuring company maturity. |
| **Control** | **Within-company design.** Unit of analysis = investor × company × month; outcome = "*this* investor sold". Conditional logit with **company fixed effects**. Because a company typically has investors of several vintages, this asks: among investors in the *same* company at the *same* time, does the one whose fund is older sell first? Company maturity, sector, round timing and market regime all difference out. |
| **Secondary control** | The hypothesis says "*independent of company performance*". Add last-round valuation growth and months-since-last-round as covariates; the coefficient should survive. |
| **Weak link** | The **label**, not the treatment. Much of Track C is undisclosed. Measured follow-through will be a lower bound, exactly as H2 was. |

The within-company design is what H1 needed and didn't have. It is available
here because of the multi-investor structure — use it.

---

## 3. H3 — Pvt→Public + auditor upgrade + IPO-experienced CFO → DRHP within 24m

| | |
|---|---|
| **Label** | DRHP filing date (SEBI draft offer document listings + exchange SME DRHP lists — public). For the cash-dated version, allotment date for those that list. |
| **Treatment** | The **conjunction** of three markers within a 12-month window, each stamped at its **MCA filing date**, not its event date: Pvt→Public conversion (INC-27 / company-category change), statutory auditor change to a larger firm (ADT-1, plus an audit-firm tier mapping you have to build), CFO appointment with prior listed/IPO experience (DIR-12 for the appointment; LinkedIn or annual-report bios for the experience flag). |
| **Confound 1 — survivorship** | §8.2 names it: take DRHP filers, look backwards, find ~90% converted to Public Ltd, declare victory. Conversion is *legally required* to IPO — near-100% sensitivity, near-zero specificity. **The universe must be every private company above a size threshold, including the thousands with every marker that never filed.** |
| **Confound 2 — size and growth** | Big fast-growing companies do all three of these things anyway. Condition on revenue band, revenue CAGR, sector and company age. |
| **Control** | Test the **incremental** lift of the conjunction over each marker alone — a nested model, not a single 2×2. The framework's own claim is that "each is separately near-necessary; the *conjunction* is the untested claim". So the comparator is companies that converted but had neither of the other two. If the conjunction's lift over conversion-alone is ~1, H3 is dead regardless of how impressive the raw number looks. |
| **Timing trap** | The auditor-experience and CFO-experience flags are the ones most likely to leak. A CFO's "prior IPO experience" is often only documented in the DRHP itself. Build that flag from sources dated **before** the DRHP or it is circular. |

**Data required: MCA21.** This is the gating purchase — see §7.

---

## 4. H9 — promoter age 58+ without next-gen board induction → Track B/E

| | |
|---|---|
| **Label** | Track B (private sale) or Track E (demerger, family settlement, holdco unwind). |
| **Treatment** | Promoter age ≥58 **and** no next-generation family member in a director role. Age from MCA DIN master (DOB); next-gen from DIR-12 director lists plus family relationship inference. |
| **Confound** | The framework names it: "easily confounded with company age and sector". Old promoters run old companies in old sectors, and those sell more. Age also correlates with everything — company size, leverage, listing status. |
| **Control** | **Within-family design.** Indian promoter families typically control several entities. Ask: does the same family sell the entity *without* a next-gen director while retaining the one *with*? Family fixed effects remove age, wealth, sector preference and succession culture in one move — because the promoter's age is identical across their own companies at a point in time. |
| **Secondary control** | Condition on company age and sector-year. Do **not** condition on company size, which is a collider here (size affects both next-gen involvement and sale probability). |
| **Horizon** | Succession is slow. The right outcome window is 24–36 months, which reduces the number of usable panel periods — factor that into the power calculation before running. |
| **Note** | The family-relationship inference is exactly the entity-resolution problem §8.6 warns about. The `PromoterRegistry` and name-matching in `liquidity/l0_entities.py` are directly reusable; surname clustering within a promoter group is the starting point. |

Well powered (MDE 1.36×). **The naive version will confirm and be wrong**, in
precisely the way H1 was. Only run it with family fixed effects.

---

## 5. H6 — 4–6 quarters of margin uplift with flat growth investment → Track B sale

**There is a measurement problem here that is prior to the power problem.**

Unlisted Indian companies file **annual** financials (AOC-4). There is no
quarterly series. So "**four to six consecutive quarters** of margin uplift" is
**not observable at all** for the target universe. The hypothesis as written
cannot be tested; it must be re-specified as *two consecutive years of margin
expansion with flat capex/sales*, which is a much coarser and weaker instrument.

| | |
|---|---|
| **Label** | Private M&A completion, cash-dated. MCA shareholding changes + CCI combination filings + press. |
| **Treatment** | Re-specified annually, as above. |
| **Confound** | The framework already concedes it: "likely confounded by ordinary operating improvement". Companies that improve margins are better companies; better companies attract buyers. Margin cycles are also strongly sectoral. |
| **Control** | Compare against companies with **equal** margin improvement that did not sell, within sector-year strata. |
| **Power** | MDE 2.23× (2.62× Bonferroni) on ~80 events — the framework's own estimate of clean Track-B events. |

**Recommendation: do not run this as a standalone hypothesis test.** Build the
feature, put it in the Track-B model, and let regularisation decide whether it
earns weight. An isolated test on 80 events will return "inconclusive" at
material cost, and an inconclusive result will be read as support.

---

## 6. H7 — ESOP buyback announcement → founder secondary within 6 months

**The framework already contains the answer, and the test should be designed to
confirm it cheaply rather than to measure a lift.**

§10 notes H7 is "often bundled — but the causal direction is unclear". In
practice ESOP buybacks and founder secondaries are commonly two line items in
**the same transaction, announced on the same day**. If so, this is not a
leading indicator at all — it is a *coincident* one, and a naive "does A predict
B within 6 months" test will return a spectacular hit rate with **zero lead
time**, which is commercially worthless. The framework is explicit that lead
time, not precision, is the metric that matters.

**The right test costs one analyst-week and no subscription:**

1. Collect ESOP buyback announcements from trade press (~60/yr, well covered).
2. Collect founder secondaries for the same companies.
3. Plot the distribution of `(founder secondary date − ESOP buyback date)`.

- Mass at 0 days → confirmatory, not predictive. **Hypothesis answered, discard.**
- Mass at +3 to +6 months → genuine lead; then run the full test.

Only if the second case holds is the harder question worth asking: does an ESOP
buyback with **no concurrent founder secondary** predict a *later* one? That
needs transaction-level detail separating the two components, which most sources
don't publish.

Apply the framework's own instruction for H10 — "test cheaply, discard fast" — to
H7. The lead-time histogram is the whole test.

---

## 7. H8 — already tested, and the mechanism now looks different

H8 was tested on real data: **lift 1.49, p = 2.8e-195**, monotonic in prior
event count. It dominates the fitted model. But "has this entity ever sold?"
cannot distinguish three stories with different consequences, so I ran the
diagnostics (`test_H8_mechanism`, no new data required).

### Result 1 — the signal decays, and eventually inverts

Event rate (6m) by months since last event, vs a never-sold baseline of 8.35%:

| Months since last event | n | rate | lift vs never-sold |
|---|---:|---:|---:|
| 0–3 | 5,563 | 33.3% | **3.99×** |
| 3–6 | 4,957 | 21.3% | 2.55× |
| 6–9 | 4,031 | 17.6% | 2.10× |
| 9–12 | 3,598 | 14.9% | 1.78× |
| 12–18 | 5,778 | 13.2% | 1.58× |
| 18–24 | 4,310 | 12.3% | 1.47× |
| 24–36 | 5,748 | 9.3% | 1.11× |
| 36–60 | 6,813 | 7.7% | 0.92× |
| 60+ | 6,003 | 5.4% | **0.65×** |

A holder who sold five years ago is **less** likely to sell than one who never
sold. So `ever_sold` as a standing flag is the wrong feature — it is a
**recency** signal with roughly a three-year half-life, and it crosses below
baseline at 36–60 months. The model already prefers `months_since_last_event`
(coefficient −4.70 vs −4.26); this explains why.

### Result 2 — it is not Rule-144-style dribble-out

532 repeat person-company sellers, 836 inter-event gaps: **median 101 days,
IQR [21, 346], coefficient of variation 1.56**. CV > 1 means **over-dispersed —
bursty, not regular**. The framework imports the Rule 144 dribble-out logic from
the US and notes "India has no equivalent constraint". That shows up in the
data: sellers execute in **campaigns**, then stop. There are no predictable
tranches to calendar.

### Result 3 — it is not just "nothing left to sell"

Conditioning on remaining promoter stake, the effect survives:

| Promoter stake | n | ever-sold rate | never-sold rate | lift |
|---|---:|---:|---:|---:|
| <20% | 4,270 | 16.8% | 3.9% | 4.29× |
| 20–50% | 22,630 | 15.7% | 9.4% | 1.66× |
| >50% | 62,958 | 14.1% | 8.5% | 1.67× |

So H8 is real, not a mechanical artefact of who has sellable stock.

### What remains to be done on H8

- **Within-person fixed effects**: does the *same* person's hazard rise after their first sale relative to their own pre-first-sale baseline? This is the only design that fully kills the persistent-type explanation. Feasible today.
- **Extend to Track C.** The framework's claim is about *people* ("people who have monetised once monetise again"), and the strongest version is cross-asset: does a founder who took a private secondary later sell in the listed market? That requires the private-market data and person-level linkage across both.

---

## 8. The data decision

One purchase unlocks three of the five untested hypotheses.

| Data | Unlocks | Access status |
|---|---|---|
| **MCA21** (Probe42 / Tofler / Zauba API) | **H3, H6, H9** + all of Track A stage 1–2 and Track B | `mca.gov.in` returns 403; `tofler.in` and `zaubacorp.com` block scrapers. **Needs a commercial contract** — this is a procurement task, not an engineering one. |
| **Private-market** (Tracxn / Venture Intelligence / VCCEdge) | **H4, H7** + all of Track C | Paid. `api.tracxn.com` blocked. |
| **SEBI AIF register** | H4 treatment (domestic fund vintage) | **Public and free.** Pages are JS-driven, so needs a rendered scrape rather than a plain fetch. |
| **SEBI DRHP listings** | H3 label | **Public and free.** Reachable. |
| **Licensed insider-trade feed** | Settles **H2**, and strengthens H8 | The NSE Reg 7(2) endpoint caps at ~20 records per symbol — this is the binding constraint on H2 today. |

Note the shape of this: **the labels for H3 are free; the features are not.**
You can build the Track-A event register — which §8.2 says is a standalone
commercial asset regardless of whether the model is ever built — before spending
anything on MCA.

---

## 9. Recommended sequence

1. **H8 within-person fixed effects.** Free, today, on data already collected. Confirms or kills the strongest signal in the current model.
2. **H7 lead-time histogram.** One analyst-week, press data only. Likely resolves H7 as confirmatory-not-predictive and removes it from the roadmap.
3. **Build the Track-A event register** from free SEBI/exchange DRHP listings. Establishes the base rate — which, per §8.2, determines whether Track A is viable *before* any subscription.
4. **Buy MCA21 access.** Then H3 (nested conjunction test) and H9 (within-family design).
5. **Buy private-market data.** Then H4 (within-company conditional logit) — the best-powered and cleanest of the set.
6. **Do not run H6 standalone.** Fold the feature into the Track-B model.

---

## 10. One thing to change in the register schema now

Every design above needs a **matched comparison group**, and §1.2 warns that
retrofitting the entity graph is painful. Add these fields to the event register
before it grows:

- `sector` and `size_band` — for stratified controls (H3, H6)
- `company_age` / incorporation date — the confound in H6 and H9
- `family_id` — groups companies under one promoter family, enabling the H9 within-family design
- `investor_id` + `fund_id` + `fund_vintage_date` — enabling the H4 within-company design
- `non_event` flag with the same fields populated — §8.2's requirement, and the thing whose absence killed the naive H1

The controls are cheap to apply and expensive to retrofit. Build them into the
register, not into the analysis.
