"""Adversarial precision test for clinical/semantic_cache.py.

Bases: hand-written pairs (adversarial_pairs.json) plus non-benchmark VERIFIED questions read (mode=ro) from the live
stack's learning store. For each base the script writes near-paraphrases (should reuse) and minimal-difference
variants (changed year, number/threshold, comparator, payer, and/or, negation, unit patients/visits, concept, group-by,
month, answer shape, first/last ...; must NOT reuse). Each variant is looked up against a temporary verified store
that holds only the base, through the real lookup path and the real local encoder.

Usage: .venv/bin/python tools/learned_routing/semantic_cache_eval.py [--bases 200] [--write]
"""
import argparse, json, random, re, shutil, sqlite3, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import learning, semantic_cache as SC

LIVE = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')

SWAPS = [  # (pattern, replacement, kind) - each produces a question that asks something different
    (r'\bmedicare\b', 'Medicaid', 'payer'), (r'\bmedicaid\b', 'Medicare', 'payer'), (r'\bcommercial\b', 'Medicaid', 'payer'),
    (r'\band\b', 'or', 'and_or'), (r'\bor\b', 'and', 'and_or'),
    (r'\bpatients\b', 'visits', 'unit'), (r'\bvisits\b', 'patients', 'unit'), (r'\bencounters\b', 'patients', 'unit'), (r'\bmembers\b', 'claims', 'unit'),
    (r'\babove\b', 'below', 'comparator'), (r'\bbelow\b', 'above', 'comparator'), (r'\bat least\b', 'at most', 'comparator'),
    (r'\bmore than\b', 'fewer than', 'comparator'), (r'>=', '<=', 'comparator'), (r'(?<![<>=])>(?!=)', '<', 'comparator'), (r'\bover\b', 'under', 'comparator'),
    (r'\bwith\b', 'without', 'negation'), (r'\bwithout\b', 'with', 'negation'), (r'\bnot\b', '', 'negation'),
    (r'\bdiabetes\b', 'hypertension', 'concept'), (r'\bhypertension\b', 'diabetes', 'concept'), (r'\bheart failure\b', 'COPD', 'concept'),
    (r'\bckd\b', 'asthma', 'concept'), (r'\ba1c\b', 'LDL', 'concept'), (r'\begfr\b', 'creatinine', 'concept'), (r'\bldl\b', 'HDL', 'concept'),
    (r'\bed visits?\b', 'urgent care visits', 'concept'), (r'\binpatient\b', 'observation', 'concept'), (r'\bstatins?\b', 'beta blockers', 'concept'),
    (r'\bsglt-?2\b', 'GLP-1', 'concept'), (r'\bby practice\b', 'by PCP', 'group'), (r'\bby pcp\b', 'by practice', 'group'),
    (r'\bby payer\b', 'by sex', 'group'), (r'\bby month\b', 'by quarter', 'group'), (r'\bper month\b', 'per year', 'group'),
    (r'\bhow many\b', 'which', 'shape'), (r'\baverage\b', 'median', 'shape'), (r'\bmedian\b', 'average', 'shape'), (r'\bcount\b', 'list', 'shape'),
    (r'\bfirst\b', 'last', 'time'), (r'\blatest\b', 'earliest', 'time'), (r'\blast\b', 'next', 'time'),
    (r'\bjanuary\b', 'February', 'month'), (r'\bmarch\b', 'April', 'month'), (r'\bjune\b', 'July', 'month'),
    (r'\bdenied\b', 'paid', 'status'), (r'\bdispensed\b', 'ordered', 'status'), (r'\bno-shows?\b', 'cancellations', 'status'),
    (r'\bfemale\b', 'male', 'sex'), (r'\bwomen\b', 'men', 'sex'), (r'\bmale\b', 'female', 'sex'),
]
SYNTHETIC_BASES = [  # slot-dense bases so year / payer / month / id / threshold mutations are well represented
    'How many Medicare patients had an ED visit in 2028?', 'Total payer paid for Medicaid members in March 2029',
    'Which commercial patients with diabetes have an A1c at least 8 in 2029?', 'Average LDL for Medicare patients with CKD in 2027',
    'Number of inpatient admissions per month for Medicaid members in 2029', 'Denied claims for Medicare in June 2029 by practice',
    'How many patients aged 75 or older on a statin in 2028?', 'Percent of Medicare members with an eGFR below 45 in 2029',
    'ED visits per 1,000 Medicaid members in 2028 by PCP', 'List patients of PCP-0007 with systolic BP over 150 in January 2029',
    'Median days to payment for commercial claims in 2029', 'How many female Medicare patients had more than 2 admissions in 2028?',
    'Count of urgent care visits in Q2 2029 for Medicaid patients', 'Which patients with heart failure and CKD had a potassium above 5.5 in 2029?',
    'Share of Medicare patients with hypertension and an A1c over 9 in 2028', 'How many patients had 3 or more ED visits in the last 90 days?',
]
PARAPHRASES = [
    lambda q: 'Can you tell me ' + q[0].lower() + q[1:],
    lambda q: q.rstrip('?. ') if q.rstrip().endswith(('?', '.')) else q + '?',
    lambda q: q.lower(),
    lambda q: re.sub(r'\bpatients\b', 'pts', q, flags=re.I),
    lambda q: re.sub(r'\bhow many\b', 'what is the number of', q, flags=re.I),
    lambda q: re.sub(r'\bED visits\b', 'emergency department visits', q, flags=re.I),
    lambda q: re.sub(r'\bin (20\d\d)\b', r'during \1', q),
    lambda q: 'Please show ' + q[0].lower() + q[1:],
]


