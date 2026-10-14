"""Replay verification v2 (independent candidates, oracles, capture gate, readings) over saved blind-run receipts.

No model is called. For the latest adjudicated full run of each blind set (M, N, O), every answer that took the hosted
verified-SQL route is replayed from its saved planner receipt: the hosted candidates' SQL (primary / second / judge) is
re-executed read-only against the live stack's semantic layer, the deterministic compiler candidate is compiled and
executed, the oracles run on the delivered query, and verification_v2.decide() is applied. Labels come from the saved
adjudications (engine_error = wrong) and the bench score (correct = right). The bottled local model is NOT run here
(CPU budget), so its contribution is not measured.

Usage: .venv/bin/python tools/quality/verification_replay.py [--results DIR] [--stack DIR] [--out FILE]
Reads bench/results and the stack read-only (sqlite mode=ro; cohort result cache disabled); writes only --out.
"""
import argparse, copy, json, os, sys, time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
os.environ['AGERE_RESULT_CACHE'] = 'off'  # cohort selection must not write into the read-only stack

from tools.quality import defects as D  # noqa: E402
from clinical_core.agere.clinical import sql_query, oracles, verification_v2 as v2, answer_checks, learning, verify  # noqa: E402

LIVE = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')


def readonly(stack):
    target = stack / 'semantic.sqlite3'
    sql_query.semantic_layer.ensure = lambda path: target  # never rebuild inside the read-only stack
    run = json.loads((stack / 'run.json').read_text())
    from clinical_core.agere.clinical import source_profile
    profile = json.loads((stack / 'source-profile.json').read_text())
    if profile.get('schema') != source_profile.VERSION or profile.get('source_digest') != run['source_digest']:
        raise SystemExit('source profile is stale; refusing to rebuild it inside the read-only stack')
    source_profile._CACHE[(str(stack), run['source_digest'])] = profile
    return profile, run


def execute(stack, sql, question):
    """Result shaped like the ladder's check(): scalar -> value, patient list -> patient_ids, else rows."""
    probe = sql_query.execute(stack, {'query': sql, 'returns': 'table'})
    rows, cols = probe.get('rows') or [], probe['columns']
    if len(rows) == 1 and len(cols) == 1: return {'value': next(iter(rows[0].values())), 'columns': cols}
    if cols[:1] == ['patient_id'] and len(cols) == 1: return {'patient_ids': sorted({r['patient_id'] for r in rows if r['patient_id']})}
    return probe


def hosted_candidates(receipt):
    """Last valid attempt per role (repairs replace the first try)."""
    by_role = {}
    for a in receipt.get('attempts') or []:
        if a.get('sql') and not a.get('error') and not a.get('missing'): by_role[a['role']] = a
    return by_role


