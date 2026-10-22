"""Label-choice audit and disclosure for every risk score and worklist (Obermeyer et al., Science 2019).

A risk score is only as fair as the label it was trained to predict. A score fitted to COST ranks sicker patients lower wherever
the system spends less on them. Every operation that scores or ranks patients is registered here with the label it predicts,
the kind of label it is, and what that does and does not mean:
  disclosure(op)   -> what the score predicts, in one sentence, and whether the label is a spending proxy for clinical need
  annotate(...)    -> answer_checks hook: any answer produced by a registered operation states its label (answer['label_choice'],
                      a limitation line, and a warning when the label is cost used as a proxy for need)
  need_gap(rows)   -> the data audit: at equal score, does one group carry more clinical need (chronic burden) than another?
  audit_scores(db, scores) -> need_gap on the warehouse (chronic-condition count by sex and race) for a stack's score table
The audit never edits a score or removes a patient; it states the label and measures the gap (heal-not-block).
"""
import statistics

VERSION = 'label-choice-audit/1'
COST, UTIL, NEED, ENGAGE, DOC, PAY = 'cost', 'utilization', 'clinical_outcome', 'engagement', 'documentation', 'payment'

# op -> (score, what it is trained/defined to predict, label kind). The first source of truth is the code that fits the score.
REGISTRY = {
    'vbc': ('internal risk score', 'next-year allowed cost (Poisson GLM on age band, sex and chronic concepts, mean 1.0)', COST),
    'risk_adjustment': ('CMS-HCC RAF', 'expected Medicare spending for payment (prospective cost model)', PAY),
    'outreach': ('outreach priority', 'expected engagement value per contact, ranked with the internal cost-based risk score as one input', COST),
    'outreach_allocation': ('allocation priority', 'expected value per contact under a capacity limit, from the outreach scores', COST),
    'outreach_bandit': ('channel choice', 'observed patient engagement per contact by channel', ENGAGE),
    'policy_evaluation': ('policy value', 'observed patient engagement per contact', ENGAGE),
    'hcc_suspects': ('HCC suspect rank', 'RAF dollars recoverable by documentation (a coding-revenue label)', DOC),
    'uplift': ('treatment-effect rank', 'estimated benefit from the program (causal effect, not baseline risk)', NEED),
    'early_signal': ('early-signal rank', 'information-times-impact prior learned from feedback', UTIL),
    'risk_outcomes': ('risk-standardized rate', 'observed readmission/mortality-type outcomes adjusted for case mix', NEED),
    'claims_review': ('review rank', 'expected dollars on the claim', COST),
    'staffing_plan': ('demand forecast', 'visit volume', UTIL),
}
PROXY_KINDS = {COST, PAY, UTIL}
_STATEMENT = {COST: 'predicts spending, not clinical need',
              PAY: 'predicts payment, not clinical need',
              UTIL: 'predicts use of services, not clinical need',
              ENGAGE: 'predicts whether the patient responds, not how much they need the service',
              DOC: 'predicts documentation yield, not clinical need',
              NEED: 'is defined on a clinical outcome or effect'}


def disclosure(op):
    r = REGISTRY.get(op)
    if not r: return None
    score, label, kind = r
    proxy = kind in PROXY_KINDS
    text = f'{score} {_STATEMENT[kind]}: its label is {label}.'
    if proxy:
        text += (' Patients whose care was under-delivered or who cost less because of access barriers rank lower at equal need; '
                 'cost is never a stand-in for need here, so the list is a spending priority and clinical need is shown separately.')
    return {'op': op, 'score': score, 'label': label, 'label_kind': kind, 'cost_proxy_for_need': proxy, 'statement': text, 'version': VERSION}


def annotate(graph, answer, verdict):
    """answer_checks hook -> list of disclosures for the registered operations in the executed graph."""
    ops = []
    for n in (graph or {}).get('nodes') or []:
        op = n.get('operation')
        if op in REGISTRY and op not in ops: ops.append(op)
    found = [d for d in (disclosure(o) for o in ops) if d]
    if not found: return None
    verdict['label_choice'] = found
    for d in found:
        verdict['annotations'].append('Label choice: ' + d['statement'])
    return found


def need_gap(rows, bins=5, min_per_cell=15):
    """rows: [{'score', 'need', 'group'}]. -> {'bins': [{'bin', 'groups': {g: {'n', 'mean_need'}}}], 'gaps': [...], 'flagged': bool}.
    Score bins are quantiles of the whole population; a gap is a group whose mean need exceeds the reference group's (largest
    group) at the same score bin by 10% or more with both cells holding min_per_cell patients (Obermeyer 2019 Fig. 1)."""
    rows = [r for r in rows if r.get('score') is not None and r.get('need') is not None and r.get('group') not in (None, '')]
    if len(rows) < bins * min_per_cell * 2: return {'status': 'too_few', 'n': len(rows), 'bins': [], 'gaps': [], 'flagged': False}
    order = sorted(range(len(rows)), key=lambda i: rows[i]['score'])
    size = len(rows) / bins
    cells = {}
    for rank, i in enumerate(order):
        b = min(int(rank / size), bins - 1)
        cells.setdefault(b, {}).setdefault(rows[i]['group'], []).append(rows[i]['need'])
    counts = {}
    for r in rows: counts[r['group']] = counts.get(r['group'], 0) + 1
    ref = max(counts, key=counts.get)
    out_bins, gaps = [], []
    for b in range(bins):
        cell = cells.get(b, {})
        out_bins.append({'bin': b + 1, 'groups': {g: {'n': len(v), 'mean_need': round(statistics.fmean(v), 3)} for g, v in cell.items()}})
        base = cell.get(ref)
        if not base or len(base) < min_per_cell: continue
        for g, v in cell.items():
            if g == ref or len(v) < min_per_cell: continue
            ratio = statistics.fmean(v) / max(statistics.fmean(base), 1e-9)
            if ratio >= 1.10: gaps.append({'bin': b + 1, 'group': g, 'reference': ref, 'need_ratio': round(ratio, 3), 'n': len(v)})
    return {'status': 'audited', 'n': len(rows), 'reference_group': ref, 'bins': out_bins, 'gaps': gaps, 'flagged': bool(gaps), 'version': VERSION}


def audit_scores(db, scores, group_columns=('sex_reported', 'race'), limit=20000):
    """Audit {patient_id: score} against chronic-condition count (the need measure in the warehouse) by each group column."""
    from .vbc import CHRONIC
    ids = list(scores)[:limit]
    if not ids: return {}
    marks = ','.join('?' * len(ids))
    need = {r[0]: r[1] for r in db.execute(
        f"SELECT patient_id, count(DISTINCT concept) FROM concept_patient WHERE kind='condition' AND concept IN ({','.join('?' * len(CHRONIC))}) "
        f'AND patient_id IN ({marks}) GROUP BY patient_id', [*CHRONIC, *ids])}
    out = {}
    for col in group_columns:
        grp = dict(db.execute(f'SELECT patient_id, {col} FROM patient WHERE patient_id IN ({marks})', ids).fetchall())
        out[col] = need_gap([{'score': scores[p], 'need': need.get(p, 0), 'group': grp.get(p)} for p in ids])
    return out
