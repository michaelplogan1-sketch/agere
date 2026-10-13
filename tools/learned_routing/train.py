"""Train the learned router (clinical/learned_router.py) from non-benchmark labels; evaluate on bench/acceptance.

Labels, in priority order:
  1. the deterministic local compiler's route for the question (PRODUCT operator / plain ops = literal_lookup /
     unsupported = not_answerable);
  2. the paid operator router's own logged decisions, recovered read-only from provider receipts;
  3. the harvest route (hosted verified SQL = literal_lookup; hosted missing data = not_answerable);
  4. the paraphrase template's intended engine (tools/learned_routing/templates.py).
Every bench question (bench/acceptance/*.json, bench/questions/*.json) and anything with token Jaccard >= 0.5 to
one is excluded from training; bench/acceptance is used only as the test set.
The live stack is opened read-only (sqlite mode=ro; the source profile is read as a file, never rebuilt).

Usage: .venv/bin/python tools/learned_routing/train.py [--stack DIR] [--write]
"""
import argparse, collections, glob, hashlib, json, os, re, sqlite3, sys, tempfile, time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from clinical_core.contracts import digest
from clinical_core.agere.clinical import learned_router as LR, operator_router as R, conformal
from clinical_core.agere.clinical.capability_registry import PRODUCT
from templates import generate

LIVE = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')
BOTTLING = Path('/Users/mikel/clinical-insights-local/artifacts/bottling')
PLAIN = {'cohort', 'aggregate', 'sql', 'union', 'intersect', 'difference'}
TARGET_ACCURACY = 0.98


def norm(q): return ' '.join(q.strip().split()).casefold().rstrip('?.!')
def tokens(t): return set(re.findall(r'[a-z0-9]+', t.lower()))


def ro(path): return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def bench_sets():
    acceptance = [c for f in sorted((ROOT / 'bench' / 'acceptance').glob('*.json')) for c in json.loads(f.read_text())]
    other = [q['question'] for f in (ROOT / 'bench' / 'questions').glob('*.json') for q in json.loads(f.read_text()) if q.get('question')]
    return acceptance, [c['question'] for c in acceptance] + other


def bench_filter(bench):
    """-> predicate: True for a bench question or anything with token Jaccard >= 0.5 to one (never training material)."""
    bench_norm = {norm(q) for q in bench}
    bench_tokens = [tokens(q) for q in bench]
    def near_bench(q):
        t = tokens(q)
        return norm(q) in bench_norm or any(len(t & b) / max(1, len(t | b)) >= 0.5 for b in bench_tokens)
    return near_bench


def profile_readonly(stack):
    from clinical_core.agere.clinical import source_profile
    run = json.loads((stack / 'run.json').read_text())
    saved = json.loads((stack / 'source-profile.json').read_text())
    if saved.get('schema') != source_profile.VERSION or saved.get('source_digest') != run['source_digest']:
        raise SystemExit('source profile is stale; refusing to rebuild it inside the read-only live stack')
    return saved, run


def compiler_label(question, profile, as_of):
    from clinical_core.agere.clinical.local_compiler import compile as compile_locally
    try:
        graph, info = compile_locally(question, profile, as_of)
    except Exception:  # noqa: BLE001 - a compiler crash is simply "no label"
        return None
    if graph is None: return None
    if graph.get('coverage') == 'unsupported': return 'not_answerable'
    ops = [n['operation'] for n in graph.get('nodes', [])]
    product = [o for o in ops if o in PRODUCT]
    if product: return product[-1]
    if ops and set(ops) <= PLAIN: return 'literal_lookup'
    return None


