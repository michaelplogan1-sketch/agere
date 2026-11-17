"""Takeover-rung cost trial (2026-11-17): eval/tiered/trial_takeover_v1.json, each question sent straight to takeover.answer
in three arms, one run each, journaled as scored (a rerun answers only what is missing). Claude via the subscription only.

  today      the loop as it is
  compact    AGERE_TIERED_TAKEOVER_COMPACT=on
  batch      compact + AGERE_TIERED_TAKEOVER_BATCH=on

    PYTHONPATH=. python eval/tiered/trial_takeover.py run|report
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.pop('ANTHROPIC_API_KEY', None)
from clinical_core.agere.providers import load_keys  # noqa: E402
load_keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
from clinical_core.agere.tiered import customer, proof, store, takeover  # noqa: E402

SET = Path('eval/tiered/trial_takeover_v1.json')
J = Path('artifacts/agere-tiered-core/trials/takeover-journal.jsonl')
ARMS = {'today': {}, 'compact': {'AGERE_TIERED_TAKEOVER_COMPACT': 'on'},
        'batch': {'AGERE_TIERED_TAKEOVER_COMPACT': 'on', 'AGERE_TIERED_TAKEOVER_BATCH': 'on'},
        'with_definitions': {}}   # added 2026-11-17 after 46c43e71: today's loop, now given the customer and governed definitions


def items():
    data = json.loads(SET.read_text())
    onboard = Path(tempfile.mkdtemp(prefix='takeover-')) / 'customer'; onboard.mkdir()
    shutil.copy(data['customer_definitions'], onboard / Path(data['customer_definitions']).name)
    customer.DIRECTORY = onboard; store.clear_caches()
    spec = importlib.util.spec_from_file_location('refs', Path(data['reference_module'])); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    table = {n[4:]: f for n, f in vars(mod).items() if n.startswith('ref_') and callable(f)}
    import sqlite3
    db = sqlite3.connect(store.path().resolve().as_uri() + '?mode=ro', uri=True)
    out = []
    for q in data['questions']:
        refs = [] if q.get('unanswerable') else [[list(r) for r in table[q['reference']](db, *q.get('args', []))]]
        out.append({'id': q['id'], 'question': q['phrasings'][0], 'domain': q['domain'], 'unanswerable': q.get('unanswerable', False),
                    'reference': refs[0] if refs else None, 'references': refs, 'reading_refs': {}})
    return out


def run():
    done = {(r['id'], r['arm']) for r in map(json.loads, J.read_text().splitlines())} if J.exists() else set()
    J.parent.mkdir(parents=True, exist_ok=True)
    os.environ['AGERE_PROOF_STRICT'] = 'on'
    for it in items():
        for arm, env in ARMS.items():
            if (it['id'], arm) in done: continue
            for k in ('AGERE_TIERED_TAKEOVER_COMPACT', 'AGERE_TIERED_TAKEOVER_BATCH'): os.environ.pop(k, None)
            os.environ.update(env)
            try:
                o = takeover.answer(it['question'])
            except Exception as exc:
                if 'limit' in str(exc).lower(): print('STOPPED AT A PROVIDER LIMIT:', str(exc)[:200]); return
                o = None
            outcome = {'status': 'answered' if o and o.get('rows') else 'declined', 'rows': (o or {}).get('rows')}
            steps = (o or {}).get('steps') or []
            rec = {'id': it['id'], 'arm': arm, 'domain': it['domain'], 'unanswerable': it['unanswerable'], 'kind': proof.outcome_kind(it, outcome),
                   'usd': sum(s.get('usd', 0.0) for s in steps), 'turns': len(steps), 'queries': len((o or {}).get('queries') or []),
                   'cache_read': sum(s.get('cache_read', 0) for s in steps), 'cache_write': sum(s.get('cache_write', 0) for s in steps),
                   'uncached': sum(s.get('input', 0) for s in steps), 'no_answer': o is None, 'rows': ((o or {}).get('rows') or [])[:3]}
            with open(J, 'a') as f: f.write(json.dumps(rec, default=str) + '\n')
            print(it['id'], arm, rec['kind'], f"${rec['usd']:.3f}", rec['turns'], 'turns', flush=True)


def report():
    sys.path.insert(0, 'eval/tiered')
    import trial_stats as T
    rs = [json.loads(l) for l in J.read_text().splitlines() if l.strip()]
    by = {}
    for r in rs: by.setdefault(r['id'], {})[r['arm']] = r
    full = {q: v for q, v in by.items() if len(v) >= 3}
    V = {'right': 1, 'wrong': -10}
    for arm in [a for a in ARMS if all(a in v for v in full.values())]:
        xs = [full[q][arm] for q in full]; c = Counter(x['kind'] for x in xs); n = len(xs)
        print(f"{arm:8} {n} questions: right {c['right']}, wrong {c['wrong']}, declined {n - c['right'] - c['wrong']}; RS(10) {sum(V.get(x['kind'], 0) for x in xs)}; "
              f"${sum(x['usd'] for x in xs) / n:.4f} an answer; {sum(x['turns'] for x in xs) / n:.2f} turns; "
              f"tokens an answer: cache read {sum(x['cache_read'] for x in xs) // n}, cache write {sum(x['cache_write'] for x in xs) // n}, uncached {sum(x['uncached'] for x in xs) // n}")
    for arm in ('compact', 'batch'):
        print(T.line(f'cost an answer, {arm} minus today', T.cluster_ci([(full[q]['today']['domain'], full[q][arm]['usd'] - full[q]['today']['usd']) for q in full]), ' $'))
        print(T.line(f'RS(10), {arm} minus today', T.cluster_ci([(full[q]['today']['domain'], V.get(full[q][arm]['kind'], 0) - V.get(full[q]['today']['kind'], 0)) for q in full])))


if __name__ == '__main__':
    {'run': run, 'report': report}[sys.argv[1]]()