def replay(results, stack, out):
    profile, run = readonly(stack)
    as_of = run['snapshot']['as_of']
    runs = D.load_runs(results); adj = D.load_adjudications(results)
    metrics = [D.run_metrics(r, adj.get(r['file'])) for r in runs]
    latest = {m['set']: m for m in metrics if m['system'] == 'agere' and m['split'] == 'blind' and m['full'] and m['adjudicated']}
    by_file = {r['file']: r for r in runs}
    ex = oracles.readonly_executor(stack)
    items, started = [], time.time()
    for s, m in sorted(latest.items()):
        cats = {i['id']: i['category'] for i in adj[m['file']].get('items') or []}
        for row in by_file[m['file']]['rows']:
            if D.route(row) != 'hosted_verified_sql' or not D.answerable(row): continue
            pr = row['response']['planner_receipt']; hr = pr.get('hosted_sql') or {}
            label = 'wrong' if cats.get(row['id']) == 'engine_error' else 'right' if row['score'].get('correct') else (cats.get(row['id']) or 'unadjudicated')
            q = row['question']; answer = row.get('answer') or {}
            item = {'set': s, 'id': row['id'], 'label': label, 'agreement': hr.get('agreement'), 'question': q[:200]}
            cands = []
            for order, (role, a) in enumerate(sorted(hosted_candidates(hr).items(), key=lambda kv: ['primary', 'second', 'judge'].index(kv[0]) if kv[0] in ('primary', 'second', 'judge') else 9)):
                try: res = execute(stack, a['sql'], q)
                except Exception as exc: item.setdefault('replay_errors', []).append(f'{role}: {str(exc)[:100]}'); continue  # noqa: BLE001
                cands.append({'label': role, 'family': 'hosted', 'params': {'query': a['sql'], 'returns': 'table'}, 'result': res, 'warnings': [], 'order': order})
            comp = None
            try:
                c = v2.compiler_candidate(stack, q, as_of, profile)
                if c and c.get('sql'): c['result'] = execute(stack, c['sql'], q); c['params'] = {'query': c['sql'], 'returns': c.get('returns') or 'table'}
                if c: comp = {**c, 'family': 'compiler', 'order': 10, 'warnings': []}
            except Exception as exc:  # noqa: BLE001
                item['compiler_error'] = str(exc)[:120]
            item['compiler_candidate'] = bool(comp)
            delivered = next((c for c in cands if c['params']['query'] == answer.get('query')), None)
            # Oracles + high-precision checks on the delivered query (what reached the reader).
            a2 = copy.deepcopy(answer); a2.pop('quality_checks', None); a2['warnings'] = []
            answer_checks.annotate(a2, q, row.get('as_of') or as_of, execute=ex)
            checks = [c for c in a2.get('quality_checks') or [] if c.get('control') != 'error']
            item['controls'] = sorted({c['control'] + ('' if c.get('severity') == 'warning' else ':info') for c in checks})
            blocking = v2.blocking_findings(checks)
            item['blocking'] = sorted({c['control'] for c in blocking})
            # Lint as it stands now (the v2 ladder would reject the query and repair or judge).
            item['lint'] = verify.lint(profile, answer.get('query') or '') if answer.get('query') else []
            item['policy'] = {name: simulate(hr.get('agreement') or '', cands, comp, delivered, blocking, item['lint'], route)
                              for name, route in (('default', False), ('compiler_dissent_routes', True))}
            item.update({k: v for k, v in item['policy']['default'].items() if k != '_chosen'})
            chosen = item['policy']['default'].get('_chosen')
            for pol in item['policy'].values(): pol.pop('_chosen', None)
            # FM-19 readings: definitional disagreement between the delivered answer and another candidate.
            pool = cands + ([comp] if comp else [])
            if chosen is not None:
                alt, diff = v2.alternative_reading(chosen, pool)
                if alt: item['readings'] = {'differs_by': [x['dimension'] for x in diff['dimensions']], 'alt': alt['labels']}
            # A definitional alternative is shown to the reader (QC-READINGS warning): it counts as a visible flag.
            for pol in item['policy'].values():
                pol['visible'] = pol['caught'] or bool(item.get('readings'))
            item['visible'] = item['policy']['default']['visible']
            # FM-23 capture gate (what capture_verified would do with this answer).
            hp = sorted({c['control'] for c in checks if c.get('severity') == 'warning' and c.get('control') in learning.QUARANTINE_CONTROLS})
            item['capture'] = 'quarantined' if hp or item.get('readings') or item.get('dissent') or item['lint'] or item['caught'] else 'recorded'
            items.append(item)
            print(f"{s} {row['id']} {label:<22} {hr.get('agreement') or '':<16} compiler={'y' if comp else '-'} v2={item['v2']:<50} blocking={item['blocking']} capture={item['capture']}", flush=True)
    return summarize(items, time.time() - started)


def simulate(agreement, cands, comp, delivered, blocking, lint, compiler_routes):
    """What the v2 ladder does with one saved answer. caught = routed to the judge or delivered with a warning."""
    os.environ['AGERE_COMPILER_DISSENT'] = 'route' if compiler_routes else 'capture'
    out = {'extra_judge_call': False, 'second_call_saved': False}
    hosted_pair = [c for c in cands if c['label'] in ('primary', 'second')]
    if agreement == 'primary=second':
        d = v2.decide(hosted_pair + ([comp] if comp else []), oracle=lambda c: blocking)
        out['v2'] = d['status'] + ':' + d['basis'][:70]
        out['second_call_saved'] = bool(comp and hosted_pair and v2.same_answer(hosted_pair[0]['result'], comp['result']))
        out['caught'] = d['status'] != 'accepted' or bool(lint)
        out['extra_judge_call'] = d['status'] == 'disputed'
        out['caught_by'] = ('lint' if lint else d['basis']) if out['caught'] else None
        out['dissent'] = d.get('dissent') or []
        out['_chosen'] = d.get('winner')
    else:  # disputed in the original run too: the judge's answer stands; caught when it is delivered with a warning
        judge = next((c for c in cands if c['label'] == 'judge'), delivered)
        dissent = comp is not None and judge is not None and not v2.same_answer(judge['result'], comp['result'])
        out['v2'] = 'judged' + (':independent_dissent' if dissent else ':independent_support' if comp else '') + (':flagged' if blocking else '')
        out['caught'] = bool(blocking) or bool(lint) or (dissent and compiler_routes)
        out['caught_by'] = ('lint' if lint else 'oracle ' + ','.join(sorted({f['control'] for f in blocking})) if blocking else
                            'compiler dissent' if dissent and compiler_routes else None)
        out['dissent'] = ['compiler'] if dissent else []
        out['_chosen'] = judge
    os.environ.pop('AGERE_COMPILER_DISSENT', None)
    return out


