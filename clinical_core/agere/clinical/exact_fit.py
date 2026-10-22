"""Exact-fit gate on the metric router: a governed metric answers only when its definition matches the question.

The router's other declines each look for one mismatch (numbers in no slot, a subtracted component, a missing clinical
requirement, the wrong answer shape, a lost breakdown). The gate runs them as one named, receipted verdict and adds the check
that was missing: a population or time qualifier the question states (sex, age, payer, a named condition, a calendar window)
must appear in the metric's own definition or in a slot value the router filled, otherwise the metric describes a broader
population than the question asked about and the ladder falls through to a rung that can keep the restriction.

Heal-not-block: a misfit is a decline with a reason (the next rung answers); it never refuses the question.
fit() reads only the metric definition, the filled slots and the compiled node.
"""
import json
import re

VERSION = 'exact-fit/1'

# qualifier family -> (phrasings in the question, evidence a definition or slot must carry)
QUALIFIERS = {
    'sex': (r'\b(?:women|woman|females?|men|males?)\b', r'\bfemale|\bmale\b|\bsex\b|\bgender\b|\bwomen\b|\bmen\b'),
    'age': (r'\b(?:aged?|over|under|older than|younger than|at least|above|below)\s+\d{1,3}\b|\b\d{1,3}\s*\+|\b\d{1,3}\s*(?:years?|yrs?)\s*(?:old|of age)\b|'
            r'\b(?:seniors?|elderly|pediatric|children|adolescents?|geriatric)\b', r'\bage\b|\bbirth|\bdob\b|\byears? old\b|\bage_'),
    'payer': (r'\b(?:medicare advantage|medicare|medicaid|commercial|dual[- ]eligible|aca|exchange)\b', r'\bpayer|\bplan\b|\bmedicare|\bmedicaid|\bcommercial|\bcoverage\b|\bline_of_business|\blob\b'),
}
CONDITIONS = {
    'diabetes': r'\bdiabet\w*|\bdiabetics?\b', 'heart_failure': r'\bheart failure\b|\bhf\b|\bchf\b|\bhfref\b|\bhfpef\b',
    'ckd': r'\bckd\b|\bchronic kidney\b|\bkidney disease\b', 'copd': r'\bcopd\b|\bchronic obstructive\b',
    'hypertension': r'\bhypertensi\w*\b', 'asthma': r'\basthma\w*\b', 'depression': r'\bdepress\w*\b',
    'dementia': r'\bdementia\b|\balzheimer\w*\b', 'cancer': r'\bcancers?\b|\boncolog\w*\b|\bmalignan\w*\b',
    'afib': r'\batrial fibrillation\b|\bafib\b', 'obesity': r'\bobes\w*\b',
}
CONDITION_EVIDENCE = {
    'diabetes': r'diabet|e1[01]', 'heart_failure': r'heart.?failure|\bhf\b|chf|i50', 'ckd': r'\bckd\b|kidney|n18',
    'copd': r'copd|obstructive|j44', 'hypertension': r'hypertens|i10', 'asthma': r'asthma|j45', 'depression': r'depress|f3[23]',
    'dementia': r'dementia|alzheimer|f03|g30', 'cancer': r'cancer|oncolog|malignan|neoplasm', 'afib': r'fibrillation|afib|i48',
    'obesity': r'obes|e66',
}
# a window the question states; the metric must carry a period (slot, time anchor) for it to apply
WINDOW = re.compile(r'\b(?:in|during|for|over|since|between|from)\s+(?:the\s+)?(?:(?:last|past|prior|previous|trailing)\s+\d*\s*(?:days?|weeks?|months?|quarters?|years?)|'
                    r'(?:19|20)\d\d|q[1-4]\b|(?:january|february|march|april|may|june|july|august|september|october|november|december))', re.I)


def _blob(m, slots, node):
    """What the metric actually computes: its definition, the values the router chose, the compiled query. Never the slot menus."""
    parts = [m.get(k) for k in ('id', 'name', 'summary', 'population', 'denominator', 'numerator', 'event', 'value_sets', 'sql_template')]
    parts += [slots, ((node or {}).get('parameters') or {}).get('query'), ((node or {}).get('parameters') or {}).get('measure')]
    parts += [(node or {}).get('variant'), (node or {}).get('definition')]
    return json.dumps(parts, default=str).lower()


def _has_period(m, slots, node):
    if (node or {}).get('period') or (slots or {}).get('period'): return True
    anchor = (m.get('time_anchor') or {}).get('mode')
    return anchor in ('period', 'as_of', 'fixed')


def fit(question, mid, m, slots, node, as_of=None, *, existing=()):
    """-> {'version', 'metric', 'fits', 'checks': [{'check', 'ok', 'detail'}], 'reasons'}. existing: reasons the router's earlier
    checks already raised (carried into the same receipt)."""
    q = ' '.join(str(question or '').lower().split())
    blob = _blob(m or {}, slots or {}, node or {})
    checks, reasons = [], list(existing)

    def add(name, ok, detail):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
        if not ok: reasons.append(detail)
    for fam, (ask, have) in QUALIFIERS.items():
        hits = sorted({h if isinstance(h, str) else h[0] for h in re.findall(ask, q)})
        if not hits: continue
        covered = bool(re.search(have, blob))
        add(f'qualifier:{fam}', covered, f"the question restricts by {fam} ({', '.join(hits)[:60]}); metric {mid} neither defines nor takes it" if not covered
            else f'{fam} restriction carried by the metric definition or slots')
    for cond, ask in CONDITIONS.items():
        if re.search(ask, q):
            covered = bool(re.search(CONDITION_EVIDENCE[cond], blob))
            add(f'condition:{cond}', covered, f'the question is about {cond.replace("_", " ")}; metric {mid} is not defined on that population' if not covered
                else f'{cond} population carried by the metric')
    if WINDOW.search(q):
        add('window', _has_period(m or {}, slots or {}, node or {}),
            f'the question states a time window; metric {mid} has no period to apply it to')
    return {'version': VERSION, 'metric': mid, 'fits': not reasons, 'checks': checks, 'reasons': reasons}


def gate(question, mid, m, slots, node, as_of, shape_reason=None, breakdown_reason=None):
    """The router's single exact-fit verdict for one filled metric. Raises LookupError(reason) (the rung declines, the ladder
    falls through) when the definition does not match the question; returns the receipt when it does."""
    existing = [r for r in (shape_reason, breakdown_reason) if r]
    verdict = fit(question, mid, m, slots, node, as_of, existing=existing)
    if not verdict['fits']:
        raise LookupError('exact-fit gate: ' + '; '.join(verdict['reasons'])[:400])
    return verdict
