"""Certify every gate on Agere's answer paths with Learn-then-Test, from SAVED labelled data only.

For each gate in clinical_core/agere/clinical/gate_policy.GATES this assembles labelled cases (score, error) from data
already on disk, splits them deterministically, runs Learn-then-Test (conformal.ltt_threshold; alpha 2% error among
accepted, delta 10%) on the calibration part, checks the result on held-back data, and writes one record per gate into
calibrated_thresholds.json bound to the identity gate_policy.identity() computes (model weights, encoder, model id +
contract, or the route's code). Nothing calls a model or a paid API (--refresh-cache re-scores saved cases with the LOCAL
encoder only).

Data per gate
  route / flag gates ('<route>.answer', 'freeform_sql.verified')   bench result rows (bench/results/agere/*.jsonl): the
        gate path is read from each row's planner receipt with gate_policy.path_gates (the same function the runtime uses);
        error = the delivered answer was wrong (adjudicated engine_error, else the bench score; adjudicated
        defensible-definition / format-only rows count as right). Abstentions are not answers and are skipped.
  local_compiler.operation, operator_router.choice                  acceptance runs (bench/results/acceptance/*.json):
        error = the planned operations differ from the expected operation.
  economical_planner.decision, nl2sql.decider                       bench receipts of delivered picks (as above, with the
        pick's probability as the score).
  learned_router                                                    tools/learned_routing/router_report.json
        acceptance_rows (saved predictions of the committed model on the routing acceptance set, which training excluded);
        error = wrong engine; not_answerable predictions are never accepted.
  semantic_cache.similarity                                         tools/calibration/data/semantic_cache_cases.json
        (adversarial pairs, minimal-difference variants, paraphrases scored by the local encoder; error = reuse of a
        non-equivalent question). --refresh-cache rebuilds it with tools/calibration/fit_thresholds.cache_data.

Splits
  Blind sets (every bench split named heldout*) are NEVER calibration data: only the first run of each blind set (the run
  made while it was still blind) is used, and only as a blind test. Any non-blind question whose text also appears in a
  blind set is dropped. Non-blind cases are split by a hash of their group key (question / base question): 70%
  calibration, 30% test. Bench rows are de-duplicated to the latest run per question (the current code's behaviour).

A record is certified only when Learn-then-Test certifies a threshold on the calibration part AND the held-back test data
do not refute it (one-sided binomial test of the test errors among accepted at level delta). Otherwise the hand-set value
stays in force, and the record states how many labelled cases certification would need at the observed error rate (or
that the observed error rate is above alpha, so no amount of data certifies the gate until the error rate falls).

Usage: .venv/bin/python tools/certify_gates.py [--results DIR] [--refresh-cache] [--write] [--out FILE] [--only g1,g2]
"""
import argparse, hashlib, json, math, re, sys, time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import calibrated_thresholds as CT, gate_policy as GP  # noqa: E402
from clinical_core.agere.clinical.conformal import binomial_cdf  # noqa: E402

MAIN = Path('/Users/mikel/clinical-insights-local')
CACHE_CASES = ROOT / 'tools' / 'calibration' / 'data' / 'semantic_cache_cases.json'
ROUTER_REPORT = ROOT / 'tools' / 'learned_routing' / 'router_report.json'
CAL_SHARE = 0.7
VERSION = 'certify-gates/1'


def norm(q):
    return ' '.join(re.sub(r'[^a-z0-9%<>=.]+', ' ', str(q).lower()).split())


def is_blind(split):
    return str(split or '').startswith('heldout')


def part(key):
    """Deterministic calibration/test assignment by group key."""
    h = int(hashlib.sha256(('certify-gates|' + key).encode()).hexdigest()[:12], 16) / 16 ** 12
    return 'cal' if h < CAL_SHARE else 'test'


def results_dir(arg=None):
    if arg: return Path(arg)
    here = ROOT / 'bench' / 'results'
    return here if (here / 'agere').is_dir() else MAIN / 'bench' / 'results'  # a worktree has no saved runs: read main's


# ---- statistics -----------------------------------------------------------------------------------------------------
def upper_bound(k, n, delta):
    """One-sided Clopper-Pearson upper bound on an error rate: k errors in n, confidence 1 - delta."""
    if n == 0: return 1.0
    lo, hi = k / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if binomial_cdf(k, n, mid) > delta: lo = mid
        else: hi = mid
    return hi


