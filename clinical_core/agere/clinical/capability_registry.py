"""One list of Agere's analytic capabilities: every surface that chooses a tool reads it.

The operator router (any phrasing -> typed operator), the frontier planner's contracts, the console
and the acceptance suite all derive from PRODUCT. A capability counts as integrated only when it is
listed here with a typed parameter schema (graph_contracts.parameter_schema), a planner contract
(graph_planner.CONTRACTS), a console view and acceptance phrasings (bench/acceptance/<op>.json).

population: 'required' = the operator needs an input population (an empty specification means everyone);
            'optional' = it may take one; 'none' = it never does.
"""

PRODUCT = {
    'explain': {'population': 'required', 'view': 'table',
                'use': 'WHY questions: what drives / contributes to / is associated with an outcome (readmission, a condition, a lab threshold).'},
    'discover': {'population': 'required', 'view': 'table',
                 'use': 'WHERE / which groups: unexpected concentrations of an outcome across practices, PCPs, payers, age, sex, social needs (hotspots nobody specified).'},
    'trajectory': {'population': 'required', 'view': 'table',
                   'use': 'Per-patient lab or vital trends: WHICH PATIENTS are declining or will cross a threshold (Kalman forecast), change points. '
                          'Population-level "what fraction reach X within T" is survival.'},
    'survival': {'population': 'required', 'view': 'curve',
                 'use': 'Time-to-event: HOW QUICKLY / HOW LONG UNTIL / median days until / what fraction reach an event within a time: readmission or follow-up '
                        'after discharge, time from diagnosis to an event or lab threshold (e.g. eGFR < 30 within 2 years); Kaplan-Meier by group, log-rank, Cox hazard ratios. '
                        'A single observed share (e.g. what share of discharges were followed by another admission within N days) with no curve, '
                        'censoring or time-until wording is literal_lookup.'},
    'vbc': {'population': 'optional', 'view': 'table',
            'use': 'Value-based care: member months, attribution, utilization per 1,000, PMPM cost, risk score, quality measures (measure library: HEDIS/Stars/eCQM approximations such as GSD, CBP, KED, breast and colorectal screening, depression screening, FMC, TRC, PCR, AHRQ PQI 90/91/92, Part D PDC adherence, the MSSP APP Plus set, with care-gap patient lists), shared-savings settlement and its risk (VaR), fair (Shapley) split of savings, cost change decomposition.'
                   ' Settlement under a named program (MSSP BASIC A-E / ENHANCED, ACO REACH, LEAD) uses the cited program rulebook terms.'
                   ' PMPM is reported as observed allowed PMPM; the IBNR-completed figure leads only when completion or IBNR is asked.'},
    'program_effect': {'population': 'optional', 'view': 'effect',
                       'use': 'Did a program or medication start work: care management, transitions of care, community services, care coordination, starting a drug class; effect on ED visits, admissions, PMPM or a lab.'},
    'outreach': {'population': 'optional', 'view': 'worklist',
                 'use': 'Who should we call / prioritise: a capacity-limited outreach worklist ranked by expected value with reasons (care gaps, rising risk, social needs, utilization).'},
    'calibration': {'population': 'optional', 'view': 'table',
                    'use': 'How well calibrated / how accurate are our forecasts and risk scores: time-split conformal backtest coverage, Brier score, ECE, reliability tables.'},
    'monitor': {'population': 'none', 'view': 'chart',
                'use': 'Is anything out of control: SPC control charts of ED/admit rates, PMPM, lab volume, denial rate, arrival lag; data-feed breaks; claims integrity, Benford, peer outliers, duplicates.'},
    'market': {'population': 'optional', 'view': 'tickers',
               'use': 'What moved / markets view: each practice, PCP or payer as a ticker for ED and admission rates, PMPM, A1c and BP control, denial and no-show rates; '
                      'change vs prior month and baseline, trend, volatility, beta to the network, moves ranked by surprise x impact, network-wide vs one-practice moves.'},
    'demand': {'population': 'optional', 'view': 'fan',
               'use': 'Volume forecasts: ED visits, admissions, appointments, discharges or bed-days per week/month with intervals and backtest vs naive; '
                      'beds needed at a target occupancy and peak day; staffing from user-supplied ratios.'},
    'profile': {'population': 'optional', 'view': 'caterpillar',
                'use': 'Fair PCP/practice comparison: which units are truly high or low on ED use, admissions, readmissions, A1c control or PMPM after panel size and case mix (shrunk rates, credibility, rank intervals).'},
}
PRODUCT['pathway'] = {'population': 'optional', 'view': 'flow',
                      'use': 'Care pathways / patient journeys: where patients go after an ED visit, discharge or PCP visit, follow-up within N days vs ED return, leakage to outside organisations, most common paths, chance of admission within N steps or days. Use for the SEQUENCE of next steps; '
                             'for how quickly / median days until one event, use survival.'}
