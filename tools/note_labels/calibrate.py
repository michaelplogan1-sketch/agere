"""Measure System One on the labeled passages and fit per-frame calibration.

    .venv/bin/python -m tools.note_labels.calibrate collect --providers jev perplexity --workers 4   # live calls, ledger-cached
    .venv/bin/python -m tools.note_labels.calibrate fit                                              # writes config/agere/note_frames_calibration.json

collect: for every labeled item whose two passes agree on the status, one status question per provider (the exact question and
options note_questions sends), plus, for single-choice detail fields both passes agree on, one detail question. Raw provider
probabilities are stored in results.jsonl next to the labels (resumable; an item/provider/task already stored is skipped).
fit: per (frame, field, provider) a per-option log-odds shift (system_one.fit_calibration, NLL + L2, leave-one-out reported) and a
threshold chosen as the lowest value on a 0.50..0.98 grid whose accepted predictions (leave-one-out calibrated probability >=
threshold) are at least TARGET precise; the file also records the combined Jev -> Perplexity pipeline: coverage, precision of what
it accepts, and what it leaves undecided. Accuracy is reported raw and calibrated.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from clinical_core.agere.clinical import note_frames as nf
from clinical_core.agere.clinical import note_questions as nq
from clinical_core.agere.clinical import system_one

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGET = 0.95
GRID = [round(0.50 + 0.02 * i, 2) for i in range(25)]
AS_OF = '2030-05-27'
LOCK = threading.Lock()


def predicate_for(item):
    return nf.validate_predicate({'op': 'note', 'frame': item['frame'], 'concept': {'term': item['concept']['term']},
                                  'statuses': ['documented']})


def passage_of(item):
    return {'passage_id': f"{item['record_id']}@{item['start']}-{item['end']}", 'record_id': item['record_id'], 'text': item['text'],
            'date': item['date'], 'note_type': item['note_type']}


def jobs():
    """[(item_id, task, question, options, gold, passage, predicate)] for every agreed status and agreed single-choice detail."""
    out = []
    for line in (HERE / 'labeled.jsonl').open():
        r = json.loads(line)
        if not r['status_agreed']:
            continue
        pred, passage = predicate_for(r), passage_of(r)
        out.append((r['id'], nf.task_key(r['frame']), nq.status_question(pred, passage), dict(nf.STATUSES), r['status'], passage, pred))
        spec = nf.FRAMES[r['frame']]['details']
        for field, val in r['detail'].items():
            if spec[field]['kind'] == 'choice' and isinstance(val, str):
                out.append((r['id'], nf.task_key(r['frame'], field), nq.detail_question(pred, passage, field),
                            {o: nq.humanize(o) for o in spec[field]['options']}, val, passage, pred))
    return out


def collect(providers, workers, ledger, results_path, decide=None):
    decide = decide or system_one.decide
    done = set()
    if results_path.exists():
        for line in results_path.open():
            x = json.loads(line)
            done.add((x['id'], x['task'], x['provider']))
    todo = [(j, p) for j in jobs() for p in providers if (j[0], j[1], p) not in done]
    print(f'{len(todo)} calls to make ({len(done)} stored)', file=sys.stderr)

    def run(item):
        (iid, task, q, opts, gold, passage, pred), prov = item
        ev = [{'id': passage['passage_id'], 'text': passage['text'], 'date': passage['date'], 'note_id': passage['record_id']}]
        try:
            r = decide(q, opts, ev, as_of=AS_OF, order=(prov,), threshold=0.0, task_key=None, ledger=ledger, allow_live=True)
        except Exception as exc:  # noqa: BLE001
            return {'id': iid, 'task': task, 'provider': prov, 'error': f'{type(exc).__name__}: {str(exc)[:160]}', 'gold': gold}
        if not r.get('label') or r.get('status') in ('prepared', 'providers_failed'):
            return {'id': iid, 'task': task, 'provider': prov, 'error': r.get('status') or 'no_label', 'gold': gold}
        return {'id': iid, 'task': task, 'provider': prov, 'gold': gold, 'label': r['label'], 'probabilities': r['probabilities'],
                'cost_usd': r.get('cost_usd'), 'cache_hit': r.get('cache_hit')}

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as pool, results_path.open('a') as f:
        for res in pool.map(run, todo):
            with LOCK:
                f.write(json.dumps(res, sort_keys=True) + '\n')
    errs = sum(1 for _ in results_path.open() if '"error"' in _)
    print(f'done; {errs} stored errors', file=sys.stderr)


def _top(p):
    return max(p, key=p.get)


def threshold_for(rows, shift):
    """Lowest grid threshold whose accepted rows (calibrated top prob >= t, top label not unknown) are >= TARGET precise.
    -> (threshold, coverage, precision) or (None, ...) when no grid value reaches the target."""
    cal = [(system_one._shifted(r['probabilities'], shift), r['label']) for r in rows]
    best = None
    for t in GRID:
        acc = [(_top(q), g) for q, g in cal if q[_top(q)] >= t and _top(q) != 'unknown']
        if not acc:
            continue
        prec = sum(a == g for a, g in acc) / len(acc)
        if prec >= TARGET:
            return t, len(acc) / len(cal), prec
        best = best or (None, len(acc) / len(cal), prec)
    return best or (None, 0.0, None)


def fit_task(rows):
    """rows: [{probabilities, label(gold)}] for one (task, provider). -> params dict incl. before/after accuracy."""
    raw = sum(_top(r['probabilities']) == r['label'] for r in rows) / len(rows)
    labels = sorted({k for r in rows for k in r['probabilities']})
    shift = system_one._fit_shift(rows, labels, 0.01, 400, 1.0)
    params = {'ece_before': system_one.ece(rows), 'ece_after_loo': None}
    # leave-one-out calibrated predictions for an honest "after"
    loo = []
    for i, r in enumerate(rows):
        s = system_one._fit_shift(rows[:i] + rows[i + 1:], labels, 0.01, 400, 1.0) if len(rows) > 3 else {}
        loo.append({'probabilities': system_one._shifted(r['probabilities'], s), 'label': r['label']})
    params['ece_after_loo'] = system_one.ece(loo)
    cal_acc = sum(_top(r['probabilities']) == r['label'] for r in loo) / len(loo)
    thr, cov, prec = threshold_for(loo, {})
    return {'n': len(rows), 'accuracy_raw': round(raw, 4), 'accuracy_calibrated_loo': round(cal_acc, 4), 'shift': shift,
            'threshold': thr if thr is not None else nf.DEFAULT_THRESHOLD, 'threshold_met_target': thr is not None,
            'coverage_at_threshold': round(cov, 4), 'precision_at_threshold': None if prec is None else round(prec, 4),
            'ece_before': params and params.get('ece_before'), 'ece_after_loo': params and params.get('ece_after_loo')}


def fit(results_path, out_path):
    by = {}
    for line in results_path.open():
        x = json.loads(line)
        if 'error' in x or x['gold'] not in x['probabilities']:
            continue
        by.setdefault((x['task'], x['provider']), []).append({'probabilities': x['probabilities'], 'label': x['gold'], 'id': x['id']})
    tasks = {}
    for (task, prov), rows in sorted(by.items()):
        if len(rows) < 8:
            continue
        tasks.setdefault(task, {})[prov] = fit_task(rows)
    # combined pipeline on status tasks: Jev accepted at its threshold else Perplexity at its threshold
    pipeline = {}
    for task, provs in tasks.items():
        if not task.endswith(':status') or 'jev' not in provs:
            continue
        j = {r['id']: r for r in by[(task, 'jev')]}
        p = {r['id']: r for r in by.get((task, 'perplexity'), [])}
        acc = und = 0
        right = 0
        for iid, r in j.items():
            q = system_one._shifted(r['probabilities'], provs['jev']['shift'])
            lab, ok = _top(q), q[_top(q)] >= provs['jev']['threshold'] and _top(q) != 'unknown'
            if not ok and iid in p and 'perplexity' in provs:
                q = system_one._shifted(p[iid]['probabilities'], provs['perplexity']['shift'])
                lab, ok = _top(q), q[_top(q)] >= provs['perplexity']['threshold'] and _top(q) != 'unknown'
            if ok:
                acc += 1
                right += lab == r['label']
            else:
                und += 1
        pipeline[task] = {'n': len(j), 'accepted': acc, 'undecided': und, 'precision_accepted': round(right / acc, 4) if acc else None,
                          'accuracy_all_counting_undecided_wrong': round(right / len(j), 4)}
    out = {'schema': 'note-frames-calibration/1', 'target_precision': TARGET, 'tasks': tasks, 'pipeline': pipeline,
           'fitted_on': 'tools/note_labels/labeled.jsonl (status-agreed items; two blind passes)'}
    for task in tasks.values():
        for prov, p in task.items():
            p['source'] = {'provider': prov}
    Path(out_path).write_text(json.dumps(out, indent=1, sort_keys=True) + '\n')
    for task, provs in tasks.items():
        for prov, p in provs.items():
            print(f"{task:36s} {prov:10s} n={p['n']:3d} raw={p['accuracy_raw']:.3f} cal_loo={p['accuracy_calibrated_loo']:.3f} "
                  f"thr={p['threshold']:.2f} cov={p['coverage_at_threshold']:.2f} prec={p['precision_at_threshold']}")
    for task, p in pipeline.items():
        print(f'PIPELINE {task:30s} {p}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['collect', 'fit'])
    ap.add_argument('--providers', nargs='+', default=['jev', 'perplexity'])
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--results', default=str(HERE / 'results.jsonl'))
    ap.add_argument('--out', default=str(ROOT / 'config/agere/note_frames_calibration.json'))
    ap.add_argument('--ledger', default=None)
    a = ap.parse_args()
    if a.cmd == 'collect':
        have = system_one.available()
        missing = [p for p in a.providers if not have.get(p)]
        if missing:
            sys.exit(f'no API key in the environment for {missing} (system_one.available() = {have}); nothing to collect')
        collect(a.providers, a.workers, a.ledger, Path(a.results))
    else:
        fit(Path(a.results), a.out)


if __name__ == '__main__':
    main()
