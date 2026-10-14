"""Fit the gate thresholds in clinical_core/agere/clinical/calibrated_thresholds.json with Learn-then-Test.

Target (calibrated_thresholds.TARGET): error among accepted <= 2% with probability >= 90%. Each gate's calibration data:

  learned_router              held-out split of the router's own non-benchmark training rows (tools/learned_routing/train.py
                              rows and split; benchmark and near-benchmark questions excluded), scored with the SAVED
                              model; error = wrong engine among eligible (shortcut-label) predictions. Bound to the weights.
  semantic_cache.similarity   tools/learned_routing/semantic_cache_eval.py cases (hand-written adversarial pairs, minimal-
                              difference variants of synthetic and live verified questions, paraphrases); score = cosine
                              when the slot check passes; error = reuse of a non-equivalent question. Bound to the encoder.
                              Searched only down to 0.80 (below that the embedding stops being an independent check).
  nl2sql.decider              saved bench receipts where the typed decider picked A or B and the picked candidate is the
                              delivered answer; error = that answer was wrong (adjudicated engine_error, else the score).
                              Benchmark-derived (no non-benchmark labels exist for this gate yet); stated in the record.
  economical_planner.decision saved bench receipts of the typed Perplexity route; same labelling.
A gate that cannot be certified keeps its hand-set value; the record says why and how much data it would need.
No model is called; the stack is opened read-only; the only file written is the thresholds JSON.

Usage: .venv/bin/python tools/calibration/fit_thresholds.py [--stack DIR] [--results DIR] [--skip router,cache] [--write]
"""
import argparse, json, os, random, shutil, sqlite3, sys, tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools' / 'learned_routing'))
from clinical_core.agere.clinical import calibrated_thresholds as CT  # noqa: E402

LIVE = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')


def router_data(stack):
    import train as T
    from clinical_core.agere.clinical import learned_router as LR
    from templates import generate
    acceptance, bench = T.bench_sets(); near_bench = T.bench_filter(bench)
    profile, run = T.profile_readonly(stack); as_of = run['snapshot']['as_of']
    raw = T.harvest(stack)
    router_log = T.recovered_router_decisions(stack, sorted({r['question'] for r in raw}), as_of)
    seen, rows = set(), []
    for r in raw:
        q = r['question']
        if T.norm(q) in seen: continue
        seen.add(T.norm(q))
        if near_bench(q): continue
        label = T.compiler_label(q, profile, as_of)
        if label is None and q in router_log: label = router_log[q]['label']
        if label is None and r.get('route') in T.ROUTE_LABEL: label = T.ROUTE_LABEL[r['route']]
        if label is None and r.get('area') in T.LITERAL_AREAS: label = 'literal_lookup'
        if label is None: continue
        rows.append({'question': q, 'label': label, 'group': 'h:' + T.norm(q)})
    for t in generate(per_template=8):
        q = t['question']
        if T.norm(q) in seen or near_bench(q): continue
        seen.add(T.norm(q))
        rows.append({'question': q, 'label': T.compiler_label(q, profile, as_of) or t['intent'], 'group': 't:' + t['template']})
    held = [r for r in rows if T.split_of(r['group']) == 'heldout']
    model = LR.load()
    if model is None: raise SystemExit('no learned router model')
    from clinical_core.local_encoders import LocalEncoder
    with tempfile.TemporaryDirectory() as tmp:
        encoder = LocalEncoder(run['encoder_path'], pooling='mean', cache=Path(tmp) / 'enc.sqlite3')
        X = LR.features([r['question'] for r in held], encoder)
        encoder.close()
    P = LR.probabilities(model, X)
    pred = P.argmax(1); maxp = P.max(1)
    y = np.array([LR.LABELS.index(r['label']) for r in held])
    eligible = np.isin(pred, [LR.LABELS.index(l) for l in LR.SHORTCUT])
    scores = np.where(eligible, maxp, -1.0)  # not_answerable predictions always defer to the paid router: never accepted
    errors = pred != y
    return scores.tolist(), errors.tolist(), {'heldout_rows': len(held), 'eligible': int(eligible.sum()),
                                               'source': 'learned-router training rows, heldout split (non-benchmark)'}, LR.model_identity(model)