PRODUCT['briefing'] = {'population': 'none', 'view': 'briefing',
                       'use': 'What should I look at today / morning briefing / what needs my attention: the proactive briefing of the most important '
                              'findings across discovery, trajectories, forecasts, cost and utilization changes, outliers, monitor and market moves, '
                              'ranked by a learning-to-rank model (information x impact prior, feedback-trained) with the reason for each position.'}

PRODUCT['scheduling'] = {'population': 'optional', 'view': 'yield',
                         'use': 'Clinic scheduling / yield management: HOW MUCH TO OVERBOOK each clinic or session, how many extra slots a practice can safely book, '
                                'WHICH UPCOMING APPOINTMENTS are likely no-shows (tomorrow / next week), who should get a reminder call, access (booking lead time). '
                                'A past no-show RATE is a literal lookup, not this.'}

PRODUCT['queueing'] = {'population': 'optional', 'view': 'staffing',
                       'use': 'Queueing-theory capacity sizing (Erlang C / A / B, square-root staffing): HOW MANY nurses / providers / beds per hour (by hour of week) are needed '
                              'to keep patient waits under a target (e.g. P(wait > 30 min) <= 10%) or bed blocking under a target (e.g. <= 2%), the chance a patient waits more than N minutes, '
                              'utilization, sensitivity to +-10% arrivals. Preserves the selected population and arrival window through the snapshot; assumes a dedicated queue for that population. '
                              'Not demand (volume forecasts; staffing only from user-supplied ratios) and not scheduling (overbooking / no-shows).'}

PRODUCT['uplift'] = {'population': 'optional', 'view': 'uplift',
                     'use': 'Who BENEFITS from outreach or care management (heterogeneous treatment effects): persuadables, uplift / CATE by member, which patients to enrol because the program changes their outcome most, '
                            'RATE / AUTOC and Qini curves, benefit by decile, targeting within capacity. Not outreach (a worklist ranked by risk / expected value, no causal effect) and not program_effect '
                            '(did the program work on average, one pooled estimate).'}

PRODUCT['early_signal'] = {'population': 'optional', 'view': 'signal',
                           'use': 'Early warning: WHICH PATIENTS show the multichannel pattern that usually PRECEDES an admission or ED visit (pre-admission signature, '
                                  'heading toward decompensation), by matched filtering against templates learned from past events. Not one lab trend (trajectory), '
                                  'not time-until curves (survival), not a capacity-limited call list (outreach).'}

PRODUCT['claims_review'] = {'population': 'optional', 'view': 'pareto',
                            'use': 'Payment integrity and revenue leakage in DOLLARS: which providers bill unusually vs peers, suspicious billing patterns, '
                                   'where we leak money (denial write-offs, underpayments, take-backs, rework), which claims were underpaid vs comparable claims, '
                                   'denial patterns by payer and reason; review flags ranked by expected dollars. '
                                   'Is-it-out-of-control charts, denial-rate shifts over time and feed health are monitor. '
                                   'A plain dollar total (how much payers paid, net of reversals; total allowed or billed) is literal_lookup.'}

PRODUCT['knowledge'] = {'population': 'none', 'view': 'passages',
                        'use': 'EXPLANATION / STRATEGY questions about value-based care itself, not this population\'s data: how does X work, what is X, '
                               'explain, how are patients attributed/aligned, compare companies (Aledade vs agilon), CMS/CMMI models and strategy, '
                               'risk adjustment (HCC V28, RADV), Stars/HEDIS/APP, frameworks (Porter value agenda, Nash population health), '
                               'evidence on what works (MSSP savings, hotspotting RCT). Cited passages from a curated public-source knowledge base.'}