def accepted_needed(err, alpha, delta, cap=1_000_000):
    """Smallest number of ACCEPTED labelled cases at which an observed error rate `err` certifies alpha at delta
    (k = round(err * n) errors); None when err >= alpha (more data cannot certify; the error rate must fall)."""
    if err >= alpha: return None
    ok = lambda n: binomial_cdf(int(round(err * n)), n, alpha) <= delta
    n = 1
    while n <= cap and not ok(n): n *= 2
    if n > cap: return None
    lo, hi = max(1, n // 2), n
    while lo < hi:
        mid = (lo + hi) // 2
        if ok(mid): hi = mid
        else: lo = mid + 1
    while lo > 1 and ok(lo - 1): lo -= 1  # the floor/round makes the curve slightly non-monotone
    return lo


def evaluate(cases, threshold, alpha, delta):
    acc = [c for c in cases if threshold is not None and c['score'] is not None and c['score'] >= threshold]
    k = sum(c['error'] for c in acc)
    out = {'n': len(cases), 'n_accepted': len(acc), 'errors_accepted': k,
           'error_rate': round(k / len(acc), 4) if acc else None,
           'error_upper_bound': round(upper_bound(k, len(acc), delta), 4) if acc else None}
    # refuted when the errors among accepted are significantly above alpha: P(Bin(n, alpha) >= k) < delta
    out['refutes_alpha'] = bool(acc) and k > 0 and (1 - binomial_cdf(k - 1, len(acc), alpha)) < delta
    return out


# ---- data -----------------------------------------------------------------------------------------------------------
def asserted(answer):
    return isinstance(answer, dict) and answer.get('answer_type') not in (None, 'unsupported') \
        and answer.get('status') in ('answered', 'partial')


def bench_rows(results):
    """-> (calibration-pool rows: latest per question from non-blind runs, blind rows: first run per blind set,
    blind question set). Each row gets 'wrong' from adjudication or score."""
    sys.path.insert(0, str(ROOT))
    from tools.quality import defects as D
    runs = [r for r in D.load_runs(results) if r['system'] == 'agere']
    adj = D.load_adjudications(results)
    def label(run, row):
        cats = {i['id']: i['category'] for i in (adj.get(run['file']) or {}).get('items') or []}
        if row['id'] in cats: return cats[row['id']] == 'engine_error', 'adjudicated'
        return not row['score'].get('correct'), 'score'
    blind_q, first_blind, latest = set(), {}, {}
    for run in sorted(runs, key=lambda r: r['started']):
        for row in run['rows']:
            if is_blind(row.get('split')):
                blind_q.add(norm(row['question']))
                first_blind.setdefault(row.get('split'), run['file'])
    blind_rows = []
    for run in runs:
        for row in run['rows']:
            if is_blind(row.get('split')):
                if first_blind.get(row.get('split')) == run['file']:
                    wrong, how = label(run, row)
                    blind_rows.append({**row, 'wrong': wrong, 'label_source': how, 'run': run['file']})
            else:
                wrong, how = label(run, row)
                latest[norm(row['question'])] = {**row, 'wrong': wrong, 'label_source': how, 'run': run['file']}
    pool = [r for q, r in latest.items() if q not in blind_q]
    return pool, blind_rows, blind_q


def route_cases(rows):
    """gate -> [case] from bench rows via the runtime's own gate path."""
    out = defaultdict(list)
    for row in rows:
        if not asserted(row.get('answer')): continue
        if (row.get('expected') or {}).get('answer_type') == 'unsupported': continue  # unanswerable: abstention is the right answer
        receipt = (row.get('response') or {}).get('planner_receipt')
        for g in GP.path_gates(receipt) or []:
            kind = GP.GATES[g['gate']][0]
            if kind == 'score':
                score = g['score']
            elif kind == 'flag':
                score = 1.0 if g['passed'] else 0.0
            else:
                score = 1.0
            if score is None: continue
            out[g['gate']].append({'key': norm(row['question']), 'score': float(score), 'error': bool(row['wrong']),
                                   'run': row['run'], 'label_source': row['label_source']})
    return out


def store_cases(files=None, blind_q=()):
    """learned_reuse.answer cases from store audits (clinical/store_audit.py): gold labels (bench / corrections) and pairs both
    System One families judged the same way. Blind questions are dropped."""
    paths = [Path(f) for f in files] if files else sorted(MAIN.glob('artifacts/*/stack/store-audit-cases.json'))
    out, seen = [], set()
    for p in paths:
        try: doc = json.loads(p.read_text())
        except (OSError, ValueError): continue
        for c in doc.get('cases') or []:
            if c['key'] in blind_q or c['key'] in seen: continue
            seen.add(c['key'])
            out.append({'key': c['key'], 'score': float(c.get('score', 1.0)), 'error': bool(c['error']), 'run': str(p),
                        'label_source': c.get('label_source')})
    return out


def acceptance_cases(results, blind_q):
    latest = {}
    for path in sorted((Path(results) / 'acceptance').glob('*.json')):
        try: doc = json.loads(path.read_text())
        except (OSError, ValueError): continue
        for row in doc if isinstance(doc, list) else doc.get('rows') or []:
            if isinstance(row, dict) and row.get('question') and 'pass' in row:
                latest[norm(row['question'])] = {**row, 'run': path.name}
    out = defaultdict(list)
    gate_of = {'local_compiler': 'local_compiler.operation', 'operator_router': 'operator_router.choice'}
    for q, row in latest.items():
        if q in blind_q or row.get('route') not in gate_of: continue
        out[gate_of[row['route']]].append({'key': q, 'score': 1.0, 'error': not row['pass'], 'run': row['run'], 'label_source': 'acceptance'})
    return out


def router_cases(blind_q):
    from clinical_core.agere.clinical import learned_router as LR
    model = LR.load()
    report = json.loads(ROUTER_REPORT.read_text())
    same = model is not None and abs((report.get('heldout') or {}).get('threshold', -1) - model['threshold']) < 1e-12
    cases = []
    for row in report.get('acceptance_rows') or []:
        if norm(row['question']) in blind_q: continue
        eligible = row['predicted'] in LR.SHORTCUT
        cases.append({'key': norm(row['question']), 'score': float(row['probability']) if eligible else -1.0,
                      'error': row['predicted'] != row['expect'], 'label_source': 'router_report acceptance_rows'})
    return cases, same, report.get('trained_at')


def cache_cases(refresh=False):
    if refresh:
        sys.path.insert(0, str(ROOT / 'tools' / 'calibration'))
        import fit_thresholds as F
        scores, errors, data, identity, meta = cache_data_with_cases(F)
        CACHE_CASES.parent.mkdir(parents=True, exist_ok=True)
        CACHE_CASES.write_text(json.dumps({'encoder_identity': identity, 'written_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                           'source': data['source'], 'cases': meta}, indent=0))
    if not CACHE_CASES.exists(): return None, None
    doc = json.loads(CACHE_CASES.read_text())
    return [{'key': norm(c['base']), 'score': c['score'], 'error': c['error'], 'label_source': 'semantic_cache_eval'}
            for c in doc['cases']], doc['encoder_identity']


def cache_data_with_cases(F):
    """fit_thresholds.cache_data, keeping each case's base question (the split groups by base)."""
    import random, shutil, sqlite3, tempfile
    import semantic_cache_eval as E
    from clinical_core.agere.clinical import learning, semantic_cache as SC
    from clinical_core.local_encoders import LocalEncoder
    stack = F.LIVE
    rng = random.Random(11)
    run = json.loads((stack / 'run.json').read_text())
    db = sqlite3.connect(f'file:{stack / "learning.sqlite3"}?mode=ro', uri=True)
    live = sorted({q for (q,) in db.execute("SELECT question FROM verified WHERE plan_kind='sql'")}); db.close()
    live = [q for q in live if not SC.is_benchmark(q) and len(q) < 220]; rng.shuffle(live)
    cases = [{**p} for p in json.loads((ROOT / 'tools' / 'learned_routing' / 'adversarial_pairs.json').read_text())]
    for q in E.SYNTHETIC_BASES + live[:200]:
        cases += [{'base': q, 'variant': v, 'reuse': False} for v, _ in E.minimal_differences(q, rng)]
        cases += [{'base': q, 'variant': f(q), 'reuse': True} for f in rng.sample(E.PARAPHRASES, 3) if f(q) != q]
    meta = []
    with tempfile.TemporaryDirectory() as tmp:
        encoder = LocalEncoder(run['encoder_path'], pooling='mean', cache=Path(tmp) / 'enc.sqlite3')
        by_base = defaultdict(list)
        for c in cases: by_base[c['base']].append(c)
        for base, items in by_base.items():
            d = Path(tmp) / 'stack'
            if d.exists(): shutil.rmtree(d)
            d.mkdir(); (d / 'run.json').write_text((stack / 'run.json').read_text())
            learning.record_verified(d, base, 'sql', {'query': 'SELECT 1'}, {}, 'hosted_verified_sql', [], 'calibration')
            for c in items:
                hit, info = SC.lookup(d, c['variant'], encoder, threshold=-1.0)
                if (info.get('reason') or '').startswith('exact'): continue
                meta.append({'base': base, 'variant': c['variant'], 'reuse': bool(c['reuse']),
                             'score': info['similarity'] if hit is not None else -1.0,
                             'error': bool(hit is not None and not c['reuse'])})
        identity = encoder.identity
        encoder.close()
    return None, None, {'source': 'semantic_cache_eval cases (adversarial + minimal differences + paraphrases; benchmarks excluded)'}, identity, meta


# ---- certification --------------------------------------------------------------------------------------------------
def needs_analysis(cases, kind, probe, floor, alpha, delta, min_accepted=10):
    """How many labelled cases certification would need. Route/flag gates: at their single operating point. Score gates:
    at the threshold (>= floor, >= min_accepted accepted cases) that needs the fewest labelled cases given the observed
    error rate there; with no threshold under alpha the gate cannot be certified by more data alone."""
    total = len(cases)
    if kind == 'score':
        points = sorted({c['score'] for c in cases if c['score'] >= max(floor or 0.0, 0.0)}) or [probe]
    else:
        points = [probe]
    best = None
    for t in points:
        acc = [c for c in cases if c['score'] >= t]
        if kind == 'score' and len(acc) < min(min_accepted, total) and t != probe: continue
        err = sum(c['error'] for c in acc) / len(acc) if acc else 0.0
        rate = len(acc) / total if total else None
        need = accepted_needed(err, alpha, delta)
        labelled = math.ceil(need / rate / CAL_SHARE) if need is not None and rate else None
        cand = {'threshold': t, 'n_accepted': len(acc), 'error_rate': round(err, 4), 'acceptance_rate': round(rate, 4) if rate is not None else None,
                'accepted_needed': need, 'labelled_needed': labelled}
        if best is None or (labelled is not None and (best['labelled_needed'] is None or labelled < best['labelled_needed'])):
            best = cand
    out = {'observed': best, 'accepted_needed': best['accepted_needed'], 'labelled_needed': best['labelled_needed']}
    where = f" at threshold {best['threshold']:.4g}" if kind == 'score' else ''
    if best['n_accepted'] < min_accepted:  # too few accepted cases to estimate an error rate: state the zero-error floor
        need = accepted_needed(0.0, alpha, delta)
        out.update(accepted_needed=need, labelled_needed=math.ceil(need / CAL_SHARE))
        out['needs'] = (f"only {best['n_accepted']} accepted labelled case(s) ({round(best['error_rate'] * best['n_accepted'])} wrong); "
                        f"needs >= {need} accepted calibration cases with zero errors (~{out['labelled_needed']} labelled cases with "
                        f"the 70/30 split), more if any are wrong")
        return out
    if best['accepted_needed'] is None:
        out['needs'] = (f"observed error among accepted {best['error_rate']:.1%}{where} >= alpha {alpha:.0%} at every threshold with "
                        f">= {min_accepted} accepted: more data alone cannot certify this gate; its error rate must fall below {alpha:.0%}")
    elif best['labelled_needed'] is None:
        out['needs'] = (f"needs >= {best['accepted_needed']} accepted calibration cases; none of the {total} labelled cases is accepted "
                        f"at the operating point yet, so the labelled total cannot be estimated")
    else:
        out['needs'] = (f"needs >= {best['accepted_needed']} accepted calibration cases at the observed {best['error_rate']:.1%} error"
                        f"{where} (~{best['labelled_needed']} labelled cases in all at {best['acceptance_rate']:.0%} acceptance with the "
                        f"70/30 split; have {total})")
    return out


def certify(name, cases, blind=(), *, bind, fallback, data, floor=None, alpha=None, delta=None):
    alpha = CT.TARGET['alpha'] if alpha is None else alpha
    delta = CT.TARGET['delta'] if delta is None else delta
    kind = GP.GATES[name][0]
    cal = [c for c in cases if part(c['key']) == 'cal']
    test = [c for c in cases if part(c['key']) == 'test']
    grid = [1.0] if kind in ('route', 'flag') else sorted({c['score'] for c in cal if c['score'] >= 0})
    rec = CT.fit(name, [c['score'] for c in cal], [c['error'] for c in cal], fallback,
                 {**data, 'labelled': len(cases), 'calibration': len(cal), 'test': len(test), 'blind_test': len(blind)},
                 alpha=alpha, delta=delta, grid=grid, floor=floor, bind=bind)
    rec.update(kind=kind, error_definition=GP.GATES[name][2], certifier=VERSION, split={'calibration_share': CAL_SHARE,
               'blind_sets': 'heldout* splits: first (blind) run only, test-only; never calibration'})
    lam = rec['threshold']
    probe = lam if lam is not None else (fallback if kind == 'score' else 1.0)
    rec['test'] = evaluate(test, probe, alpha, delta)
    rec['blind_test'] = evaluate(list(blind), probe, alpha, delta)
    if lam is not None and (rec['test']['refutes_alpha'] or rec['blind_test']['refutes_alpha']):
        which = 'held-out test' if rec['test']['refutes_alpha'] else 'blind test'
        rec.update(calibrated=False, threshold=None, ltt_threshold=lam,
                   reason=f"Learn-then-Test certified {lam} on calibration, but the {which} refutes the {alpha:.0%} bound "
                          f"({rec['test' if which == 'held-out test' else 'blind_test']['errors_accepted']} errors among "
                          f"{rec['test' if which == 'held-out test' else 'blind_test']['n_accepted']} accepted); hand-set {fallback} kept")
        rec.pop('guarantee', None)
    if not rec['calibrated']:
        rec.update(needs_analysis(cal + test, kind, probe, floor, alpha, delta))
        for which in ('blind_test', 'test'):
            t = rec[which]
            if t['refutes_alpha'] and kind == 'score':  # at the hand-set threshold only; a stricter one may still certify
                rec['needs'] += (f"; at the hand-set threshold {probe} the {which.replace('_', ' ')} shows {t['errors_accepted']}/"
                                 f"{t['n_accepted']} wrong")
                break
            if t['refutes_alpha']:  # held-back data already show the error rate is above alpha: data volume is not the gap
                rec['labelled_needed'] = None
                rec['needs'] = (f"the {which.replace('_', ' ')} shows {t['errors_accepted']}/{t['n_accepted']} wrong "
                                f"({t['error_rate']:.0%}, significantly above {alpha:.0%}); more calibration data cannot certify this "
                                f"gate until its error rate falls (non-blind estimate: {rec['needs']})")
                break
        head = rec.get('reason') if lam is not None else (f"not certifiable at alpha={alpha:.0%}, delta={delta:.0%} from "
                                                           f"{len(cal)} calibration cases; hand-set {fallback} kept")
        rec['reason'] = head + '; ' + rec['needs']
    else:
        rec['guarantee'] = CT.guarantee_text(rec) + (f"; held-out test {rec['test']['errors_accepted']}/{rec['test']['n_accepted']} wrong "
                                                     f"(upper bound {rec['test']['error_upper_bound']})")
    if not rec['calibrated']:  # graded certificate: the strictest pre-declared alpha the data certify, unless held-back data refute it
        g = CT.fit_graded([c['score'] for c in cal], [c['error'] for c in cal], delta, grid, floor)
        if g:
            t, b = evaluate(test, g['threshold'], g['alpha'], delta), evaluate(list(blind), g['threshold'], g['alpha'], delta)
            g.update(test=t, blind_test=b)
            if t['refutes_alpha'] or b['refutes_alpha']:
                rec['graded_refuted'] = {**g, 'reason': f"graded alpha {g['alpha']:.0%} refuted by the {'held-out test' if t['refutes_alpha'] else 'blind test'}"}
            else:
                g['guarantee'] += f"; held-out test {t['errors_accepted']}/{t['n_accepted']}, blind {b['errors_accepted']}/{b['n_accepted']} wrong"
                rec['graded'] = g
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--results', default=None)
    ap.add_argument('--refresh-cache', action='store_true')
    ap.add_argument('--write', action='store_true'); ap.add_argument('--out', default=None)
    ap.add_argument('--only', default='')
    ap.add_argument('--store-cases', action='append', default=None,
                    help='store-audit-cases.json files (store_audit.py) adding labelled learned_reuse.answer cases; default: every stack')
    a = ap.parse_args(argv)
    results = results_dir(a.results)
    only = set(filter(None, a.only.split(',')))
    pool, blind_rows, blind_q = bench_rows(results)
    by_route = route_cases(pool)
    blind_by_route = route_cases(blind_rows)
    by_accept = acceptance_cases(results, blind_q)
    records = []

    def want(name): return not only or name in only

    if want('learned_router'):
        from clinical_core.agere.clinical import learned_router as LR
        cases, same, trained = router_cases(blind_q)
        model = LR.load()
        bind = GP.identity('learned_router') if same else 'report-model-mismatch'
        records.append(certify('learned_router', cases, bind=bind, fallback=(model or {}).get('threshold'),
                               data={'source': f'router_report.json acceptance_rows (model trained {trained}); '
                                               + ('report matches the committed model' if same else 'REPORT DOES NOT MATCH THE COMMITTED MODEL')}))
    if want('semantic_cache.similarity'):
        from clinical_core.agere.clinical import semantic_cache as SC
        cases, enc = cache_cases(a.refresh_cache)
        if cases is None:
            records.append({**CT.fit('semantic_cache.similarity', [], [], SC.SIMILARITY, {'source': 'no saved per-case data'}),
                            'reason': f'no saved cases at {CACHE_CASES}; run with --refresh-cache (local encoder only)'})
        else:
            records.append(certify('semantic_cache.similarity', cases, bind=enc, fallback=SC.SIMILARITY, floor=0.80,
                                   data={'source': f'{CACHE_CASES.relative_to(ROOT)} (semantic_cache_eval cases; benchmarks excluded)'}))
    for name in ('economical_planner.decision', 'nl2sql.decider'):
        if want(name):
            records.append(certify(name, by_route.get(name, []), blind_by_route.get(name, []), bind=GP.identity(name),
                                   fallback=GP.GATES[name][1],
                                   data={'source': 'bench receipts of delivered picks, non-blind runs (latest per question); '
                                                   'error = delivered answer wrong'}))
    for name in ('local_compiler.operation', 'operator_router.choice'):
        if want(name):
            records.append(certify(name, by_accept.get(name, []), bind=GP.identity(name), fallback=1.0,
                                   data={'source': 'acceptance runs (bench/results/acceptance, latest per question); error = wrong operation'}))
    store = store_cases(a.store_cases, blind_q)
    for name, (kind, fallback, _) in GP.GATES.items():
        if kind in ('route', 'flag') and name not in ('local_compiler.operation', 'operator_router.choice') and want(name):
            extra = store if name == 'learned_reuse.answer' else []
            records.append(certify(name, by_route.get(name, []) + extra, blind_by_route.get(name, []), bind=GP.identity(name), fallback=fallback,
                                   data={'source': 'bench rows, development/challenge runs (latest per question); '
                                                   'error = delivered answer wrong (adjudicated where available)'}))
    for r in records:
        status = 'CERTIFIED' if r.get('calibrated') else (f"graded {r['graded']['alpha']:.0%}" if r.get('graded') else 'not certified')
        t = r.get('test') or {}
        print(f"{r['name']:30s} {status:13s} thr={r.get('threshold')} n={(r.get('data') or {}).get('labelled')} "
              f"cal={r.get('n_calibration')} | test {t.get('errors_accepted')}/{t.get('n_accepted')} | "
              f"blind {(r.get('blind_test') or {}).get('errors_accepted')}/{(r.get('blind_test') or {}).get('n_accepted')} | "
              f"{r.get('guarantee') or r.get('reason')}")
    if a.write or a.out:
        if only:  # keep the other gates' records
            existing = (CT._load(a.out or None).get('gates') or {})
            records = [existing[k] for k in existing if k not in {r['name'] for r in records}] + records
        CT.write(records, a.out)
    return records


if __name__ == '__main__':
    main()