def recovered_router_decisions(stack, questions, as_of):
    """The paid router's choose-step answers, matched to questions by recomputing their content-addressed request keys."""
    receipts = stack / 'provider-receipts'
    names = {p.stem for p in receipts.glob('*.json') if '-' not in p.stem}
    found = {}
    for q in questions:
        body = {'model': R.MODEL, 'max_tokens': 1200, 'system': R.SYSTEM.format(as_of=as_of), 'tools': R.menu(),
                'tool_choice': {'type': 'any', 'disable_parallel_tool_use': True}, 'messages': [{'role': 'user', 'content': q}]}
        key = digest({'contract': 'context-student/1', 'provider': 'claude', 'body': body})
        if key in names:
            raw = json.loads((receipts / f'{key}.json').read_text())['response']
            calls = [b for b in raw.get('content', []) if b.get('type') == 'tool_use']
            if len(calls) == 1 and calls[0]['name'] in LR.LABELS:
                found[q] = {'label': calls[0]['name'], 'usage': raw.get('usage'), 'model': raw.get('model'),
                            'seconds': (json.loads((receipts / f'{key}.json').read_text()).get('receipt') or {}).get('seconds')}
    return found


def harvest(stack):
    rows = []
    for f in sorted(glob.glob(str(BOTTLING / '*.jsonl'))):
        for line in open(f):
            try: r = json.loads(line)
            except ValueError: continue
            if r.get('question'): rows.append({'question': r['question'], 'route': r.get('route'), 'area': r.get('area'), 'source': Path(f).name})
    db = ro(stack / 'learning.sqlite3')
    rows += [{'question': q, 'route': route, 'source': 'learning.verified'} for q, route in db.execute('SELECT question, route FROM verified')]
    rows += [{'question': q, 'route': route, 'source': 'learning.outcomes'} for q, route in db.execute('SELECT question, route FROM outcomes')]
    db.close()
    db = ro(stack / 'answers.sqlite3')
    rows += [{'question': q, 'route': route, 'source': 'answers'} for q, route in db.execute('SELECT question, route FROM answers')]
    db.close()
    return rows


ROUTE_LABEL = {'hosted_verified_sql': 'literal_lookup', 'hosted_small_model_sql': 'literal_lookup', 'learned_verified_sql': 'literal_lookup',
               'hosted_missing_data': 'not_answerable', 'local_unsupported': 'not_answerable'}


# Bottling areas whose generated questions are plain data lookups by construction (generate_questions.AREAS);
# areas that overlap an analytic engine (VBC, transitions, operations, revenue cycle, data quality) are not assumed.
LITERAL_AREAS = {'cohorts', 'medications', 'utilization', 'social_needs'}


def split_of(group):
    h = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 100
    return 'train' if h < 70 else 'cal' if h < 85 else 'heldout'


def fit_temperature(logits, y):
    best = (None, float('inf'))
    for t in np.exp(np.linspace(np.log(0.2), np.log(5), 120)):
        p = LR.softmax(logits / t)
        nll = -np.mean(np.log(np.maximum(p[np.arange(len(y)), y], 1e-12)))
        if nll < best[1]: best = (float(t), nll)
    return best[0]


