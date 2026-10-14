# Usage: python3 tools/notes_extraction/v22_note_precision.py <units.json> clinical_core/agere/clinical/note_facts_admission.json
"""Precision of note_facts rules/1 on the v2.2 note sample. Labels = my reading of each extracted fact in context
(patterns checked by eye with notes_ctx.py; rules below encode those judgments so every fact gets one)."""
import json, math, re, sys
from collections import Counter, defaultdict
units = json.load(open(sys.argv[1]))
admission = json.load(open(sys.argv[2]))['held_out_vs_label_model']


def wilson_low(k, n, z=1.96):
    if not n: return None
    p = k / n; d = 1 + z * z / n
    return round((p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d, 3)


def judge(u, f):
    t = u['text']; a, b = f['span_start'], f['span_end']; ev = t[a:b].lower(); before = t[max(0, a - 120):a]
    ft, v, neg = f['fact_type'], str(f['value']), f['negated']
    in_allergy = re.search(r'allergies\b:?[ \t]*\n?[^\n]*$', before, re.I) and t[a - 1:a] == '('
    if ft == 'smoking_status':
        if v == 'current': return 'current' in ev and not re.search(r'(never|former)\s*$', before, re.I), 'never/former smoker read as current'
        return True, ''
    if ft == 'symptom' and in_allergy: return False, 'allergy reaction read as current symptom'
    if ft == 'imaging_finding' and re.search(r'negative for', ev): return False, 'negated finding (negative for) read as present'
    return True, ''


tp, n, errors, by_style = Counter(), Counter(), defaultdict(Counter), defaultdict(Counter)
for u in units:
    group = 'hie' if u['stream'] == 'hie_documents' else ('problem_list_update' if u['note_type'] == 'problem_list_update' else 'signed_note')
    for f in u['facts']:
        ok, why = judge(u, f)
        n[f['fact_type']] += 1; tp[f['fact_type']] += ok
        by_style[group]['facts'] += 1; by_style[group]['tp'] += ok
        if not ok: errors[f['fact_type']][why] += 1
# recall spot checks on explicit mentions the admitted types cover
missed = Counter()
for u in units:
    t = u['text']; types = {(f['fact_type'], str(f['value'])) for f in u['facts']}
    if re.search(r'transportation barriers?(\.| (reported|positive|identified))', t, re.I) and ('social_need', 'transport') not in types: missed['social_need:transport'] += 1
    if re.search(r'housing (instability|insecurity)', t, re.I) and ('social_need', 'housing') not in types: missed['social_need:housing'] += 1
    if re.search(r'\bnever smoker\b', t, re.I) and ('smoking_status', 'never') not in types: missed['smoking_status:never'] += 1
    if re.search(r'\b(SOB|shortness of breath)\b', t) and not any(x[0] == 'dyspnea' for x in types): missed['dyspnea (incl. "no SOB")'] += 1
result = {'units': len(units), 'by_source': {k: dict(v) for k, v in by_style.items()}, 'per_fact_type': {}}
for ft in sorted(n):
    old = admission.get(ft, {})
    result['per_fact_type'][ft] = {'facts': n[ft], 'true_positive': tp[ft], 'precision': round(tp[ft] / n[ft], 3),
                                   'precision_wilson_low': wilson_low(tp[ft], n[ft]), 'held_out_precision_v20': old.get('precision'),
                                   'errors': dict(errors[ft])}
total = sum(n.values()); result['overall'] = {'facts': total, 'true_positive': sum(tp.values()), 'precision': round(sum(tp.values()) / total, 3)}
result['recall_spot_check_missed_units'] = dict(missed)
print(json.dumps(result, indent=1))