PRODUCT['risk_adjustment'] = {'population': 'optional', 'view': 'raf',
                              'use': 'CMS-HCC risk adjustment (Medicare Advantage / ACO REACH): RAF and HCCs per patient (V24, V28, payment-year blend), '
                                     'coding gaps, HCC recapture (coded last year, not yet this year), suspect conditions without a diagnosis code '
                                     '(documentation gaps), coding completeness, RAF / risk score by practice, PCP or payer, dollars at stake. '
                                     'An internal (non-HCC) risk score is vbc.'}

PRODUCT['network'] = {'population': 'optional', 'view': 'graph',
                      'use': 'Provider network (patient-sharing graph of clinicians or organisations): which specialists and hospitals our patients actually use, '
                             'key referral hubs (centrality), provider communities, which PCPs share the most patients, care-team cohesion (care density), '
                             'referral concentration (HHI), care leaking out of the network by specialty or practice (visits and dollars), steering '
                             'simulations (if we steer cardiology to practice X, how much leakage do we recover?) and how a practice-pattern change would '
                             'spread across PCPs (diffusion model). Patient journeys / next steps after an ED visit or discharge are pathway.'}

PRODUCT['contract_strategy'] = {'population': 'optional', 'view': 'options',
                                'use': 'Risk-contract STRATEGY: should we move tracks (MSSP BASIC to ENHANCED), which ACO REACH option / risk corridor is best for us, '
                                       'what our upside/downside contract is worth (option value, VaR/CVaR, sensitivities to benchmark and trend), compare '
                                       'two contract offers, what sharing rate / MSR to negotiate (Nash bargaining, break-even against care-management spend), '
                                       'how much downside risk we can take at a confidence level. The settlement amount under ONE stated contract '
                                       '("what would we earn under X") is vbc.'}

PRODUCT['target_trial'] = {'population': 'optional', 'view': 'trial',
                           'use': 'COMPARATIVE EFFECTIVENESS of two drug strategies by target trial emulation (new-user active-comparator design): '
                                  'drug A versus drug B (or high- vs moderate-intensity statins) on a lab change (eGFR decline, A1c, LDL, BP) or time to '
                                  'admission / ED visit / incident condition; explicit protocol, propensity overlap weights or matching, balance, ITT and '
                                  'per-protocol, E-value, negative control. A single drug or program start with no comparator ("did starting X reduce Y") '
                                  'is program_effect; time-to-event curves with no treatment comparison are survival.'}
PRODUCT['line_of_therapy'] = {'population': 'optional', 'view': 'lines',
                              'use': 'TREATMENT PATTERNS from fills: lines of therapy (first-, second-, third-line regimens), add-on vs switch, '
                                     'treatment sequences (Sankey), switching matrices, time on treatment, persistence / discontinuation and PDC for '
                                     'diabetes, heart-failure GDMT, hypertension, depression, CKD or rheumatoid arthritis. Time to one event after a '
                                     'start with no regimen logic is survival; journeys through care settings are pathway.'}
PRODUCT['trial_feasibility'] = {'population': 'optional', 'view': 'funnel',
                                'use': 'CLINICAL TRIAL FEASIBILITY: how many patients meet stated inclusion/exclusion criteria (conditions, labs with '
                                       'recency, medications, age, sex), the step-by-step eligibility / attrition funnel, which criteria remove the most '
                                       'patients, eligible patients by site (practice) and investigator (PCP), diversity of the eligible population by '
                                       'race, ethnicity, sex, age and language against stated goals, and expected newly eligible patients (enrolment) per '
                                       'month. A plain count with no trial framing is a literal lookup.'}
PRODUCT['loop_closure'] = {'population': 'optional', 'view': 'loops',
                           'use': 'Closing the loop (diagnostic safety): abnormal or critical results with no follow-up or acknowledgement, '
                                  'referrals never completed, report / imaging recommendations not acted on, positive screens without a diagnostic '
                                  'work-up, orders never resulted; open loops with age and owner, close rates by practice or clinician.'}

PRODUCT['diagnostic_delay'] = {'population': 'optional', 'view': 'lookback',
                               'use': 'Missed or delayed diagnoses (SPADE / look-back): how long before a CKD, diabetes, hypertension, heart-failure or '
                                      'colorectal-cancer diagnosis the signals appeared, missed-opportunity visits, patients with diagnostic signals but '
                                      'no diagnosis (review prompts), ED discharges later admitted with a missed diagnosis. HCC coding gaps are risk_adjustment.'}

