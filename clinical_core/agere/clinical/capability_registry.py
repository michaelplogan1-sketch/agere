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
                   'use': 'Per-patient lab or vital trends and forecasts: who is declining, who will cross a threshold (Kalman forecast), change points.'},
    'survival': {'population': 'required', 'view': 'curve',
                 'use': 'Time-to-event: readmission or follow-up curves after discharge, time from diagnosis to an event or lab threshold, Kaplan-Meier by group, log-rank, Cox hazard ratios.'},
    'vbc': {'population': 'optional', 'view': 'table',
            'use': 'Value-based care: member months, attribution, utilization per 1,000, PMPM cost, risk score, quality measures, shared-savings settlement and its risk (VaR), fair (Shapley) split of savings, cost change decomposition.'},
    'program_effect': {'population': 'optional', 'view': 'effect',
                       'use': 'Did a program or medication start work: care management, transitions of care, community services, care coordination, starting a drug class; effect on ED visits, admissions, PMPM or a lab.'},
    'outreach': {'population': 'optional', 'view': 'worklist',
                 'use': 'Who should we call / prioritise: a capacity-limited outreach worklist ranked by expected value with reasons (care gaps, rising risk, social needs, utilization).'},
    'monitor': {'population': 'none', 'view': 'chart',
                'use': 'Is anything out of control: SPC control charts of ED/admit rates, PMPM, lab volume, denial rate, arrival lag; data-feed breaks; claims integrity, Benford, peer outliers, duplicates.'},
    'market': {'population': 'optional', 'view': 'tickers',
               'use': 'What moved / markets view: each practice, PCP or payer as a ticker for ED and admission rates, PMPM, A1c and BP control, denial and no-show rates; '
                      'change vs prior month and baseline, trend, volatility, beta to the network, moves ranked by surprise x impact, network-wide vs one-practice moves.'},
}

# Questions a SQL query over the semantic tables answers exactly are handed to the verified SQL ladder.
LITERAL = ('Literal data questions: counts, lists, sums, averages, maxima, record filters, date arithmetic, simple breakdowns '
           'by a field, shares, rates per 1,000 without case-mix adjustment.')


def operations():
    return list(PRODUCT)
