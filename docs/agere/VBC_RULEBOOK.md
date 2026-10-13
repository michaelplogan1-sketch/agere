# VBC program rulebook

**Last verified: 2026-10-13.** Machine-readable source of truth: `config/agere/programs/*.json` (schema `_schema.json`). Loader, validator and resolver: `clinical_core/agere/clinical/programs.py`. Validate with `python -m clinical_core.agere.clinical.programs`.

Every parameter in those files cites a public source: URL, title, section or page, the year it applies to, and the retrieval date. Government programs cite only government sources (cms.gov, ecfr.gov, federalregister.gov). Commercial and Medicaid entries are **typical published ranges, not any payer's contract**, and they never resolve to settlement terms.

## How the engines use it

- When a question names a program, `local_compiler` merges that program's terms under the terms stated in the question. Stated terms win.
- `vbc.settle` then applies the merged terms: sharing and loss rates, the MSR/MLR or the variable MSR schedule, savings and loss caps, the revenue-based loss limit, the REACH/LEAD risk corridors, the benchmark discount and the quality withhold.
- The answer's `method` names the program and year and lists the sources.
- The benchmark is always contract-specific. If it is missing, the answer lists exactly what is still needed. Example: "What would we earn under MSSP ENHANCED?" returns `benchmark_pmpm (contract-specific; MSSP ENHANCED performance year 2026 supplies sharing_rate, msr_schedule, loss_rate, savings_cap_pct, loss_cap_pct)`.
- The operator router reports the same message as `requires_input`.
- Default assumptions, which appear in every answer:
  - The quality performance standard is met (quality score 1.0).
  - Two-sided MSSP uses the variable MSR/MLR election unless the question states an MSR.
  - The REACH/LEAD quality withhold is fully earned back.
  - The MSSP revenue-based loss limit applies only when `participant_revenue` is supplied.
- MA helpers for a later risk-adjustment engine: `hcc_blend(year)`, `normalization_factor(year, model)`, `coding_intensity(year)`, `payment_risk_score({model: raw}, year)`, `qbp_bonus_pct(stars)`, `rebate_pct(stars)`.

## Medicare Shared Savings Program (PY2025-2026)

Sources:
- 42 CFR 425.605 BASIC: https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-425/subpart-G/section-425.605
- 42 CFR 425.610 ENHANCED: https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-425/subpart-G/section-425.610
- 42 CFR 414.1415(c)(3) for the Level E loss limit.

The eCFR version read was dated 2026-10-08 and includes the CY2026 PFS final rule.

| Track / level | Risk | Max sharing rate | Savings cap (% benchmark) | Loss rate | Loss limit |
|---|---|---|---|---|---|
| BASIC A | one-sided | 40% | 10% | n/a | n/a |
| BASIC B | one-sided | 40% | 10% | n/a | n/a |
| BASIC C | two-sided | 50% | 10% | 30% | 2% of participant revenue, capped at 1% of benchmark |
| BASIC D | two-sided | 50% | 10% | 30% | 4% of revenue, capped at 2% of benchmark |
| BASIC E (Advanced APM) | two-sided | 50% | 10% | 30% | 8% of revenue, capped at 4% of benchmark |
| ENHANCED (Advanced APM) | two-sided | 75% | 20% | 1 − 0.75 × quality score, within 40%–75% | 15% of benchmark |

**MSR/MLR**
- One-sided levels use a variable MSR by number of assigned beneficiaries, linearly interpolated within each band:
  - 1–499: 12.2%
  - 500–999: 12.2% → 8.7%
  - 1,000–2,999: 8.7% → 5.0%
  - 3,000–4,999: 5.0% → 3.9%
  - 5,000–5,999: 3.9% → 3.6%
  - … (bands continue in the JSON)
  - 60,000+: 2.0%
  - Source: CMS MSSP Shared Savings and Losses Methodology Specifications v11, Table 5, p.44.
- Two-sided ACOs elect a symmetric MSR/MLR: 0%, 0.5%, 1.0%, 1.5%, 2.0%, or variable (425.605(b), 425.610(b)).
- A fixed election reverts to the variable MSR/MLR in any year with fewer than 5,000 assigned beneficiaries (425.110(b)(3)(iii)).

**Quality (425.512)**
- Standard for PY2025–26: report APP Plus and score at or above the 40th percentile of MIPS Quality scores. The PY2025 value is 76.70.
- An eCQM reporting incentive applies in PY2025–26.
- Alternative standard: at or above the 10th percentile on at least one outcome measure. Savings then follow a sliding scale (sharing rate × quality score).
- APP Plus measures:
  - 2025: 321, 479, 001, 134, 236, 112.
  - 2026: adds 113.
  - 2027: adds 305.
  - Source: CY2026 PFS final-rule fact sheet.
