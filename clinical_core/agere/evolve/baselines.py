"""Baseline outreach policies, written in the same sandboxed policy language the evolution loop mutates.

- router_order        the deterministic router's order: task priority (missing-pillar count), then router position
- highest_risk_first  the simulator baseline's expected monthly admission rate, highest first
- outreach_knapsack   clinical/outreach.py's value of one contact (avoidable utilization at its stated assumptions plus
                      $600 per open GDMT pillar, combined with its overlap discount, times its base reach probability);
                      with a patient-count capacity outreach.py solves this exactly by sorting on value, which is what the
                      evaluator's rank-and-fill does. knapsack_order() runs outreach.knapsack itself (hours budgets).
- random              a fixed pseudo-random order (sine hash of the router position)
"""
from __future__ import annotations

ROUTER_ORDER = '''def prioritize(p):
    # router task priority (3 high .. 1 low), then the router's own order
    return 10.0 * p['task_priority'] - p['router_rank']
'''

HIGHEST_RISK = '''def prioritize(p):
    return p['monthly_admission_rate']
'''

RANDOM = '''import math

def prioritize(p):
    u = math.sin(p['router_rank'] * 12.9898 + 78.233) * 43758.5453
    return u - math.floor(u)
'''


def outreach_code():
    """outreach.py's contact value with its constants read from the module (not copied by hand)."""
    from ..clinical import outreach
    A, R = outreach.ASSUMPTIONS, outreach.REASONS
    gap = float(R['GAP_GDMT_BB'][2])
    return f'''# Value of one outreach contact as in clinical/outreach.py (planning assumptions from that module)
ADM_COST = {float(A['admission_cost_usd'])}
ED_COST = {float(A['ed_visit_cost_usd'])}
ADM_REDUCTION = {float(A['admission_reduction_if_engaged'])}
ED_REDUCTION = {float(A['ed_reduction_if_engaged'])}
UTIL_FLOOR = {float(A['utilization_floor_usd'])}
GAP_VALUE = {gap}
DISCOUNT = 0.5
REACH = {float(A['base_reach_probability'])}

def prioritize(p):
    adm = 12.0 * p['monthly_admission_rate']
    ed = 12.0 * p['monthly_ed_rate']
    util = ADM_REDUCTION * adm * ADM_COST + ED_REDUCTION * ed * ED_COST
    values = [GAP_VALUE] * p['missing_count']
    if util >= UTIL_FLOOR:
        values.append(util)
    values = sorted(values, reverse=True)
    return REACH * sum(v * DISCOUNT ** k for k, v in enumerate(values))
'''


def all_baselines():
    return {'router_order': ROUTER_ORDER, 'highest_risk_first': HIGHEST_RISK, 'outreach_knapsack': outreach_code(), 'random': RANDOM}


def knapsack_order(values, weights, capacity):
    """outreach.knapsack's chosen set, best value first (for hours budgets; with unit weights it equals sorting)."""
    from ..clinical import outreach
    chosen, _ = outreach.knapsack(values, weights, capacity)
    return sorted(chosen, key=lambda i: (-values[i], i))
