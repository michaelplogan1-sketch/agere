"""Train and evaluate the schema reranker (clinical_core/agere/clinical/schema_rerank.py) offline.

Gold labels come from verified SQL in the bottling harvests (artifacts/bottling/answers-*.jsonl). Every benchmark
question (bench/questions, bench/capability, bench/acceptance) and its near-duplicates is excluded from training;
evaluation is grouped 5-fold cross-validation over question clusters. Benchmark reference SQL is used only as an extra
test set (stream / field / value coverage). The live stack is read read-only. No network, no paid calls.

Usage: .venv/bin/python tools/schema_rerank_eval.py [--stack DIR] [--save] [--out report.json]
"""
import argparse, glob, json, re, sys, time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import nl2sql, schema_rerank as R  # noqa: E402

MAIN = Path('/Users/mikel/clinical-insights-local')


def bench_questions(root):
    qs = []
    for f in sorted((root / 'bench/questions').glob('*.json')):
        qs += [(q.get('id'), q['question'], _sql(root / 'bench', q.get('reference_sql'))) for q in json.load(open(f)) if isinstance(q, dict) and q.get('question')]
    cap = root / 'bench/capability/questions.json'
    if cap.exists(): qs += [(q.get('id'), q['question'], _sql(root / 'bench/capability', q.get('reference_sql'))) for q in json.load(open(cap)) if isinstance(q, dict) and q.get('question')]
    for f in sorted((root / 'bench/acceptance').glob('*.json')):
        qs += [(None, q['question'], None) for q in json.load(open(f)) if isinstance(q, dict) and q.get('question')]
    return qs


def _sql(base, ref):
    if not ref: return None
    path = base / ref
    if ref.endswith('.sql') and path.exists(): return path.read_text()
    return ref if re.search(r'(?i)\bselect\b', ref) else None


def jaccard(a, b):
    return len(a & b) / max(len(a | b), 1)


def harvest(root, bench):
    bench_norm = {q.strip().lower() for _, q, _ in bench}
    bench_tok = [set(R.tokens(q)) for _, q, _ in bench]
    seen, pairs, dropped = set(), [], Counter()
    for f in sorted(glob.glob(str(root / 'artifacts/bottling/answers-*.jsonl'))):
        for line in open(f):
            if not line.strip(): continue
            r = json.loads(line)
            sql = r.get('query') or ''
            if r.get('route') not in ('hosted_verified_sql', 'local_compiler', 'frontier_escalation') or not re.search(r'(?i)\bselect\b', sql):
                continue
            key = r['question'].strip().lower()
            if key in seen: dropped['duplicate'] += 1; continue
            seen.add(key)
            if key in bench_norm: dropped['benchmark_exact'] += 1; continue
            t = set(R.tokens(r['question']))
            if any(jaccard(t, b) >= 0.8 for b in bench_tok): dropped['benchmark_near'] += 1; continue
            pairs.append({'question': r['question'], 'sql': re.sub(r'--[^\n]*\n', '', sql).strip(), 'area': r.get('area')})
    return pairs, dropped


def clusters(pairs, vecs):
    parent = list(range(len(pairs)))

    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    toks = [set(R.tokens(p['question'])) for p in pairs]
    sims = vecs @ vecs.T if vecs is not None else None
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            if jaccard(toks[i], toks[j]) >= 0.5 or (sims is not None and sims[i, j] >= 0.85):
                parent[find(i)] = find(j)
    return [find(i) for i in range(len(pairs))]


