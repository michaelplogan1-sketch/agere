"""Calibrate Agere's free-tier cascades (clinical_core/agere/clinical/cascade.py) and write config/agere/cascade_thresholds.json.

    # 1. collect route outcomes (writes tools/calibration/data/cascade_route_calibration.jsonl; append-only by source)
    .venv/bin/python tools/calibrate_cascades.py collect-offline --workspace-root <scratch root with a copy of pilot-native-v2-12>
    .venv/bin/python tools/calibrate_cascades.py collect-full    --workspace-root <scratch root> [--limit N] [--workers 3]
    .venv/bin/python tools/calibrate_cascades.py collect-bench
    .venv/bin/python tools/calibrate_cascades.py collect-families [--semantic <semantic.sqlite3>]   # dq_family + vbc_family
    # 2. therapy-barrier labels: tools/calibration/label_therapy_candidates.py (Claude reference + Jev + Perplexity)
    # 3. fit and write the versioned thresholds
    .venv/bin/python tools/calibrate_cascades.py fit [--alpha 0.05] [--delta 0.10] [--write]

Calibration data (each row: tier, score, error, group, source, provenance):
  offline   Gold 10-derived question variants (tools/calibration/cascade_variants.py: parameters and phrasing varied, trusted
            patient list rendered from an explicitly written definition) answered by the cohort-family route alone
            (cohort_families.plan with no model), plus the locked Gold 10 questions (truth = locked customer-id lists).
  full      the same variants WITHOUT the cohort-family shortcut being the only path: the product's fast path
            (Stack._compose under specialists-first deferral, live small-model rungs through the metered ledger) on a
            scratch copy of the workspace; the row's tier is the specialist route that claimed the question
            (together._specialist_answer), error = its patient list (count) differs from the trusted one.
  bench     saved bench result rows (bench/results/agere, development/challenge splits; references are our own SQL, not
            generator answer keys) through tools/certify_gates.bench_rows; blind heldout* rows are test-only. Plus the
            semantic-cache adversarial/paraphrase cases (tools/calibration/data/semantic_cache_cases.json).
  families  question variants with inline definitions (tools/calibration/family_variants.py: years, products, formats,
            conditions, statuses, codes, counts, windows varied) answered by dq_families.plan / vbc_families.plan alone and
            executed read-only; truth = an independent Python reference over plain row reads for an explicitly written
            definition; a variant the family declines is skipped (the route did not claim it).
  therapy   tools/calibration/data/therapy_barrier_labels.jsonl (reference = claude-opus-5-5 label).

Split: by a hash of each row's group (one trusted definition / one question); 70% calibration, 30% evaluation.
One label/score-independent, hash-selected representative per group enters the binomial tests. Repeated wordings are
retained in the row audit, not counted as independent observations. Any blind member makes its whole group evaluation-only.
Unresolved reference labels are not negative supervision. A tier is certified only when Learn-then-Test certifies a threshold on its calibration part and
the held-out part does not refute alpha (one-sided binomial test at delta). Tiers with too little data stay
"uncalibrated" (today's behaviour). Synthetic data only.
"""
from __future__ import annotations

import argparse, hashlib, json, os, sqlite3, sys, time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools' / 'calibration'))

from clinical_core.agere.clinical import cascade as C  # noqa: E402

DATA = ROOT / 'tools' / 'calibration' / 'data'
ROUTE_SET = DATA / 'cascade_route_calibration.jsonl'
THERAPY_LABELS = DATA / 'therapy_barrier_labels.jsonl'
GOLD = ROOT / 'docs' / 'agere' / 'gold10'
SLUG = 'pilot-native-v2-12'
CAL_SHARE = 0.7
VERSION = 'calibrate-cascades/2'


def part(group):
    h = int(hashlib.sha256(('cascade|' + str(group)).encode()).hexdigest()[:12], 16) / 16 ** 12
    return 'cal' if h < CAL_SHARE else 'test'