PRODUCT['risk_outcomes'] = {'population': 'optional', 'view': 'funnel',
                            'use': 'CMS-style risk-standardized outcome rates (RSRR, observed-to-expected, excess readmission ratio, funnel plot) for '
                                   'readmission, ED revisits, post-discharge ED visits, length of stay or mortality by hospital, practice or PCP. '
                                   'Plain "fair comparison" of PCPs/practices is profile.'}

PRODUCT['medication_state'] = {'population': 'optional', 'view': 'table',
                               'use': 'MEDICATION STATE per patient and drug class or GDMT pillar (RASi/ARNI, evidence-based beta-blocker, MRA, SGLT2i): '
                                      'filled and current, on the list but not filled (likely not taking), documented stopped or intolerant, or never '
                                      'recorded, with evidence dates. Which HF patients are missing a GDMT pillar, who is on / not on / stopped a class.'}
PRODUCT['default_definition'] = {'population': 'optional', 'view': 'table',
                                 'use': 'NAMED STANDARD DEFINITIONS when a question states none: avoidable spend (AHRQ PQI admissions + NYU/Billings '
                                        'avoidable ED visits, in dollars), ambulatory-care-sensitive / preventable admissions, avoidable ED visits, '
                                        'open measure gaps (governed denominator minus numerator, as a patient list), medication adherence (PDC >= 0.8).'}

from . import engine_suite as _suite  # wave-1 decision engines declare their own menu entries (engine_suite.MODULES)
PRODUCT.update(_suite.product())

# Questions a SQL query over the semantic tables answers exactly are handed to the verified SQL ladder.
LITERAL = ('Literal data questions: counts, lists, sums, averages, maxima, record filters, date arithmetic, simple breakdowns '
           'by a field, shares, rates per 1,000 without case-mix adjustment, dollar totals (paid, allowed, billed, net of reversals), '
           'and single observed rates such as the share of discharges followed by a readmission within N days.')


def operations():
    return list(PRODUCT)


# Answer-shape contract per engine (answer_shape.conform reads it): the requested shapes (answer_shape.requested) an
# engine's analysis natively answers. Every engine reports its view as a table, and a "which patients" question is served
# when that table has one row per patient. A count or value question is native only where the engine's analysis IS that
# quantity (a forecast of beds, a survival fraction, trial eligibility, a quality-measure rate, member months); its table is
# then the answer's evidence. Elsewhere (a lines-of-therapy table for "how many moved to second line", a monitor table for a
# count) the engine answered a different question and the rung is declined. answer_shape never reads a scalar out of an
# engine table: it holds many numbers, and picking one would answer a question the engine was not asked.
DEFAULT_ANSWERS = ('table', 'patient_set')
NATIVE_SCALARS = {
    'survival': ('value',), 'demand': ('count', 'value'), 'scheduling': ('count', 'value'), 'queueing': ('count', 'value'), 'trial_feasibility': ('count',),
    'diagnostic_delay': ('count', 'value'), 'pathway': ('count', 'value'), 'contract_strategy': ('value',), 'network': ('value',),
    'profile': ('value',), 'risk_outcomes': ('value',), 'risk_adjustment': ('value',), 'program_effect': ('value',),
    'market': ('value',), 'monitor': ('value',), 'claims_review': ('value',), 'vbc': ('count', 'value'), 'calibration': ('value',),
    'line_of_therapy': ('value',), 'target_trial': ('value',), 'loop_closure': ('count', 'value'), 'explain': ('value',),
    'medication_state': ('count',), 'default_definition': ('count', 'value'),
}
NATIVE_SCALARS.update(_suite.native_scalars())

from .foundation_engines import products as _foundation_products, native_scalars as _foundation_scalars
PRODUCT.update(_foundation_products())  # data-foundation engines (foundation_engines.py)
NATIVE_SCALARS.update(_foundation_scalars())


def answers(operation):
    """Requested shapes an engine natively answers; None for operations that are not engines (sql, aggregate, cohorts)."""
    if operation not in PRODUCT: return None
    return DEFAULT_ANSWERS + NATIVE_SCALARS.get(operation, ())