def baseline_shown(question, profile, cat, extra=()):
    """Items the current nl2sql.schema_for shows (mirrors its rendering rules) + its stream ranking."""
    from clinical_core.agere.clinical import source_profile
    shown = {it['id'] for it in cat.items if it['kind'] in ('table', 'column') or it['id'].startswith(('concept:', 'drug_class:'))}
    for t, spec in cat.tables.items():
        for c, vals in spec['enums'].items(): shown |= {f'value:{t}.{c}={v}' for v in vals}
    streams = nl2sql.retrieve_streams(question, profile, k=6)
    streams += [x for x in extra if x not in streams and x in profile['streams']]
    for s in streams:
        shown.add(f'stream:{s}')
        for f, fs in profile['streams'][s]['fields'].items():
            if not fs['populated'] or f in source_profile.PLANNER_SKIP or f.endswith(('_time',)): continue
            shown.add(f'field:{s}.{f}')
            vals = fs.get('values') or {}
            if fs['types'].get('true') or fs['types'].get('false'): continue
            if vals and len(vals) <= 12 and max(map(len, vals)) <= 40:
                shown |= {f'value:{s}.{f}={v}' for v in sorted(vals, key=lambda v: -vals[v])[:12]}
    enc = (profile['streams'].get('encounters') or {}).get('fields', {}).get('type', {}).get('values') or {}
    shown |= {f'value:visit.type={v}' for v in sorted(enc, key=lambda v: -enc[v])[:30]}
    return shown, streams


def failed_bench_ids(root):
    """Benchmark question ids Agere answered wrongly in any recorded run (bench/results/agere)."""
    out = set()
    for f in glob.glob(str(root / 'bench/results/agere/*.jsonl')):
        for line in open(f):
            if not line.strip(): continue
            r = json.loads(line)
            score = r.get('score') or {}
            if isinstance(score, dict) and score.get('correct') is False and not score.get('correct_abstention'):
                out.add(r.get('id'))
    return out


def shots_eval(p, train, qvec, tvecs, out, k=4):
    """Few-shot example choice: current token Jaccard (score >= 0.2) vs encoder cosine vs hybrid."""
    gold = {i for i in p['gold'] if not i.startswith('column:')}
    if not gold: return
    qt = set(nl2sql._tokens(p['question']))  # the current rule's tokens
    tt = [set(nl2sql._tokens(x['question'])) for x in train]
    jac = np.array([jaccard(qt, t) for t in tt])
    picks = {'jaccard_current': [n for n in np.argsort(-jac)[:k] if jac[n] >= 0.2]}
    if qvec is not None:
        cos = tvecs @ qvec
        picks['encoder'] = list(np.argsort(-cos)[:k])
        picks['hybrid'] = list(np.argsort(-(cos + 0.5 * jac))[:k])
        # same number of examples as the current rule (prompt size unchanged), chosen by the hybrid score
        picks['hybrid_same_count'] = picks['hybrid'][:len(picks['jaccard_current'])]
    for name, idx in picks.items():
        used = set().union(*[train[n]['gold'] for n in idx]) if idx else set()
        out[name].append(len(gold & used) / len(gold))


def kind(i):
    g = i.split(':', 1)[0]
    return 'concept' if g in ('concept', 'drug_class') else g


