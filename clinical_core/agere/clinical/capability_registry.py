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
                        'after discharge, time from diagnosis to an event or lab threshold (e.g. eGFR < 30 within 2 years); Kaplan-Meier by group, log-rank, Cox hazard ratios.'},
    'vbc': {'population': 'optional', 'view': 'table',
            'use': 'Value-based care: member months, attribution, utilization per 1,000, PMPM cost, risk score, quality measures, shared-savings settlement and its risk (VaR), fair (Shapley) split of savings, cost change decomposition.'},
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

PRODUCT['early_signal'] = {'population': 'optional', 'view': 'signal',
                           'use': 'Early warning: WHICH PATIENTS show the multichannel pattern that usually PRECEDES an admission or ED visit (pre-admission signature, '
                                  'heading toward decompensation), by matched filtering against templates learned from past events. Not one lab trend (trajectory), '
                                  'not time-until curves (survival), not a capacity-limited call list (outreach).'}

PRODUCT['claims_review'] = {'population': 'optional', 'view': 'pareto',
                            'use': 'Payment integrity and revenue leakage in DOLLARS: which providers bill unusually vs peers, suspicious billing patterns, '
                                   'where we leak money (denial write-offs, underpayments, take-backs, rework), which claims were underpaid vs comparable claims, '
                                   'denial patterns by payer and reason; review flags ranked by expected dollars. '
                                   'Is-it-out-of-control charts, denial-rate shifts over time and feed health are monitor.'}

PRODUCT['risk_adjustment'] = {'population': 'optional', 'view': 'raf',
                              'use': 'CMS-HCC risk adjustment (Medicare Advantage / ACO REACH): RAF and HCCs per patient (V24, V28, payment-year blend), '
                                     'coding gaps, HCC recapture (coded last year, not yet this year), suspect conditions without a diagnosis code '
                                     '(documentation gaps), coding completeness, RAF / risk score by practice, PCP or payer, dollars at stake. '
                                     'An internal (non-HCC) risk score is vbc.'}

# Questions a SQL query over the semantic tables answers exactly are handed to the verified SQL ladder.
LITERAL = ('Literal data questions: counts, lists, sums, averages, maxima, record filters, date arithmetic, simple breakdowns '
           'by a field, shares, rates per 1,000 without case-mix adjustment.')


def operations():
    return list(PRODUCT)
