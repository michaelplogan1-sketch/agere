"""Run the frozen pilot set through the live product (/api/stack/compose) and write the scorer's submission file.

    .venv/bin/python bench/pilot_run.py --out tmp/pilot/P1 [--split dev|calibration|all] [--workers 3]
    python3 -m ecosystem.eval_scorer --release releases/healthcare-warehouse-v2.8 --pilot tmp/pilot/P1/answers.jsonl \
        --sample-release <v2.8 sample dir> [--per-question]     (run from the generator repo)

Each question is asked the way a customer would ask it: the question followed by its stated definition. The product's
answer is mapped to the pilot response format without reinterpretation: answered/partial -> answered (count, value,
patient_ids or table), unsupported -> unsupported (missing), clarify -> clarify (readings), anything else -> abstain.
Every response, cost and latency is kept next to the submission.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.adapters import post_json  # noqa: E402

PILOT = Path('/Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v2.8/questions/pilot_set.jsonl')


def submission(q, answer):
    a = answer if isinstance(answer, dict) else {}
    status = a.get('status')
    row = {'id': q['id']}
    if status == 'clarify':
        return row | {'status': 'clarify', 'readings': a.get('readings') or a.get('missing') or []}
    if status == 'unsupported':
        return row | {'status': 'unsupported', 'missing': a.get('missing') or []}
    if status not in ('answered', 'partial'):
        return row | {'status': 'abstain'}
    kind = q['response_format']
    if kind in ('count', 'value'):
        v = a.get(kind)
        if v is None:
            v = a.get('value' if kind == 'count' else 'count')
        return row | ({'status': 'answered', kind: v} if v is not None else {'status': 'abstain'})
    if kind == 'patient_set':
        ids = a.get('patient_ids')
        if ids is None and isinstance(a.get('table'), list):
            ids = [r.get('patient_id') for r in a['table'] if isinstance(r, dict) and r.get('patient_id')]
        return row | ({'status': 'answered', 'patient_ids': ids} if ids is not None else {'status': 'abstain'})
    table = a.get('table')
    if not isinstance(table, list):
        return row | {'status': 'abstain'}
    rows = []
    for r in table:
        if not isinstance(r, dict) or not r:
            continue
        if 'keys' in r and 'value' in r:
            rows.append({'key': ' -> '.join(str(k) for k in r['keys']), 'value': r['value']})
            continue
        nums = [k for k, v in r.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        keys = [k for k in r if k not in nums] or [next(iter(r))]
        value_col = [k for k in nums if k not in keys]
        rows.append({'key': ' -> '.join(str(r[k]) for k in keys), 'value': r[value_col[-1]] if value_col else None})
    return row | {'status': 'answered', 'table': rows}


def ask(q, url):
    text = q['question'] + (' Definition: ' + q['definition'] if q.get('definition') else '')
    t0 = time.time()
    try:
        resp = post_json(url.rstrip('/') + '/api/stack/compose', {'question': text}, timeout=900)
        err = None
    except Exception as exc:  # noqa: BLE001 - a failed call is an abstention, recorded
        resp, err = {}, f'{type(exc).__name__}: {exc}'[:400]
    econ = resp.get('planning_economics') or {}
    return {'id': q['id'], 'seconds': round(time.time() - t0, 1), 'error': err,
            'cost_usd': econ.get('new_api_estimate_usd'), 'paid_calls': resp.get('new_paid_api_calls'),
            'decision': (resp.get('together') or {}).get('decision'), 'answer': resp.get('answer'),
            'route': resp.get('route')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--split', default='all', choices=('dev', 'calibration', 'all'))
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--url', default='http://127.0.0.1:8885')
    ap.add_argument('--questions', type=Path, help='another question file ({id, question, answer_type}); default: the pilot set')
    a = ap.parse_args()
    qs = [json.loads(l) for l in (a.questions or PILOT).read_text().splitlines() if l.strip()]
    for q in qs:
        q.setdefault('definition', '')
        q.setdefault('response_format', q.get('answer_type'))
    qs = [q for q in qs if a.split == 'all' or q.get('split', a.split) == a.split]
    a.out.mkdir(parents=True, exist_ok=True)
    done = {}
    log = a.out / 'responses.jsonl'
    if log.exists():  # resumable: questions already answered are not asked again
        for line in log.read_text().splitlines():
            r = json.loads(line)
            if not r.get('error'):
                done[r['id']] = r
    todo = [q for q in qs if q['id'] not in done]
    with ThreadPoolExecutor(max_workers=a.workers) as pool, open(log, 'a') as f:
        for r in pool.map(lambda q: ask(q, a.url), todo):
            f.write(json.dumps(r, default=str) + '\n'); f.flush()
            done[r['id']] = r
            print(r['id'], r['seconds'], r['decision'], (r['answer'] or {}).get('status'), r['error'] or '', flush=True)
    by_id = {q['id']: q for q in qs}
    subs = [submission(by_id[i], done[i]['answer']) for i in by_id if i in done]
    (a.out / 'answers.jsonl').write_text(''.join(json.dumps(s, default=str) + '\n' for s in subs))
    costs = [done[i]['cost_usd'] for i in by_id if i in done]
    print(json.dumps({'questions': len(subs), 'answered': sum(s['status'] == 'answered' for s in subs),
                      'cost_known_usd': round(sum(c for c in costs if isinstance(c, (int, float))), 4),
                      'cost_unknown': sum(1 for c in costs if c is None)}))


if __name__ == '__main__':
    main()