def _rate(k, n): return f'{k}/{n}' + (f' ({100 * k / n:.0f}%)' if n else '')


def policy_summary(items, name):
    wrong = [i for i in items if i['label'] == 'wrong']; right = [i for i in items if i['label'] == 'right']
    pol = lambda i: i['policy'][name]
    return {'caught_wrong': sum(pol(i)['caught'] for i in wrong), 'wrong': len(wrong),
            'false_alarms_right': sum(pol(i)['caught'] for i in right), 'right': len(right),
            'coverage_lost': round(sum(pol(i)['caught'] for i in right) / len(right), 4) if right else None,
            'caught_by': dict(Counter(pol(i)['caught_by'] for i in wrong if pol(i)['caught'])),
            'false_alarm_by': dict(Counter(pol(i)['caught_by'] for i in right if pol(i)['caught'])),
            'caught_or_readings_wrong': sum(pol(i)['visible'] for i in wrong), 'caught_or_readings_right': sum(pol(i)['visible'] for i in right),
            'extra_judge_calls': sum(pol(i)['extra_judge_call'] for i in items),
            'second_calls_saved': sum(pol(i)['second_call_saved'] for i in items)}


def summarize(items, seconds):
    wrong = [i for i in items if i['label'] == 'wrong']; right = [i for i in items if i['label'] == 'right']
    other = [i for i in items if i['label'] not in ('wrong', 'right')]
    controls = defaultdict(lambda: {'wrong': 0, 'right': 0, 'other': 0})
    for i in items:
        for c in set(i['controls']):
            controls[c]['wrong' if i['label'] == 'wrong' else 'right' if i['label'] == 'right' else 'other'] += 1
    oracle_table = {}
    for c, v in sorted(controls.items()):
        fired = v['wrong'] + v['right']
        oracle_table[c] = {**v, 'precision_wrong_vs_right': round(v['wrong'] / fired, 3) if fired else None,
                           'recall_of_wrong': round(v['wrong'] / len(wrong), 3) if wrong else None,
                           'false_alarm_rate_on_right': round(v['right'] / len(right), 3) if right else None}
    report = {
        'schema': 'verification-v2-replay/1', 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'seconds': round(seconds, 1),
        'scope': 'latest adjudicated full run of blind sets M, N, O; hosted_verified_sql route only; saved receipts re-executed read-only; '
                 'no model called; bottled local model not run',
        'hosted_answers': len(items), 'wrong_engine_errors': len(wrong), 'right': len(right), 'other_failures': len(other),
        'caught_wrong': sum(i['caught'] for i in wrong), 'false_alarms_right': sum(i['caught'] for i in right),
        'coverage_lost': round(sum(i['caught'] for i in right) / len(right), 4) if right else None,
        'escape_rate_before': 1.0, 'escape_rate_after': round(1 - sum(i['caught'] for i in wrong) / len(wrong), 4) if wrong else None,
        'caught_by': dict(Counter(i['caught_by'] for i in wrong if i['caught'])),
        'false_alarm_by': dict(Counter(i['caught_by'] for i in right if i['caught'])),
        'extra_judge_calls': sum(i.get('extra_judge_call', False) for i in items),
        'second_calls_saved': sum(i.get('second_call_saved', False) for i in items),
        'compiler_candidates': sum(i['compiler_candidate'] for i in items),
        'capture': {'wrong_recorded_before': len(wrong), 'wrong_recorded_after': sum(i['capture'] == 'recorded' for i in wrong),
                    'right_recorded_before': len(right), 'right_recorded_after': sum(i['capture'] == 'recorded' for i in right)},
        'readings': {'on_defensible_definition': sum(1 for i in other if i['label'] == 'defensible_definition' and i.get('readings')),
                     'defensible_definition_total': sum(1 for i in other if i['label'] == 'defensible_definition'),
                     'on_wrong': sum(1 for i in wrong if i.get('readings')), 'on_right': sum(1 for i in right if i.get('readings'))},
        'policies': {name: policy_summary(items, name) for name in ('default', 'compiler_dissent_routes')},
        'lint_now_rejects': {'wrong': sum(bool(i['lint']) for i in wrong), 'right': sum(bool(i['lint']) for i in right)},
        'controls': oracle_table, 'items': items}
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--results', default=str(ROOT / 'bench' / 'results')); ap.add_argument('--stack', default=str(LIVE))
    ap.add_argument('--out', default=str(ROOT / 'docs' / 'agere' / 'verification_v2_replay.json'))
    a = ap.parse_args(argv)
    report = replay(Path(a.results), Path(a.stack), Path(a.out))
    Path(a.out).write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != 'items'}, indent=1, default=str))


if __name__ == '__main__':
    main()