- The health equity adjustment to the quality score is removed starting PY2026.

**Assignment (425.400/402)**
- Prospective, or preliminary prospective with retrospective reconciliation.
- Step 1: primary care practitioners. Step 2: specialists. Step 3 (from PY2025): an expanded 24-month window for non-physician ACO professionals.
- Voluntary alignment applies.

**Benchmark (425.652/656/658, 425.605(a))**
- Regional adjustment is capped at +5% and −1.5% of national per-capita expenditure.
- The ACPT carries one-third weight in the update factor.
- Risk-score growth is capped at demographic growth + 3 points (agreements from 2024).
- Prior savings adjustment: the lesser of 50% of savings or 5% of national per-capita expenditure.
- The CMS-HCC model follows the MA phase-in.

**Advance investment payments (425.630)**
- One-time payment of $250,000.
- Quarterly payments of $0–$45 per beneficiary by risk-factor score, for up to 8 quarters and capped at 10,000 beneficiaries.
- Recouped from shared savings.

**Prepaid shared savings (425.640)** covers renewing ACOs from 2026.

**Rule changes**
- CY2026 final rule: from 2027 starts, at most one glide-path agreement period (5 one-sided years).
- CY2027 PFS proposed rule (July 2026, *proposed only*, not compiled):
  - Level E sharing rate 50% → 60%.
  - ACPT guardrails.
  - Prior-savings scaling 50% → 75%.
  - AIP flat $45/$25 from PY2028.

## ACO REACH (PY2025-2026; model ends 2026-12-31)

Sources:
- PY2026 Financial Operating Guide Overview Rev. 1.1: https://www.cms.gov/priorities/innovation/files/aco-reach-py26-financial-ops-guide-ovw.pdf
- PY2025 guide Rev. 1.2: https://www.cms.gov/files/document/py25-reach-fin-op-guide.pdf

| Option | First-corridor share | Benchmark discount | Risk corridors (marginal share by savings/loss % of benchmark) | Quality withhold |
|---|---|---|---|---|
| Professional (PY2025-26) | 50% | none | <5% 50%; 5-10% 35%; 10-15% 15%; >15% 5% | 2% (PY25), 5% (PY26) |
| Global PY2025 | 100% | 3.5% | <25% 100%; 25-35% 50%; 35-50% 25%; >50% 10% | 2% |
| Global PY2026 | 100% | 4.0% | <10% 100%; 10-35% 50%; 35-50% 25%; >50% 10% | 5% |

Other terms:
- **Capitation:** Professional must take PCC (APO optional). Global may take TCC or PCC.
- **Stop-loss:** optional and residual-based.
- **Population (health equity) adjustment, per beneficiary per month:**
  - ≥P90: +$30
  - P80–90: +$20
  - P70–80: +$10
  - P30–70: $0
  - <P30: −$10
  - CDI replaces ADI in PY2026.
- **Other PY2026 changes:** 60/40 historical/regional blend, an extra 3% risk-growth cap, and V28 at 100%.