def cache_data(stack):
    import semantic_cache_eval as E
    from clinical_core.agere.clinical import learning, semantic_cache as SC
    rng = random.Random(11)
    run = json.loads((stack / 'run.json').read_text())
    db = sqlite3.connect(f'file:{stack / "learning.sqlite3"}?mode=ro', uri=True)
    live = sorted({q for (q,) in db.execute("SELECT question FROM verified WHERE plan_kind='sql'")}); db.close()
    live = [q for q in live if not SC.is_benchmark(q) and len(q) < 220]; rng.shuffle(live)
    cases = [{**p} for p in json.loads((ROOT / 'tools' / 'learned_routing' / 'adversarial_pairs.json').read_text())]
    for q in E.SYNTHETIC_BASES + live[:200]:
        cases += [{'base': q, 'variant': v, 'reuse': False} for v, _ in E.minimal_differences(q, rng)]
        cases += [{'base': q, 'variant': f(q), 'reuse': True} for f in rng.sample(E.PARAPHRASES, 3) if f(q) != q]
    scores, errors = [], []
    from clinical_core.local_encoders import LocalEncoder
    with tempfile.TemporaryDirectory() as tmp:
        encoder = LocalEncoder(run['encoder_path'], pooling='mean', cache=Path(tmp) / 'enc.sqlite3')
        by_base = {}
        for c in cases: by_base.setdefault(c['base'], []).append(c)
        for base, items in by_base.items():
            d = Path(tmp) / 'stack'
            if d.exists(): shutil.rmtree(d)
            d.mkdir(); (d / 'run.json').write_text((stack / 'run.json').read_text())
            learning.record_verified(d, base, 'sql', {'query': 'SELECT 1'}, {}, 'hosted_verified_sql', [], 'calibration')
            for c in items:
                hit, info = SC.lookup(d, c['variant'], encoder, threshold=-1.0)
                if (info.get('reason') or '').startswith('exact'): continue  # the exact rung owns case-only variants
                scores.append(info['similarity'] if hit is not None else -1.0)
                errors.append(bool(hit is not None and not c['reuse']))
        identity = encoder.identity
        encoder.close()
    return scores, errors, {'cases': len(scores), 'negatives': sum(1 for c in cases if not c['reuse']),
                            'source': 'semantic_cache_eval cases (adversarial + minimal differences + paraphrases; benchmarks excluded)'}, identity


def receipt_data(results, kind):
    """(score, error) pairs from saved bench receipts for the typed decider ('decider') or the typed route ('decision')."""
    sys.path.insert(0, str(ROOT))
    from tools.quality import defects as D
    runs = D.load_runs(results); adj = D.load_adjudications(results)
    seen, scores, errors = set(), [], []
    for r in runs:
        if r['system'] != 'agere': continue
        cats = {i['id']: i['category'] for i in (adj.get(r['file']) or {}).get('items') or []}
        for row in r['rows']:
            pr = ((row.get('response') or {}).get('planner_receipt') or {})
            wrong = cats.get(row['id']) == 'engine_error' or (row['id'] not in cats and not row['score'].get('correct'))
            if kind == 'decider':
                hs = pr.get('hosted_sql') or {}; pick = hs.get('decider') or {}
                if pick.get('choice') not in ('A', 'B') or not str(hs.get('agreement', '')).startswith('decider'): continue
                score = pick.get('probability')
            else:
                step = next((s for s in pr.get('steps') or [] if s.get('stage') == 'typed_decision_router'), None)
                if not step or pr.get('route') != 'compact_retrieval_typed_router': continue
                dec = step.get('decision') or {}; score = (dec.get('probabilities') or {}).get(dec.get('choice'))
            key = (row['question'], score)
            if score is None or key in seen: continue
            seen.add(key); scores.append(float(score)); errors.append(bool(wrong))
    return scores, errors, {'cases': len(scores), 'source': f'saved bench receipts ({kind} picks that were delivered); benchmark-derived'}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--stack', default=str(LIVE)); ap.add_argument('--results', default=str(ROOT / 'bench' / 'results'))
    ap.add_argument('--skip', default=''); ap.add_argument('--write', action='store_true'); ap.add_argument('--out', default=None)
    a = ap.parse_args(argv)
    stack, skip = Path(a.stack), set(filter(None, a.skip.split(',')))
    from clinical_core.agere.clinical import learned_router as LR, nl2sql, semantic_cache as SC, economical_planner as EP
    records = []
    if 'router' not in skip:
        s, e, data, bind = router_data(stack)
        records.append(CT.fit('learned_router', s, e, (LR.load() or {}).get('threshold'), data, grid=[x for x in sorted(set(s)) if x >= 0], bind=bind))
    if 'cache' not in skip:
        s, e, data, bind = cache_data(stack)
        records.append(CT.fit('semantic_cache.similarity', s, e, SC.SIMILARITY, data, floor=0.80, bind=bind,
                              grid=[x for x in sorted(set(s)) if x >= 0]))
    s, e, data = receipt_data(Path(a.results), 'decider')
    records.append(CT.fit('nl2sql.decider', s, e, nl2sql.Decider.THRESHOLD, data))
    s, e, data = receipt_data(Path(a.results), 'decision')
    records.append(CT.fit('economical_planner.decision', s, e, EP.DECISION_THRESHOLD, data))
    for r in records:
        print(r['name'], 'calibrated' if r['calibrated'] else 'FALLBACK', r.get('threshold'), '|', r.get('guarantee') or r.get('reason'))
    if a.write or a.out:
        CT.write(records, a.out)


if __name__ == '__main__':
    main()