def choose_threshold(maxp, correct, target=TARGET_ACCURACY, min_cases=30):
    """Smallest threshold t such that accuracy on cases with p >= t, AND on every stricter threshold, is >= target."""
    order = np.argsort(-maxp)
    p, c = maxp[order], correct[order].astype(float)
    acc = np.cumsum(c) / np.arange(1, len(c) + 1)
    ok = acc >= target
    # every prefix up to k must be fine from min_cases on: take the longest prefix whose running accuracy never drops below target
    k_best = None
    for k in range(len(p)):
        if k + 1 >= min_cases and not ok[k]: break
        if k + 1 >= min_cases and (k + 1 == len(p) or p[k + 1] < p[k]): k_best = k
    if k_best is None: return 1.01, 0.0, float('nan')
    return float(p[k_best]), (k_best + 1) / len(p), float(acc[k_best])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stack', default=str(LIVE)); ap.add_argument('--write', action='store_true')
    ap.add_argument('--C', type=float, default=4.0)
    a = ap.parse_args()
    stack = Path(a.stack)
    started = time.time()
    acceptance, bench = bench_sets()
    near_bench = bench_filter(bench)
    profile, run = profile_readonly(stack)
    as_of = run['snapshot']['as_of']

    raw = harvest(stack)
    seen, rows, sources = set(), [], collections.Counter()
    questions = sorted({r['question'] for r in raw})
    router_log = recovered_router_decisions(stack, questions + [c['question'] for c in acceptance], as_of)
    for r in raw:
        q = r['question']
        if norm(q) in seen: continue
        seen.add(norm(q))
        if near_bench(q): sources['excluded_bench_or_near'] += 1; continue
        label, how = compiler_label(q, profile, as_of), 'compiler'
        if label is None and q in router_log: label, how = router_log[q]['label'], 'router_log'
        if label is None and r.get('route') in ROUTE_LABEL: label, how = ROUTE_LABEL[r['route']], 'harvest_route'
        if label is None and r.get('area') in LITERAL_AREAS: label, how = 'literal_lookup', 'harvest_area'
        if label is None: sources['unlabelled'] += 1; continue
        sources[how] += 1
        rows.append({'question': q, 'label': label, 'how': how, 'group': 'h:' + norm(q)})
    for t in generate(per_template=8):
        q = t['question']
        if norm(q) in seen: continue
        seen.add(norm(q))
        if near_bench(q): sources['excluded_bench_or_near'] += 1; continue
        label = compiler_label(q, profile, as_of)
        how = 'compiler' if label else 'template'
        sources[how + '_template'] += 1
        rows.append({'question': q, 'label': label or t['intent'], 'how': how, 'group': 't:' + t['template']})
    print('sources', dict(sources))
    print('labels', collections.Counter(r['label'] for r in rows))

    from clinical_core.local_encoders import LocalEncoder
    with tempfile.TemporaryDirectory() as tmp:
        encoder = LocalEncoder(run['encoder_path'], pooling='mean', cache=Path(tmp) / 'encoder-cache.sqlite3')
        if encoder.identity != run['encoder_identity']: raise SystemExit('encoder identity differs from the stack')
        X = LR.features([r['question'] for r in rows], encoder)
        Xa = LR.features([c['question'] for c in acceptance], encoder)
        encoder.close()
    y = np.array([LR.LABELS.index(r['label']) for r in rows])
    part = np.array([split_of(r['group']) for r in rows])
    from sklearn.linear_model import LogisticRegression
    clf = LogisticRegression(C=a.C, max_iter=4000, class_weight='balanced')
    tr = part == 'train'
    clf.fit(X[tr], y[tr])
    W = np.zeros((len(LR.LABELS), X.shape[1])); b = np.full(len(LR.LABELS), -30.0)
    for i, cls in enumerate(clf.classes_): W[cls] = clf.coef_[i]; b[cls] = clf.intercept_[i]
    logits = lambda M: M @ W.T + b
    cal, held = part == 'cal', part == 'heldout'
    temperature = fit_temperature(logits(X[cal]), y[cal])
    ph = LR.softmax(logits(X[held]) / temperature)
    maxp, pred = ph.max(1), ph.argmax(1)
    correct = pred == y[held]
    eligible = np.isin(pred, [LR.LABELS.index(l) for l in LR.SHORTCUT])  # not_answerable predictions always defer
    threshold, held_cov, held_acc = choose_threshold(maxp[eligible], correct[eligible])
    held_cov = held_cov * eligible.mean()  # as a share of all held-out questions
    print(f'temperature {temperature:.3f}; held-out n={held.sum()} argmax acc {correct.mean():.3f}; '
          f'threshold {threshold:.4f} coverage {held_cov:.3f} acc {held_acc:.3f}; ECE {conformal.ece(maxp, correct):.3f}')

    # acceptance (test only)
    pa = LR.softmax(logits(Xa) / temperature)
    from clinical_core.agere.clinical.local_compiler import compile as compile_locally
    results = []
    for c, p in zip(acceptance, pa):
        label = LR.LABELS[int(p.argmax())]
        ok = label in {c['expect_operation'], *c.get('also_accept', [])}
        try: graph, _ = compile_locally(c['question'], profile, as_of)
        except Exception: graph = None  # noqa: BLE001
        results.append({'question': c['question'], 'expect': c['expect_operation'], 'predicted': label, 'probability': round(float(p.max()), 4),
                        'covered': bool(p.max() >= threshold and label in LR.SHORTCUT), 'correct': ok, 'compiler_handles': graph is not None,
                        'paid_router_logged': (router_log.get(c['question']) or {}).get('label')})
    def summary(sub):
        cov = [r for r in sub if r['covered']]
        return {'n': len(sub), 'argmax_accuracy': round(sum(r['correct'] for r in sub) / max(1, len(sub)), 4),
                'coverage': round(len(cov) / max(1, len(sub)), 4), 'covered': len(cov),
                'accuracy_when_covered': round(sum(r['correct'] for r in cov) / max(1, len(cov)), 4) if cov else None,
                'wrong_when_covered': [(r['question'], r['expect'], r['predicted'], r['probability']) for r in cov if not r['correct']]}
    test = {'all': summary(results), 'compiler_declined': summary([r for r in results if not r['compiler_handles']])}
    logged = [r for r in results if r['paid_router_logged']]
    test['paid_router_on_logged_acceptance'] = {'n': len(logged), 'accuracy': round(sum(r['paid_router_logged'] in {r['expect']} for r in logged) / max(1, len(logged)), 4)}
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'wrong_when_covered'} for k, v in test.items()}, indent=1))
    for w in test['all']['wrong_when_covered']: print('  WRONG', w)

    # economics of the paid choose step, from its own receipts
    from clinical_core.agere.economics import usage_cost
    usd = [((usage_cost(v['model'], v['usage']) or {}).get('estimated_usd') or 0) for v in router_log.values() if v.get('usage')]
    secs = [v['seconds'] for v in router_log.values() if v.get('seconds')]
    choose = {'receipts': len(router_log), 'mean_usd': round(float(np.mean(usd)), 6) if usd else None,
              'mean_seconds': round(float(np.mean(secs)), 3) if secs else None}
    cov = test['compiler_declined']['coverage'] if test['compiler_declined']['n'] else test['all']['coverage']
    savings = {'per_100_router_questions_usd': round(100 * cov * (choose['mean_usd'] or 0), 4),
               'per_100_router_questions_seconds': round(100 * cov * (choose['mean_seconds'] or 0), 1), 'coverage_used': cov}
    print('paid choose step', choose, 'savings', savings)

    report = {'version': LR.VERSION, 'trained_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'training_rows': len(rows),
              'label_sources': dict(sources), 'labels': dict(collections.Counter(r['label'] for r in rows)),
              'splits': dict(collections.Counter(part.tolist())), 'C': a.C, 'temperature': temperature,
              'heldout': {'n': int(held.sum()), 'argmax_accuracy': round(float(correct.mean()), 4), 'threshold': threshold,
                          'coverage': round(held_cov, 4), 'accuracy_at_threshold': round(held_acc, 4),
                          'ece': round(float(conformal.ece(maxp, correct)), 4), 'brier': round(float(conformal.brier(maxp, correct)), 4)},
              'acceptance_test': test, 'acceptance_rows': results, 'paid_choose_step': choose, 'savings_estimate': savings,
              'seconds': round(time.time() - started, 1)}
    if a.write:
        model = {'version': LR.VERSION, 'labels': LR.LABELS, 'cues': LR.cue_names(), 'encoder_identity': run['encoder_identity'],
                 'lexical_scale': LR.LEXICAL_SCALE, 'temperature': temperature, 'threshold': threshold,
                 'target_heldout_accuracy': TARGET_ACCURACY, 'heldout': report['heldout'],
                 'training': {'rows': len(rows), 'label_sources': dict(sources), 'benchmarks_excluded': True},
                 'weights': np.round(W, 6).tolist(), 'bias': np.round(b, 6).tolist()}
        LR.MODEL_FILE.write_text(json.dumps(model, separators=(',', ':')))
        (Path(__file__).resolve().parent / 'router_report.json').write_text(json.dumps(report, indent=1))
        print('wrote', LR.MODEL_FILE)


if __name__ == '__main__':
    main()