def summarize(rows, ks=(1, 3, 6, 10, 25)):
    """rows: [{'gold': set, 'ranked': {group: [ids]}, 'shown': set, 'full': set, 'chars': int}]"""
    out = {}
    for g in R.GROUPS:
        sub = [r for r in rows if any(kind(i) == g for i in r['gold'])]
        if not sub: continue
        m = {'questions': len(sub), 'gold_items': sum(sum(kind(i) == g for i in r['gold']) for r in sub)}
        if all('ranked' in r for r in sub):
            for k in ks:
                hit = sum(len({i for i in r['gold'] if kind(i) == g} & set(r['ranked'].get(g, [])[:k])) for r in sub)
                m[f'recall@{k}'] = round(hit / m['gold_items'], 4)
            rr = []
            for r in sub:
                gold = {i for i in r['gold'] if kind(i) == g}
                ranks = [n for n, i in enumerate(r['ranked'].get(g, [])) if i in gold]
                rr.append(1 / (1 + ranks[0]) if ranks else 0.0)
            m['mrr'] = round(float(np.mean(rr)), 4)
        hit = sum(len({i for i in r['gold'] if kind(i) == g} & r['shown']) for r in sub)
        m['slice_coverage'] = round(hit / m['gold_items'], 4)
        if all('full' in r for r in sub):
            hit = sum(len({i for i in r['gold'] if kind(i) == g} & r['full']) for r in sub)
            m['slice_full_doc'] = round(hit / m['gold_items'], 4)
        m['questions_fully_covered'] = round(sum({i for i in r['gold'] if kind(i) == g} <= r['shown'] for r in sub) / len(sub), 4)
        out[g] = m
    out['all'] = {'questions': len(rows), 'questions_fully_covered': round(sum(r['gold'] <= r['shown'] for r in rows) / max(len(rows), 1), 4),
                  'item_coverage': round(sum(len(r['gold'] & r['shown']) for r in rows) / max(sum(len(r['gold']) for r in rows), 1), 4),
                  'mean_slice_chars': round(float(np.mean([r['chars'] for r in rows])), 1),
                  'max_slice_chars': int(max([r['chars'] for r in rows] or [0]))}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stack', default=str(MAIN / 'artifacts/agere-healthcare-v123/stack'))
    ap.add_argument('--data-root', default=str(MAIN))
    ap.add_argument('--save', action='store_true', help='train on all pairs and write the shipped model file')
    ap.add_argument('--out', default=None)
    ap.add_argument('--no-encoder', action='store_true')
    args = ap.parse_args()
    stack, data_root = Path(args.stack), Path(args.data_root)
    started = time.time()
    profile = json.loads((stack / 'source-profile.json').read_text())  # read-only
    vocab = R.semantic_vocabulary(stack)
    cat = R.Catalog(profile, nl2sql.TABLES, vocab)
    encoder = None if args.no_encoder else R.load_encoder()
    bench = bench_questions(data_root)
    pairs, dropped = harvest(data_root, bench)
    for p in pairs:
        p['gold'] = R.gold_items(p['sql'], cat)['items']
    pairs = [p for p in pairs if p['gold']]
    qv = encoder.encode([p['question'] for p in pairs], batch_size=64) if encoder is not None else None
    cl = clusters(pairs, qv)
    uniq = sorted(set(cl))
    rng = np.random.default_rng(11); rng.shuffle(uniq)
    fold_of = {c: n % 5 for n, c in enumerate(uniq)}
    folds = [fold_of[c] for c in cl]
    legacy = lambda q: nl2sql.retrieve_streams(q, profile, k=20)
    base = R.Reranker(cat, encoder, legacy=legacy)  # shares item vectors with every fold model
    report = {'version': R.VERSION, 'data': {'harvest_pairs_with_gold': len(pairs), 'dropped': dict(dropped), 'clusters': len(set(cl)),
              'catalog_items': len(cat.items), 'catalog_by_kind': dict(Counter(R._group(it) for it in cat.items)),
              'gold_items_by_group': dict(Counter(kind(i) for p in pairs for i in p['gold'])),
              'encoder': None if encoder is None else str(encoder.identity)}}
    rows_base, rows_lex, rows_rr, rows_rl = [], [], [], []
    shot_rows = defaultdict(list)
    for fold in range(5):
        train = [p for p, f in zip(pairs, folds) if f != fold]
        test = [p for p, f in zip(pairs, folds) if f == fold]
        pri = R.Priors([(p['question'], p['gold']) for p in train])
        rr = R.Reranker(cat, encoder, priors=pri, legacy=legacy, vectors=base.vectors)
        rr.weights = R.train(rr, [(p['question'], p['gold']) for p in train])
        rl = R.Reranker(cat, None, priors=pri, legacy=legacy)  # encoder unavailable: lexical fallback weights
        rl.weights = R.train(rl, [(p['question'], p['gold']) for p in train])
        tr_idx = [n for n, f in enumerate(folds) if f != fold]
        for p in test:
            q = p['question']
            shots_eval(p, [pairs[n] for n in tr_idx], qv[pairs.index(p)] if qv is not None else None,
                       qv[tr_idx] if qv is not None else None, shot_rows)
            shown, streams = baseline_shown(q, profile, cat)
            cur = nl2sql.legacy_schema_for(q, profile)[1]
            branked = {'stream': ['stream:' + s for s in legacy(q)]}
            rows_base.append({'gold': p['gold'], 'shown': shown, 'full': shown, 'chars': len(cur), 'ranked': branked})
            X = rr.features(q)
            lex = X[:, 2] + 2 * X[:, 9] + X[:, 10]
            lranked = defaultdict(list)
            for k in np.argsort(-lex): lranked[R.GROUPS[rr.group[k]]].append(cat.items[k]['id'])
            scores = rr.score(q, X)
            ranked = defaultdict(list)
            for i, _ in rr.rank(q, scores=scores): ranked[kind(i)].append(i)
            sl = R.render(rr, q, profile, scores=scores, budget=len(cur))
            rows_lex.append({'gold': p['gold'], 'shown': set(), 'chars': 0, 'ranked': lranked})
            rows_rr.append({'gold': p['gold'], 'shown': sl['shown'], 'full': sl['full'], 'chars': len(sl['text']), 'ranked': ranked})
            sc = rl.score(q)
            ranked = defaultdict(list)
            for i, _ in rl.rank(q, scores=sc): ranked[kind(i)].append(i)
            sl = R.render(rl, q, profile, scores=sc, budget=len(cur))
            rows_rl.append({'gold': p['gold'], 'shown': sl['shown'], 'full': sl['full'], 'chars': len(sl['text']), 'ranked': ranked})
        print(f'fold {fold}: train {len(train)} test {len(test)}', file=sys.stderr)
    report['current_schema_for'] = summarize(rows_base)
    report['lexical_only_ranking'] = {g: {k: v for k, v in m.items() if k.startswith(('recall', 'mrr', 'questions', 'gold'))}
                                      for g, m in summarize(rows_lex).items() if g != 'all'}
    report['reranker'] = summarize(rows_rr)
    report['reranker_without_encoder'] = summarize(rows_rl)
    report['few_shot_selection'] = {'metric': 'share of the question\'s gold tables/streams/fields/values/concepts used by the 4 selected verified examples',
                                    **{k: {'mean_gold_recall': round(float(np.mean(v)), 4), 'questions': len(v)} for k, v in shot_rows.items()}}
    report['prompt_chars'] = {'current_mean': report['current_schema_for']['all']['mean_slice_chars'],
                              'reranker_mean': report['reranker']['all']['mean_slice_chars'],
                              'reranker_over_budget': sum(r['chars'] > b['chars'] for r, b in zip(rows_rr, rows_base))}
    # Benchmark reference SQL: test-only coverage of streams / fields / values on the raw store.
    final_pri = R.Priors([(p['question'], p['gold']) for p in pairs])
    final = R.Reranker(cat, encoder, priors=final_pri, legacy=legacy, vectors=base.vectors)
    final.weights = R.train(final, [(p['question'], p['gold']) for p in pairs])
    final_lex = R.Reranker(cat, None, priors=final_pri, legacy=legacy)
    lexical_weights = R.train(final_lex, [(p['question'], p['gold']) for p in pairs])
    brows_base, brows_rr = [], []
    for qid, q, ref in bench:
        if not ref: continue
        gold = {i for i in R.gold_items(ref, cat)['items'] if kind(i) in ('stream', 'field', 'value')}
        if not gold: continue
        shown, _ = baseline_shown(q, profile, cat)
        cur = nl2sql.legacy_schema_for(q, profile)[1]
        sl = R.render(final, q, profile, budget=len(cur))
        brows_base.append({'id': qid, 'gold': gold, 'shown': shown, 'chars': len(cur)})
        brows_rr.append({'id': qid, 'gold': gold, 'shown': sl['shown'], 'full': sl['full'], 'chars': len(sl['text'])})
    failed = failed_bench_ids(data_root)
    report['benchmark_reference_sql_test_only'] = {
        'note': 'raw-store reference SQL; streams/fields/values only; final model trained on harvest only',
        'current_schema_for': summarize(brows_base), 'reranker': summarize(brows_rr),
        'known_failures': {'ids': len(failed),
                           'current_schema_for': summarize([r for r in brows_base if r['id'] in failed]),
                           'reranker': summarize([r for r in brows_rr if r['id'] in failed])}}
    report['elapsed_seconds'] = round(time.time() - started, 1)
    if args.save:
        R.save_model(final, lexical_weights)
        report['saved_model'] = str(R.MODEL_FILE.relative_to(ROOT))
    text = json.dumps(report, indent=1, default=str)
    if args.out: Path(args.out).write_text(text)
    print(text)


if __name__ == '__main__':
    main()