def minimal_differences(q, rng):
    out = []
    years = re.findall(r'\b20\d\d\b', q)
    if years: out.append((re.sub(years[0], str(int(years[0]) + 1), q, count=1), 'year'))
    nums = [m for m in re.finditer(r'(?<![\w.])\d+(?:\.\d+)?(?![\w])', q) if not re.fullmatch(r'20\d\d', m.group(0))]
    if nums:
        m = rng.choice(nums); v = float(m.group(0)); new = str(int(v) + 1) if v == int(v) else str(round(v + 0.5, 2))
        out.append((q[:m.start()] + new + q[m.end():], 'number'))
    for pattern, repl, kind in SWAPS:
        if re.search(pattern, q, re.I):
            changed = re.sub(pattern, repl, q, count=1, flags=re.I)
            changed = ' '.join(changed.split())
            if changed.casefold() != q.casefold(): out.append((changed, kind))
    return out


def store(tmp, bases):
    (tmp / 'run.json').write_text((LIVE / 'run.json').read_text())
    for q in bases:
        learning.record_verified(tmp, q, 'sql', {'query': 'SELECT 1'}, {}, 'hosted_verified_sql', [], 'test')


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--bases', type=int, default=200); ap.add_argument('--write', action='store_true')
    ap.add_argument('--threshold', type=float, default=SC.SIMILARITY)
    a = ap.parse_args()
    rng = random.Random(11)
    run = json.loads((LIVE / 'run.json').read_text())
    from clinical_core.local_encoders import LocalEncoder
    db = sqlite3.connect(f'file:{LIVE / "learning.sqlite3"}?mode=ro', uri=True)
    live = sorted({q for (q,) in db.execute("SELECT question FROM verified WHERE plan_kind='sql'")} )
    db.close()
    live = [q for q in live if not SC.is_benchmark(q) and len(q) < 220]
    rng.shuffle(live)
    pairs = json.loads((Path(__file__).with_name('adversarial_pairs.json')).read_text())
    cases = [{**p, 'kind': 'handwritten', 'source': 'handwritten'} for p in pairs]
    for q in SYNTHETIC_BASES + live[:a.bases]:
        for variant, kind in minimal_differences(q, rng):
            cases.append({'base': q, 'variant': variant, 'reuse': False, 'kind': kind, 'source': 'live_verified' if q in live else 'synthetic_base'})
        for f in rng.sample(PARAPHRASES, 3):
            v = f(q)
            if v != q: cases.append({'base': q, 'variant': v, 'reuse': True, 'kind': 'paraphrase', 'source': 'live_verified'})
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        encoder = LocalEncoder(run['encoder_path'], pooling='mean', cache=Path(tmp) / 'enc.sqlite3')
        by_base = {}
        for c in cases: by_base.setdefault(c['base'], []).append(c)
        for base, items in by_base.items():
            d = Path(tmp) / 'stack'
            if d.exists(): shutil.rmtree(d)
            d.mkdir(); store(d, [base])
            for c in items:
                hit, info = SC.lookup(d, c['variant'], encoder, threshold=a.threshold)
                results.append({**c, 'reused': hit is not None, 'reason': info.get('reason'), 'similarity': info.get('similarity'),
                                'same_slots': SC.same_slots(c['base'], c['variant'])})
        encoder.close()
    neg = [r for r in results if not r['reuse']]
    exact = [r for r in results if r['reuse'] and (r['reason'] or '').startswith('exact')]  # case-only variants: the exact rung answers them
    pos = [r for r in results if r['reuse'] and r not in exact]
    false_reuse = [r for r in neg if r['reused']]
    kinds = {}
    for r in neg: kinds.setdefault(r['kind'], [0, 0]); kinds[r['kind']][0] += 1; kinds[r['kind']][1] += r['reused']
    sims = sorted(r['similarity'] for r in pos if r['same_slots'] and r['similarity'] is not None)
    summary = {'threshold': a.threshold, 'minimal_difference_pairs': len(neg), 'false_reuses': len(false_reuse),
               'slot_check_alone_would_reuse': sum(r['same_slots'] for r in neg),
               'case_only_variants_left_to_exact_rung': len(exact), 'paraphrase_pairs': len(pos), 'paraphrases_reused': sum(r['reused'] for r in pos),
               'paraphrase_recall': round(sum(r['reused'] for r in pos) / max(1, len(pos)), 4),
               'paraphrase_slot_match': sum(r['same_slots'] for r in pos),
               'paraphrase_similarity_min_p5_median': [sims[0], sims[len(sims) // 20], sims[len(sims) // 2]] if sims else None,
               'by_kind_pairs_falsely_reused': {k: f'{v[1]}/{v[0]}' for k, v in sorted(kinds.items())}}
    print(json.dumps(summary, indent=1))
    for r in false_reuse: print('FALSE REUSE', r['kind'], '|', r['base'], '=>', r['variant'], r['similarity'])
    for r in neg:
        if r['same_slots'] and not r['reused']: print('  slot-equal negative (blocked by similarity)', r['kind'], '|', r['base'], '=>', r['variant'])
    missed = [r for r in pos if not r['reused']]
    for r in missed[:15]: print('  missed paraphrase', r['reason'], '|', r['base'], '=>', r['variant'])
    if a.write:
        (Path(__file__).with_name('semantic_cache_report.json')).write_text(json.dumps({'summary': summary, 'false_reuses': false_reuse, 'missed_paraphrases': [{k: r[k] for k in ('base', 'variant', 'reason', 'similarity')} for r in missed]}, indent=1))


if __name__ == '__main__':
    main()