**Successor: LEAD** (RFA: https://www.cms.gov/priorities/innovation/files/lead-rfa.pdf; status Announced)
- Runs 2027-01-01 to 2036-12-31.
- No MSR or MLR.
- **Professional:** 50%, no discount. Corridors ≤10% 50%; 10–15% 35%; 15–20% 15%; >20% 5%.
- **Global:** 100%. Corridors ≤15% 100%; 15–35% 50%; 35–50% 25%; >50% 10%.
  - Discount is 3.0% for lower-spending ACOs.
  - For higher-spending ACOs it ramps from 1.75% in 2027 to 3.0% in 2032.
- 3% quality withhold.
- No rebasing for 10 years.

## Medicare Advantage

Sources: CMS Rate Announcements CY2024–2027, e.g. https://www.cms.gov/files/document/2026-announcement.pdf.

| Payment year | 2020 CMS-HCC ("V24") | 2024 CMS-HCC ("V28") | Normalization V24 / V28 | Coding pattern adjustment |
|---|---|---|---|---|
| 2024 | 67% | 33% | 1.146 / 1.015 | 5.9% |
| 2025 | 33% | 67% | 1.153 / 1.045 | 5.9% |
| 2026 | 0% | 100% | - / 1.067 | 5.9% |
| 2027 | 0% | 100% (proposed 2027 model not finalized) | - / 1.079 | 5.9% |

- The PY2027 rules exclude unlinked chart-review diagnoses (except for MA switchers) and audio-only diagnoses.
- CMS calls the models "2020 CMS-HCC" and "2024 CMS-HCC". V24/V28 is industry shorthand.

**Star Ratings**
- Weights (42 CFR 422.166(e)):
  - Improvement 5.
  - Outcome and intermediate outcome 3.
  - Patient experience/complaints 2, and access 2. Both were 4 through the 2025 Stars.
  - Process 1.
- Cut points: clustering with mean resampling, Tukey outlier deletion (since the 2024 Stars), and guardrails still in force.
- The Health Equity Index reward will not be implemented. The historical reward factor is kept (CY2027 final rule, 91 FR 17515).
- QBP: +5 percentage points at 4+ stars, doubled in qualifying counties (422.258(d)(7)).
- Rebates (422.266):
  - ≥4.5 stars: 70%.
  - 3.5–4.0 stars: 65%.
  - <3.5 stars: 50%.

## Other CMS/CMMI models (reference only, not settled)

| Model | Status (2026-10) | Key financial terms | Source |
|---|---|---|---|
| Kidney Care Choices | Active, extended to 2027-12-31; KCF ended PY2025 | CKCC Professional 50% savings/losses; Global 100%; PY2026 adds a 1% CKD-benchmark discount; CKD capitation cut 50% | KCC PY2026 quick reference |
| Primary Care First | Ended 2025-12-31 | Performance-based adjustment +50% / −10%, quarterly | PCF model page |
| Making Care Primary | Terminated early 2025-06-30 | Track 2: 50/50 prospective/FFS; Track 3: fully prospective | MCP model page |
| BPCI Advanced | Ended 2025-12-31 | Discount 2% medical / 3% surgical (from MY6); stop-gain/loss ±20% | MY6 fact sheet |
| TEAM | Active 2026-2030 | Track 1 stop-gain 10%, no stop-loss; Track 2 ±5%; Track 3 ±20%; discount 1.5% CABG/bowel, 2% LEJR/SHFFT/spinal | TEAM overview |
| GUIDE | Active to 2032-06-30 | DCMP PY2026 e.g. $157–$409 per beneficiary per month in the first 6 months; respite cap $2,625 | GUIDE payment methodology v3.0 |
| ACCESS | Active from 2026-07-05 | Annual payment eCKM $360/$180, CKM $420/$210, MSK $180, BH $180/$90; 50% withheld for reconciliation | ACCESS payment amounts |

## Commercial and Medicaid (typical ranges, not contracts)

**Commercial**
- The most common split is 50/50, with a range of 30–80% to the provider. Source: Bailit & Hughes 2011.
- A 2012–13 survey found a mean of 56% to the ACO. In that survey, 41% of private contracts were upside-only and 79% were quality-contingent. Source: Lewis et al., AJMC 2014.
- MSRs are often absent, or a flat 2–5% where present.
- 39.2% of commercial payments were in LAN categories 3–4 in CY2023. Source: HCP-LAN 2024.
- These sources are old (2011–2014); no newer public survey was found.

**Medicaid**
- The NY VBP Roadmap sets these floors:
  - Level 1: at least 40% of savings to the provider.
  - Level 2: at least 20% of losses borne by the provider, with losses capped at 3% of the target budget in year 1 and 5% from year 2.
  - Guideline table: up to 90% of savings or losses.
- 43.7% of Medicaid payments were in LAN categories 3–4 in CY2023.

**HCP-LAN categories** run from 1 to 4C. Category 3A is shared savings with upside only; 3B adds downside risk.

## Not verified from a primary source

- **MSSP:**
  - The eCFR MSR table is an image, so the values come from the CMS specifications PDF.
  - An MSSP-specific statement of the PY2025 V24/V28 blend (it is inferred from MA).
  - Advanced APM status, which comes from a cms.gov FAQ search snippet.
  - The CY2027 proposals, which come from the CMS fact sheet only.
  - APP Plus measure 484 timing.
- **ACO REACH:**
  - The Global discount for PY2023–24.
  - Stop-loss attachment points.
- **LEAD:** the application deadline and the separate finance paper.
- **KCC:** the current Global ESRD discount.
- **BPCI Advanced:** whether the MY7–8 discounts carried over.
- **TEAM:** the CMS overview deck was used; the FR and eCFR text was not checked.
- **MA:** the SSA §1853 text was not loaded (CMS quotes the 5.9% figure), and the Advance Notices were not read.
- **Medicaid states:**
  - MassHealth: an MSR/MLR of 1% or 2%, and a 70/30 split on the first 5% under Risk Track 2. Seen only in search snippets (403).
  - Minnesota IHP: a 2% MSR is unverified; quality affects 25–50% of savings.
  - Oregon CCO: 70% of payments in LAN 2C+ and 25% in 3B+ in 2024. Seen only in search snippets.
  - These are not compiled.
- **HCP-LAN:** the 2017 white paper URL returns 404, so the fact sheet was used instead.