def _append(rows, source, path=ROUTE_SET):
    """Replace the rows of one source in the calibration set (other sources kept)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = []
    if path.exists():
        keep = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        keep = [r for r in keep if r.get('source') != source]
    with open(path, 'w') as f:
        for r in keep + rows:
            f.write(json.dumps(r, default=str) + '\n')
    return len(keep) + len(rows)


def _workspace(root):
    from clinical_core.agere.onboarding.workspace import Workspace
    return Workspace(SLUG, root=root)


def _items():
    import cascade_variants as V
    return V.generate() + V.gold_items(GOLD)


def _truth(db, ws, item, tables, cache):
    """-> (truth ids, id space 'person' | 'customer')."""
    import cascade_variants as V
    if 'customer_ids' in item['truth']:
        return sorted(item['truth']['customer_ids']), 'customer'
    key = item['group']
    if key not in cache:
        cache[key] = V.trusted_ids(db, item, tables)
    return cache[key], 'person'


def _same(ws, got_ids, got_count, truth, space, answer_type):
    if answer_type == 'count':
        return got_count is not None and int(got_count) == len(truth)
    if got_ids is None:
        return False
    got = sorted({str(x) for x in got_ids})
    if space == 'customer':
        m = ws.customer_ids(got)
        got = sorted({m.get(x) or x for x in got})
    return got == sorted(truth)


# ---- offline: the cohort-family route alone --------------------------------------------------------------------------
def collect_offline(root):
    from clinical_core.agere.clinical import cohort_families as cf
    ws = _workspace(root)
    as_of = ws.state['as_of']
    db = sqlite3.connect(f'file:{ws.semantic_path}?mode=ro', uri=True)
    tables = cf._tables(ws.semantic_path)
    rows, cache, skipped = [], {}, Counter()
    for it in _items():
        res = cf.plan(ws.semantic_path, it['question'], as_of)  # no ledger: slots only a model could fill decline
        if res is None:
            skipped['family did not claim'] += 1
            continue
        node = res['graph']['nodes'][0]['parameters']
        out = [r for r in db.execute(node['query'])]
        got_ids, got_count = None, None
        if node['returns'] == 'count':
            got_count = out[0][0] if out else 0
        else:
            got_ids = [r[0] for r in out if r[0] is not None]
            got_count = len(set(got_ids))
        truth, space = _truth(db, ws, it, tables, cache)
        correct = _same(ws, got_ids, got_count, truth, space, it['answer_type'])
        score = C.cohort_family_score(res['receipt'])
        rows.append({'tier': 'cohort_family', 'item': it['id'], 'group': it['group'], 'score': score, 'error': not correct,
                     'source': 'offline_cohort_family', 'question': it['question'], 'answer_type': it['answer_type'],
                     'detail': {'family_claimed': res['spec']['family'], 'family_expected': it['family'], 'truth_n': len(truth),
                                'got_n': got_count, 'defaults': res['receipt'].get('defaults'), 'returns': node['returns']},
                     'provenance': it['provenance']})
    db.close()
    n = _append(rows, 'offline_cohort_family')
    errs = sum(r['error'] for r in rows)
    print(f'offline: {len(rows)} claimed ({errs} wrong), skipped {dict(skipped)}; calibration set now {n} rows')
    return rows


# ---- offline: the dq_family / vbc_family routes alone -------------------------------------------------------------------
PILOT_SEMANTIC = ROOT / 'artifacts' / 'workspaces' / SLUG / 'semantic.sqlite3'


def collect_families(semantic=None):
    import family_variants as FV
    from clinical_core.agere.clinical import dq_families, vbc_families
    from clinical_core.agere.clinical.sql_query import execute
    path = Path(semantic or PILOT_SEMANTIC)
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    ref = FV.Reference(db, FV.AS_OF)
    mods = {'dq_family': dq_families, 'vbc_family': vbc_families}
    rows = {'dq_family': [], 'vbc_family': []}
    skipped = Counter()
    for it in FV.generate():
        mod = mods[it['tier']]
        res = mod.plan(None, it['question'], FV.AS_OF)
        other_tier = 'vbc_family' if it['tier'] == 'dq_family' else 'dq_family'
        other = mods[other_tier].plan(None, it['question'], FV.AS_OF)
        if other is not None:  # the other tier also claims it: a routing error charged to that tier
            rows[other_tier].append({'tier': other_tier, 'item': it['id'], 'group': it['group'],
                                     'score': C.route_score(other_tier, {other_tier: other['receipt']}), 'error': True,
                                     'source': 'offline_families', 'question': it['question'], 'answer_type': it['answer_type'],
                                     'detail': {'claimed_by_wrong_tier': True, 'family_claimed': other['spec']['family']},
                                     'provenance': it['provenance']})
        if res is None:
            skipped[f"{it['tier']}: family did not claim"] += 1
            continue
        node = res['graph']['nodes'][0]['parameters']
        try:
            out = execute(None, node, target=path, time_limit=120)
            got = (out.get('value') if node['returns'] in ('count', 'value') else out.get('patient_ids') if node['returns'] == 'patient_set'
                   else {r['key']: r['value'] for r in out['rows']})
        except ValueError as exc:
            got = None; err = str(exc)
        else:
            err = None
        truth = ref.answer(it['truth'])
        correct = res['spec']['returns'] == it['answer_type'] and FV.same(got, truth, it['answer_type'])
        rows[it['tier']].append({'tier': it['tier'], 'item': it['id'], 'group': it['group'],
                                 'score': C.route_score(it['tier'], {it['tier']: res['receipt']}), 'error': not correct,
                                 'source': 'offline_families', 'question': it['question'], 'answer_type': it['answer_type'],
                                 'detail': {'family_claimed': res['spec']['family'], 'family_expected': it['family'],
                                            'got': got if not isinstance(got, list) else len(got),
                                            'truth': truth if not isinstance(truth, list) else len(truth), 'error': err},
                                 'provenance': it['provenance']})
    db.close()
    allrows = rows['dq_family'] + rows['vbc_family']
    n = _append(allrows, 'offline_families')
    for tier, rs in rows.items():
        errs = [r for r in rs if r['error']]
        print(f"{tier}: {len(rs)} claimed ({len(errs)} wrong)")
        for r in errs[:20]: print('   WRONG', r['question'][:120], '|', json.dumps(r['detail'], default=str)[:300])
    print(f'skipped {dict(skipped)}; calibration set now {n} rows')
    return allrows


# ---- full fast path ---------------------------------------------------------------------------------------------------
def collect_full(root, limit=None, workers=3, only_without_definition=False, resume=False, shard=None):
    if os.environ.get('AGERE_CALIBRATION_NO_MLX') == '1':
        # local MLX model rungs (bottled SQL, local rewrite/candidate) block on the GPU while another job trains on it; they are
        # not calibrated tiers here, so they are made unavailable (their code paths skip on ImportError)
        for m in ('mlx', 'mlx.core', 'mlx_lm'):
            sys.modules[m] = None
    from run_integrated_stack import keys
    keys()
    from clinical_core.agere.clinical import cohort_families as cf
    from clinical_core.agere.clinical.economical_planner import defer_to_analyst
    from clinical_core.agere.clinical.together import _specialist_answer, _fast_route
    from clinical_core.agere.onboarding import workspace_stack
    ws = _workspace(root)
    stack = workspace_stack.stack_for(ws, allow_live=True)
    db = sqlite3.connect(f'file:{ws.semantic_path}?mode=ro', uri=True, check_same_thread=False)
    tables = cf._tables(ws.semantic_path)
    items = _items()
    if only_without_definition:
        items = [i for i in items if 'Definition:' not in i['question']]
    if limit:
        items = items[:limit]
    cache = {}
    for it in items:  # trusted lists first (single thread)
        _truth(db, ws, it, tables, cache)

    def one(it):
        t0 = time.perf_counter()
        try:
            with defer_to_analyst(True):
                fast = stack._compose(it['question'], planning='auto')
            err = None
        except Exception as exc:  # noqa: BLE001
            fast, err = None, f'{type(exc).__name__}: {exc}'[:300]
        return it, fast, err, round(time.perf_counter() - t0, 2)

    rows, usd, routes = [], 0.0, Counter()
    # progress log: one line per asked question with its calibration row (or none), so an interrupted run resumes
    # shards run as separate processes (threads inside one process deadlocked in native libraries on long runs)
    partial = DATA / (f'cascade_full_run.partial.{shard[0]}of{shard[1]}.jsonl' if shard else 'cascade_full_run.partial.jsonl')
    done = {}
    if resume:
        for f in sorted(DATA.glob('cascade_full_run.partial*.jsonl')):
            for l in f.read_text().splitlines():
                try: d = json.loads(l)
                except ValueError: continue
                if 'row' in d and not d.get('error'): done[d['item']] = d
    if shard:
        items = [it for k, it in enumerate(items) if k % shard[1] == shard[0]]
        done = {k: v for k, v in done.items() if k in {it['id'] for it in items}}
    for d in done.values():
        routes[d['route']] += 1
        if d['row']: rows.append(d['row'])
    todo = [it for it in items if it['id'] not in done]
    print(f'full: resuming with {len(done)} done, {len(todo)} to ask', flush=True)
    from concurrent.futures import as_completed
    with ThreadPoolExecutor(max_workers=workers) as pool, open(partial, 'a' if resume else 'w') as plog:
        futures = [pool.submit(one, it) for it in todo]
        for k, fut in enumerate(as_completed(futures)):
            it, fast, err, secs = fut.result()
            route = _fast_route(fast) if fast else None
            econ = (fast or {}).get('planning_economics') or {}
            usd += econ.get('new_api_estimate_usd') or 0.0
            routes[route] += 1
            if k % 25 == 0:
                print(f'full: {k + 1}/{len(items)}  ${usd:.3f}  routes {dict(routes)}', flush=True)
            if err or not fast:
                plog.write(json.dumps({'item': it['id'], 'route': route, 'error': err or 'no result', 'seconds': secs}) + '\n'); plog.flush()
                continue
            receipt = fast.get('planner_receipt') or {}
            tier = C.route_tier(receipt)
            if tier is None or not _specialist_answer(fast, it['answer_type'] if it['answer_type'] == 'patient_set' else None, it['question']):
                plog.write(json.dumps({'item': it['id'], 'route': route, 'error': None, 'seconds': secs, 'row': None}) + '\n'); plog.flush()
                continue
            a = fast.get('answer') or {}
            got_ids = a.get('patient_ids') if isinstance(a.get('patient_ids'), list) else None
            if got_ids is None and isinstance(a.get('table'), list) and a['table'] and isinstance(a['table'][0], dict) and 'patient_id' in a['table'][0]:
                got_ids = [r['patient_id'] for r in a['table']]
            got_count = a.get('count') if a.get('count') is not None else (len(set(got_ids)) if got_ids is not None else None)
            truth, space = _truth(db, ws, it, tables, cache)
            correct = _same(ws, got_ids, got_count, truth, space, it['answer_type'])
            scored = receipt.get('prior_planning_receipt') if receipt.get('route') == 'exact_question_cache' else receipt
            rows.append({'tier': tier, 'item': it['id'], 'group': it['group'], 'score': C.route_score(tier, scored or {}),
                         'error': not correct, 'source': 'full_fast_path', 'question': it['question'], 'answer_type': it['answer_type'],
                         'detail': {'route': receipt.get('route'), 'truth_n': len(truth), 'got_n': got_count, 'seconds': secs,
                                    'cost_usd': econ.get('new_api_estimate_usd'), 'metric': receipt.get('metric'),
                                    'operator': receipt.get('operator')},
                         'provenance': it['provenance']})
            plog.write(json.dumps({'item': it['id'], 'route': route, 'error': None, 'seconds': secs, 'row': rows[-1]}, default=str) + '\n')
            plog.flush()
    db.close()
    if shard:
        print(f'full shard {shard}: {len(rows)} rows (run collect-full --resume to merge)')
        return rows
    n = _append(rows, 'full_fast_path')
    print(f'full: {len(items)} asked, routes {dict(routes)}, {len(rows)} specialist claims ({sum(r["error"] for r in rows)} wrong), '
          f'estimated new API ${usd:.3f}; calibration set now {n} rows')
    (DATA / 'cascade_full_run.ledger.json').write_text(json.dumps({'asked': len(items), 'routes': dict(routes), 'usd_estimate': usd,
                                                                  'written_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, indent=1))
    return rows


# ---- saved bench rows ---------------------------------------------------------------------------------------------------
BENCH_ROUTES = {'local_compiler': 'local_compiler', 'metric_router': 'metric_router', 'operator_router': 'operator_router',
                'compact_retrieval_typed_router': 'compact_retrieval_typed_router', 'learned_verified_sql': 'learned_verified_sql',
                'semantic_cache': 'semantic_cache', 'cohort_family': 'cohort_family'}


def collect_bench():
    from tools.certify_gates import bench_rows, results_dir, asserted, norm, cache_cases
    pool, blind, _ = bench_rows(results_dir())
    rows = []
    for split, src in (('pool', pool), ('blind', blind)):
        for row in src:
            if not asserted(row.get('answer')): continue
            if (row.get('expected') or {}).get('answer_type') == 'unsupported': continue
            receipt = (row.get('response') or {}).get('planner_receipt') or {}
            tier = C.route_tier(receipt)
            if tier is None: continue
            scored = receipt.get('prior_planning_receipt') if receipt.get('route') == 'exact_question_cache' else receipt
            rows.append({'tier': tier, 'item': row.get('id'), 'group': 'bench:' + norm(row['question']), 'score': C.route_score(tier, scored or {}),
                         'error': bool(row['wrong']), 'source': 'bench', 'blind': split == 'blind', 'question': row['question'],
                         'detail': {'run': row['run'], 'label_source': row['label_source'], 'split': row.get('split')},
                         'provenance': {'source': f"bench/results/agere/{row['run']} ({row.get('split')})"}})
    cases, enc = cache_cases(False)
    for c in cases or []:
        rows.append({'tier': 'semantic_cache', 'item': None, 'group': 'cache:' + c['key'], 'score': c['score'], 'error': c['error'],
                     'source': 'semantic_cache_cases', 'detail': {'encoder': enc},
                     'provenance': {'source': 'tools/calibration/data/semantic_cache_cases.json (adversarial + minimal differences + paraphrases)'}})
    _append([r for r in rows if r['source'] == 'bench'], 'bench')
    _append([r for r in rows if r['source'] == 'semantic_cache_cases'], 'semantic_cache_cases')
    print(f'bench: {Counter((r["tier"], r["source"], r.get("blind", False)) for r in rows)}')
    return rows


# ---- therapy tiers ------------------------------------------------------------------------------------------------------
def therapy_rows(path=None, *, audit=None):
    """Per-tier (score, error) rows with a settled reference label.

    Unknown/missing references cannot establish either a barrier or its absence and never supervise a certificate.
    A student's unknown is an abstention, not an agreed negative. These are reference-label comparisons, not clinical truth.
    tier1_rules: every rule-decided candidate (score = rule confidence); tier2_classifier: candidates the rules left open
    (score = classifier probability of its label); tier3_jev / tier3_perplexity: candidates the rules left open (the
    leftover population), score = provider probability of its label ('unknown' answers are never accepted)."""
    path = Path(path or THERAPY_LABELS)
    if not path.exists(): return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip(): continue
        c = json.loads(line)
        ref = (c['labels'].get('claude') or {}).get('label')
        if ref in (None, 'unknown'):
            if audit is not None:
                key = 'missing_reference' if ref is None else 'unresolved_reference'
                audit[key] = audit.get(key, 0) + 1
            continue
        g = 'tb:' + c['case_id']
        base = {'group': g, 'item': c['case_id'], 'source': 'therapy_labels', 'reference': ref}
        if c['tier'] == 1 and c['decided_by'] == 'rules':
            label = c['label']
            rows.append({**base, 'tier': 'tier1_rules', 'score': -1.0 if label in (None, 'unknown') else
                         c.get('confidence') if c.get('confidence') is not None else 1.0,
                         'error': label != ref, 'label': label})
            continue
        if c.get('classifier_label') is not None and c.get('classifier_probability') is not None:
            contradicted = c.get('student_assertion') in ('negated', 'other_person', 'retracted') and (c.get('student_probability') or 0) >= 0.5
            label = c['classifier_label']
            score = -1.0 if label == 'unknown' else 0.0 if (contradicted and label != 'no_barrier') else c['classifier_probability']
            rows.append({**base, 'tier': 'tier2_classifier', 'score': score, 'error': label != ref, 'label': label})
        for prov, tier in (('jev', 'tier3_jev'), ('perplexity', 'tier3_perplexity')):
            got = c['labels'].get(prov) or {}
            if not got: continue
            label, p = got.get('label'), got.get('probability')
            rows.append({**base, 'tier': tier, 'score': -1.0 if label in (None, 'unknown') or p is None else float(p),
                         'error': label != ref, 'label': label})
    return rows


# ---- fit ----------------------------------------------------------------------------------------------------------------
SCORE_DEFS = {
    'cohort_family': 'cascade.cohort_family_score: 1 - 0.08 x convention defaults applied - 0.25 x model-filled slots - 0.05 without an inline definition',
    'dq_family': 'family_common.score: 1 - 0.08 x convention defaults applied - 0.05 without an inline definition',
    'vbc_family': 'family_common.score: 1 - 0.08 x convention defaults applied - 0.05 without an inline definition',
    'semantic_cache': 'local encoder cosine similarity to the verified question (identical slots required first)',
    'compact_retrieval_typed_router': 'typed decider probability of its chosen analysis',
    'operator_router': 'learned-router probability when it chose the engine, else 1.0',
    'tier1_rules': 'rule confidence', 'tier2_classifier': 'barrier-type classifier probability of its label (0 when the student contradicts a barrier)',
    'tier3_jev': 'Jev probability of its label', 'tier3_perplexity': 'Perplexity Decisions probability of its label',
}


# today's hand-set operating point per tier (score >= this answers), used to test uncalibrated tiers on held-out data
TODAY = {'tier3_jev': 0.80, 'tier3_perplexity': 0.80, 'tier2_classifier': 0.70, 'semantic_cache': 0.85}


def refutes(test, alpha, delta):
    from clinical_core.agere.clinical.conformal import binomial_cdf
    n, k = test['n_accepted'], test['errors_accepted']
    return bool(n) and k > 0 and (1 - binomial_cdf(k - 1, n, alpha)) < delta


def independent_rows(rows):
    """One prespecified representative per independent source group, selected without scores or labels.

    The certificate's population is these group-weighted representatives, not every wording. Other variants remain
    diagnostic evidence. A group identifier must encode all known shared source/patient/family dependencies; hashing
    does not establish that different groups are independent. Replicated or reordered rows cannot add sample size.
    """
    groups = defaultdict(list)
    for row in rows:
        group = row.get('group')
        if not isinstance(group, str) or not group.strip():
            raise ValueError('Calibration needs an explicit independent source group')
        groups[group].append(row)
    result = []
    for group, members in sorted(groups.items()):
        observations = {}
        for row in members:
            identity = json.dumps([row.get('tier'), group, row.get('source'), row.get('item'), row.get('question')],
                                  sort_keys=True, separators=(',', ':'))
            key = hashlib.sha256(('cascade-representative/1|' + identity).encode()).hexdigest()
            prior = observations.get(key)
            if prior is not None and (prior.get('score'), prior.get('error')) != (row.get('score'), row.get('error')):
                raise ValueError('Conflicting calibration outcomes for the same observation require review')
            observations[key] = row
        selected = dict(observations[min(observations)])
        selected['blind'] = any(r.get('blind') for r in members)
        result.append(selected)
    return result


def fit_all(alpha=C.DEFAULT_ALPHA, delta=C.DEFAULT_DELTA, write=False, out=None):
    rows = [json.loads(l) for l in ROUTE_SET.read_text().splitlines() if l.strip()] if ROUTE_SET.exists() else []
    reference_audit = {}
    rows += therapy_rows(audit=reference_audit)
    blind_groups = {r['group'] for r in rows if r.get('blind')}
    by_tier = defaultdict(list)
    for r in rows:
        if type(r.get('error')) is not bool:
            raise ValueError('An unresolved outcome cannot enter correctness calibration')
        # Missing scores remain abstentions before representative selection; dropping them would make selection
        # depend on score availability. Their identity participates just like every other settled-reference case.
        by_tier[r['tier']].append({**r, 'score': -1.0 if r.get('score') is None else r['score'],
                                  'unscored': r.get('score') is None,
                                  'blind': bool(r.get('blind') or r['group'] in blind_groups)})
    records = []
    for tier in C.ROUTE_TIERS + C.THERAPY_TIERS:
        raw = by_tier.get(tier, [])
        rs = independent_rows(raw)
        cal = [r for r in rs if not r.get('blind') and part(r['group']) == 'cal']
        test = [r for r in rs if not r.get('blind') and part(r['group']) == 'test']
        blind = [r for r in rs if r.get('blind')]
        scores = [r['score'] for r in cal]
        grid = sorted({s for s in scores if s >= 0}) or None
        data = {'labelled': len(rs), 'calibration': len(cal), 'heldout': len(test), 'blind': len(blind),
                'sources': dict(Counter(r['source'] for r in rs)), 'errors_all': sum(r['error'] for r in rs),
                'sampling': 'one label/score-independent hash-selected representative per independent source group',
                'raw_rows': len(raw), 'raw_errors': sum(r['error'] for r in raw),
                'unscored_rows': sum(r.get('unscored', False) for r in raw),
                'reference_exclusions': reference_audit}
        rec = C.fit(tier, scores, [r['error'] for r in cal], alpha=alpha, delta=delta, grid=grid, data=data,
                    score_definition=SCORE_DEFS.get(tier, 'route-level (1.0 for every claim); qualification applies to the declared representative population'))
        lam = rec['threshold']
        rec['heldout'] = C.evaluate([r['score'] for r in test], [r['error'] for r in test], lam, delta)
        rec['blind_test'] = C.evaluate([r['score'] for r in blind], [r['error'] for r in blind], lam, delta)
        # the operating point that would be in force: the certified threshold, else today's hand-set one
        op = lam if lam is not None else TODAY.get(tier, 0.0)
        ho = C.evaluate([r['score'] for r in test], [r['error'] for r in test], op, delta)
        bl = C.evaluate([r['score'] for r in blind], [r['error'] for r in blind], op, delta)
        if lam is None:
            rec['heldout_at_todays_threshold'] = {**ho, 'threshold': op}
            rec['blind_at_todays_threshold'] = {**bl, 'threshold': op}
        if not rs:
            rec['reason'] = 'no labelled calibration data for this tier; today\'s behaviour kept (uncalibrated)'
        elif refutes(ho, alpha, delta) or refutes(bl, alpha, delta):
            # held-out or blind data show errors significantly above alpha at the operating point: measured, not certifiable
            which = 'held-out' if refutes(ho, alpha, delta) else 'blind'
            t = ho if which == 'held-out' else bl
            rec.update(calibrated=False, ltt_threshold=lam, threshold=None, decision='defer',
                       reason=(f'Learn-then-Test certified {lam} on calibration but ' if lam is not None else
                               f'not certifiable on calibration; at today\'s threshold {op} ')
                       + f"the {which} data show {t['errors_accepted']}/{t['n_accepted']} wrong, significantly above alpha={alpha:.0%}: "
                         f'the tier defers')
        records.append(rec)
        t = rec['heldout']
        print(f"{tier:32s} {rec.get('decision', 'uncalibrated'):12s} thr={rec['threshold']} n={len(rs)} cal={len(cal)} "
              f"errs={data['errors_all']} | cal acc {rec.get('n_accepted')} err {rec.get('errors_accepted')} bound {rec.get('bound')} | "
              f"heldout {t['errors_accepted']}/{t['n_accepted']} of {t['n']} cov {t['coverage']} ub {t['error_upper_bound']}"
              + ('' if rec['calibrated'] else f" | {rec.get('reason', '')[:160]}"))
    meta = {'calibrator': VERSION, 'calibration_sets': {'routes': str(ROUTE_SET.relative_to(ROOT)), 'therapy': str(THERAPY_LABELS.relative_to(ROOT))},
            'split': {'calibration_share': CAL_SHARE, 'group': 'one trusted definition / question / candidate',
                      'sampling': 'one prespecified representative per independent group; exchangeability to that population remains an assumption',
                      'blind': 'groups with any blind member are evaluation only'}, 'reference_exclusions': reference_audit}
    report = therapy_report(records, summary_path=THERAPY_LABELS.with_name('therapy_barrier_reader_summary.json'))
    if report:
        meta['therapy_cascade'] = report
        print(json.dumps(report, indent=1))
    if write or out:
        C.write(records, out, meta=meta)
        print('wrote', out or C.FILE)
    return records


def therapy_report(records, summary_path=None, ledger_path=None):
    """Coverage and error per reader tier at the thresholds in force (certified, else hand-set), simulated over every
    labelled candidate in order (rules -> classifier -> Jev -> Perplexity -> Claude), and the typed-tier cost per 1,000
    text units (notes / documents / note facts) from the measured per-case costs of the labelling run."""
    from clinical_core.agere.clinical import therapy_barriers as TB
    if not THERAPY_LABELS.exists(): return None
    recs = {r['tier']: r for r in records}
    thr = lambda t, fb: (recs[t]['threshold'] if recs.get(t, {}).get('calibrated') else
                         float('inf') if recs.get(t, {}).get('decision') == 'defer' else fb)
    t1, t2, tj, tp = thr('tier1_rules', None), thr('tier2_classifier', None), thr('tier3_jev', TB.TYPED_ACCEPT), thr('tier3_perplexity', TB.TYPED_ACCEPT)
    cands = [json.loads(l) for l in THERAPY_LABELS.read_text().splitlines() if l.strip()]
    stats = {k: {'decided': 0, 'errors': 0, 'reached': 0, 'reference_compared': 0,
                 'unresolved_reference': 0, 'abstained': 0}
             for k in ('tier1_rules', 'tier2_classifier', 'tier3_jev', 'tier3_perplexity', 'tier3_claude')}
    for c in cands:
        ref = (c['labels'].get('claude') or {}).get('label')
        def decide(tier, label):
            if label in (None, 'unknown'):
                stats[tier]['abstained'] += 1
                return
            stats[tier]['decided'] += 1
            if ref in (None, 'unknown'):
                stats[tier]['unresolved_reference'] += 1
            else:
                stats[tier]['reference_compared'] += 1
                stats[tier]['errors'] += int(label != ref)
        stats['tier1_rules']['reached'] += 1
        if c['tier'] == 1 and c['decided_by'] == 'rules' and (t1 is None or (c.get('confidence') or 0) >= t1):
            decide('tier1_rules', c['label']); continue
        stats['tier2_classifier']['reached'] += 1
        p, lab = c.get('classifier_probability'), c.get('classifier_label')
        if p is not None:
            contradicted = c.get('student_assertion') in ('negated', 'other_person', 'retracted') and (c.get('student_probability') or 0) >= 0.5
            ok = (p >= t2 and not (contradicted and lab != 'no_barrier')) if t2 is not None else (
                (lab != 'no_barrier' and p >= TB.TIER2_ACCEPT and not contradicted) or (lab == 'no_barrier' and p >= TB.TIER2_REJECT))
            if ok:
                decide('tier2_classifier', lab); continue
        done = False
        for prov, tier, t in (('jev', 'tier3_jev', tj), ('perplexity', 'tier3_perplexity', tp)):
            stats[tier]['reached'] += 1
            got = c['labels'].get(prov) or {}
            if got.get('label') not in (None, 'unknown') and (got.get('probability') or 0) >= t:
                decide(tier, got['label']); done = True; break
        if not done:
            stats['tier3_claude']['reached'] += 1
            decide('tier3_claude', ref)
    n = len(cands)
    for k, v in stats.items():
        v['coverage_of_all'] = round(v['decided'] / n, 4) if n else None
        v['coverage_of_reached'] = round(v['decided'] / v['reached'], 4) if v['reached'] else None
        v['error_vs_claude'] = round(v['errors'] / v['reference_compared'], 4) if v['reference_compared'] else None
    summary = json.loads(Path(summary_path).read_text()) if summary_path and Path(summary_path).exists() else {}
    units = summary.get('text_units')
    led = json.loads(THERAPY_LABELS.with_suffix('.ledger.json').read_text()) if THERAPY_LABELS.with_suffix('.ledger.json').exists() else {}
    est = summary.get('tier3_estimate') or {}
    per_case = {'jev': (led.get('usd') or {}).get('jev', 0) / n if n else None,
                'perplexity': (led.get('usd') or {}).get('perplexity', 0) / n if n else None,
                # the product's Claude body (TYPED_LABELS, batched) from the reader's own token estimate
                'claude': (est.get('claude_opus_usd_if_all_escalate') or 0) / est['leftover_cases'] if est.get('leftover_cases') else None}
    cost = None
    if units and all(v is not None for v in per_case.values()):
        called = {'jev': tj != float('inf'), 'perplexity': tp != float('inf'), 'claude': True}  # a deferred provider is not called
        reach = {k: stats[f'tier3_{k}']['reached'] / n if called[k] else 0.0 for k in ('jev', 'perplexity', 'claude')}
        per_candidate = sum(reach[k] * per_case[k] for k in reach)
        cost = {'candidates_per_1000_units': round(1000 * n / units, 2), 'usd_per_candidate_typed_tier': round(per_candidate, 6),
                'usd_per_1000_units': round(1000 * n / units * per_candidate, 5),
                'usd_per_1000_units_if_all_candidates_went_to_claude': round(1000 * n / units * per_case['claude'], 5),
                'per_case_usd': {k: round(v, 7) for k, v in per_case.items()}, 'text_units': units, 'candidates': n,
                'basis': 'measured labelling-run ledger cost per case (Jev, Perplexity); Claude = reader tier3_estimate for its own body'}
    show = lambda v: 'deferred (not certified)' if v == float('inf') else v
    return {'thresholds_in_force': {'tier1_rules': show(t1), 'tier2_classifier': show(t2) if t2 is not None else f'hand-set {TB.TIER2_ACCEPT}/{TB.TIER2_REJECT}',
                                    'tier3_jev': show(tj), 'tier3_perplexity': show(tp)}, 'tiers': stats, 'cost': cost,
            'qualification': 'Agreement with settled reference labels only; unresolved references are ungraded, not correct negatives. This is not independent clinical truth.'}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('collect-offline'); p.add_argument('--workspace-root', required=True)
    p = sub.add_parser('collect-full'); p.add_argument('--workspace-root', required=True); p.add_argument('--limit', type=int)
    p.add_argument('--workers', type=int, default=3); p.add_argument('--without-definition', action='store_true')
    p.add_argument('--resume', action='store_true'); p.add_argument('--shard', help='i/n: ask only items with index %% n == i')
    sub.add_parser('collect-bench')
    p = sub.add_parser('collect-families'); p.add_argument('--semantic')
    p = sub.add_parser('fit'); p.add_argument('--alpha', type=float, default=C.DEFAULT_ALPHA); p.add_argument('--delta', type=float, default=C.DEFAULT_DELTA)
    p.add_argument('--write', action='store_true'); p.add_argument('--out')
    a = ap.parse_args(argv)
    if a.cmd == 'collect-offline': return collect_offline(a.workspace_root)
    if a.cmd == 'collect-full': return collect_full(a.workspace_root, a.limit, a.workers, a.without_definition, a.resume,
                                                  tuple(int(x) for x in a.shard.split('/')) if a.shard else None)
    if a.cmd == 'collect-bench': return collect_bench()
    if a.cmd == 'collect-families': return collect_families(a.semantic)
    return fit_all(a.alpha, a.delta, a.write, a.out)


if __name__ == '__main__':
    main()
