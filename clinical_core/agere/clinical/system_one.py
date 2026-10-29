"""System One: typed Jev / Perplexity judgments as one shared primitive, before any Claude model.

  choice()          one typed Choice call (PersistentGateway.classify; cached by request hash in the provider ledger).
  decide()          local healthcare student first (free, only above its own threshold); then the task's bake-off order
                    (default Jev, then Perplexity when Jev is below threshold, says 'unknown' or errors). Never raises.
  typed_question()  question-only judgment (evidence=[]), for checker/router builders.
  available()       which provider keys are present (values never read out).
  calibrate() / fit_calibration()  per-task log-odds shift: one scalar per (task_key, option), NLL-fit, identity when unfit.
  attribute()       deletion attribution (arXiv 2610.27099): necessity = P(label|doc) - P(label|doc minus sentence),
                    sufficiency = P(label|sentence alone) - P(label|empty). Batched, cache hits free.
allow_live=False never calls a provider: a 'prepared' result carries the request hash (cache hits still answer).
Calibration lives in config/agere/system_one_calibration.json (AGERE_SYSTEM_ONE_CALIBRATION overrides the path).
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from ...providers import EvidenceTask
from ..gateway import PersistentGateway, BudgetLedger, default_ledger_path

VERSION = 'system-one/1'
ROOT = Path(__file__).resolve().parents[3]
PROVIDERS = ('jev', 'perplexity')
KEY_ENV = {'jev': 'TYPESAFE_API_KEY', 'perplexity': 'PERPLEXITY_API_KEY'}
UNKNOWN = 'Insufficient, contradictory or unavailable evidence'
_EPS = 1e-6
_LOCK = threading.Lock()


def calibration_path():
    return Path(os.environ.get('AGERE_SYSTEM_ONE_CALIBRATION') or ROOT / 'config/agere/system_one_calibration.json')


def available():
    """{'jev': bool, 'perplexity': bool}: whether each provider's key is configured. Values are never returned."""
    return {p: bool(os.environ.get(KEY_ENV[p])) for p in PROVIDERS}


# -- one typed call --------------------------------------------------------------------------------------------------
def _gateway(ledger, allow_live):
    return PersistentGateway(ledger, allow_live=allow_live)


def _open_ledger(ledger):
    """(ledger, owned): a BudgetLedger passes through; a path or None opens one (closed by the caller)."""
    if isinstance(ledger, BudgetLedger) or (ledger is not None and hasattr(ledger, 'db')):
        return ledger, False
    return BudgetLedger(Path(ledger) if ledger else default_ledger_path()), True


def _options(options):
    opts = {str(k): str(v) for k, v in dict(options).items()}
    if 'unknown' not in opts:  # heal, never reject: every typed choice can say the data is absent
        opts['unknown'] = UNKNOWN
    return opts


def _evidence(evidence, as_of):
    out, seen = [], set()
    for i, e in enumerate(evidence or []):
        eid = str(e.get('id') or e.get('record_id') or f'e{i}')
        if eid in seen:
            eid = f'{eid}#{i}'
        seen.add(eid)
        observed=e.get('date') or e.get('event_date')
        # The typed task already carries its snapshot cutoff separately. Keep
        # the source clock explicitly unknown within its existing string schema.
        out.append({'id': eid, 'text': str(e.get('text') or ''), 'date':str(observed)[:10] if observed else 'unknown',
                    'note_id': str(e.get('note_id') or e.get('record_id') or eid)})
    return out


def _result(raw, provider, task_key):
    probabilities = raw.get('probabilities') or {}
    cost = raw.get('cost_estimate') or {}
    return {'label': raw.get('label'), 'probabilities': probabilities,
            'calibrated_probabilities': calibrate(task_key, probabilities, provider=provider) if task_key else dict(probabilities),
            'evidence_ids': list(raw.get('evidence_ids') or []), 'provider': provider, 'model': raw.get('model'),
            'cost_usd': 0.0 if raw.get('cache_hit') else cost.get('estimated_usd'), 'cache_hit': bool(raw.get('cache_hit')),
            'request_hash': raw.get('request_hash'), 'task_key': task_key}


def choice(question, options, evidence, *, as_of, provider='jev', ledger=None, allow_live=True, task_key=None, select_source=True,
           long_context=False):
    """One typed Choice. Returns {'label','probabilities','calibrated_probabilities','evidence_ids','provider','model',
    'cost_usd','cache_hit','request_hash'}; with allow_live=False and no cached answer, status='prepared' and label None.
    Provider/request errors raise (decide() turns them into steps)."""
    if provider not in PROVIDERS:
        raise ValueError(f'System One provider must be one of {PROVIDERS}')
    as_of = str(as_of)[:10]
    date.fromisoformat(as_of)
    task = EvidenceTask(str(question), as_of, _options(options), _evidence(evidence, as_of),select_source=select_source,long_context=long_context)
    handle, owned = _open_ledger(ledger)
    try:
        raw = _gateway(handle, allow_live).classify(provider, task)
    finally:
        if owned:
            handle.close()
    if raw.get('status') == 'prepared':
        return {'status': 'prepared', 'label': None, 'probabilities': {}, 'calibrated_probabilities': {}, 'evidence_ids': [],
                'provider': provider, 'model': PersistentGateway.MODELS[provider], 'cost_usd': 0.0, 'cache_hit': False,
                'request_hash': raw.get('request_hash'), 'task_key': task_key}
    return _result(raw, provider, task_key)


def decide(question, options, evidence, *, as_of, order=None, threshold=0.8, task_key=None, ledger=None, allow_live=True,
           select_source=True,long_context=False):
    """Cheapest typed provider first; the next one only when the previous is below threshold (calibrated), says 'unknown',
    or fails. Returns the accepted (else best) choice plus 'decided_by' (None when nobody cleared the bar), 'steps' and
    total 'cost_usd'. Never raises for a provider failure: when every provider fails the label is 'unknown'.
    order=None takes the task's decider bake-off order (system_one_deciders; default Jev -> Perplexity). Ahead of every paid
    provider, the task's local healthcare student (system_one_student) decides when it clears its own cross-fitted
    threshold ($0); a paid decision that clears the bar is kept as a teacher label for that student."""
    from . import system_one_deciders, system_one_student
    if order is None:
        order = system_one_deciders.order_for(task_key)
    steps, results = [], []
    student = None
    try:
        student = system_one_student.predict(task_key, question, _options(options), evidence,as_of=as_of) if task_key else None
    except Exception as exc:  # noqa: BLE001 - the student is an optimization; providers still answer
        steps.append({'provider': 'local_student', 'status': 'error', 'error': f'{type(exc).__name__}: {str(exc)[:200]}'})
    if student and student.get('label'):
        steps.append({'provider': 'local_student', 'status': 'answered', 'label': student['label'],
                      'probability': student['probabilities'][student['label']], 'threshold': student['threshold'], 'cost_usd': 0.0,
                      'cache_hit': False, 'request_hash': None})
        return {'label': student['label'], 'probabilities': student['probabilities'], 'calibrated_probabilities': student['probabilities'],
                'evidence_ids': [], 'provider': 'local_student', 'model': student['reading']['model'], 'cost_usd': 0.0,
                'cache_hit': False, 'request_hash': None, 'task_key': task_key, 'decided_by': 'local_student', 'steps': steps}
    if student and student.get('reading'):
        steps.append({'provider': 'local_student', 'status': 'below_threshold', **student['reading'], 'cost_usd': 0.0})
    for provider in order:
        try:
            r = choice(question, options, evidence, as_of=as_of, provider=provider, ledger=ledger, allow_live=allow_live,
                       task_key=task_key,select_source=select_source,long_context=long_context)
        except Exception as exc:  # one provider failing never walls the caller out
            steps.append({'provider': provider, 'status': 'error', 'error': f'{type(exc).__name__}: {str(exc)[:200]}',
                          **getattr(exc,'provider_receipt',{})})
            continue
        p = r['calibrated_probabilities'].get(r['label']) if r['label'] else None
        steps.append({'provider': provider, 'status': r.get('status', 'answered'), 'label': r['label'], 'probability': p,
                      'cost_usd': r['cost_usd'], 'cache_hit': r['cache_hit'], 'request_hash': r['request_hash']})
        if r.get('status') == 'prepared':
            results.append(r)
            continue
        results.append(r)
        if r['label'] != 'unknown' and p is not None and p >= threshold:
            if task_key: system_one_student.record(task_key,question,_options(options),evidence,r['label'],p,provider,
                                                  as_of=as_of,model=r.get('model'),request_hash=r.get('request_hash'))
            return {**r, 'decided_by': provider, 'steps': steps, 'cost_usd': _total(steps)}
    answered = [r for r in results if r.get('status') != 'prepared']
    if answered:
        known = [r for r in answered if r['label'] != 'unknown']
        best = max(known or answered, key=lambda r: r['calibrated_probabilities'].get(r['label'], 0))
        return {**best, 'decided_by': None, 'steps': steps, 'cost_usd': _total(steps)}
    if results:
        return {**results[0], 'status': 'prepared', 'decided_by': None, 'steps': steps, 'cost_usd': 0.0}
    return {'label': 'unknown', 'probabilities': {}, 'calibrated_probabilities': {}, 'evidence_ids': [], 'provider': None,
            'model': None, 'cost_usd': 0.0, 'cache_hit': False, 'request_hash': None, 'task_key': task_key,
            'status': 'providers_failed', 'decided_by': None, 'steps': steps}


def _total(steps):
    return sum(s.get('cost_usd') or 0.0 for s in steps)


def judge_fields(subject,questions,evidence,*,as_of,ledger=None,allow_live=True,threshold=.8,order=None,long_context=False):
    """Independent named Choices share one state; only unresolved fields fall back.

    This does not fit calibration, create physician labels, or certify a complete
    clinical answer. Each accepted field retains its actual provider/threshold.
    """
    if not questions:return {'judgments':{},'steps':[],'cost_usd':0.0}
    pending=dict(questions);accepted={};steps=[];observed={}
    for provider in (PROVIDERS if order is None else order):
        task=EvidenceTask(str(subject),str(as_of)[:10],{'unknown':UNKNOWN},_evidence(evidence,as_of),
                          select_source=False,long_context=long_context,questions=pending)
        handle,owned=_open_ledger(ledger)
        try:
            raw=_gateway(handle,allow_live).classify(provider,task)
            if raw.get('status')=='prepared':
                steps.append({'provider':provider,'status':'prepared','cost_usd':0.0,'request_hash':raw.get('request_hash')});continue
            usd=0.0 if raw.get('cache_hit') else (raw.get('cost_estimate') or {}).get('estimated_usd')
            steps.append({'provider':provider,'status':'partial' if raw.get('invalid_fields') or raw.get('unexpected_fields') else 'answered',
                          'invalid_fields':raw.get('invalid_fields')or{},'unexpected_fields':raw.get('unexpected_fields')or[],
                          'cost_usd':usd,'cache_hit':bool(raw.get('cache_hit')),
                          'request_hash':raw.get('request_hash'),'fields':list(pending)})
            for key,result in raw['judgments'].items():
                p=result['probabilities'].get(result['label'],0)
                row={**result,'probability':p,'provider':provider,'model':raw.get('model'),'decided_by':None,'threshold':threshold}
                observed[key]=row
                if result['label']!='unknown' and p>=threshold:
                    accepted[key]={**row,'decided_by':provider}
            pending={k:v for k,v in pending.items() if k not in accepted}
            if not pending:break
        except Exception as exc:
            steps.append({'provider':provider,'status':'error','error':f'{type(exc).__name__}: {str(exc)[:200]}',
                          **getattr(exc,'provider_receipt',{})})
        finally:
            if owned:handle.close()
    return {'judgments':{k:accepted.get(k) or observed.get(k) or {'label':'unknown','decided_by':None} for k in questions},
            'steps':steps,'cost_usd':_total(steps),'scope':'Per-field model judgments, not independent clinical acceptance'}


def typed_question(question, options, *, as_of, order=None, threshold=0.8, task_key=None, ledger=None, allow_live=True):
    """Question-only typed judgment (no evidence), e.g. 'is this question about a medication?' for checkers/routers."""
    return decide(question, options, [], as_of=as_of, order=order, threshold=threshold, task_key=task_key, ledger=ledger,
                  allow_live=allow_live)


# -- calibration: per-task log-odds shift ----------------------------------------------------------------------------
def _load():
    path = calibration_path()
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {'schema': 'system-one-calibration/1', 'tasks': {}}


def _shifted(probabilities, shift):
    logits = {k: math.log(min(max(float(p), _EPS), 1.0)) + float(shift.get(k, 0.0)) for k, p in probabilities.items()}
    top = max(logits.values())
    z = sum(math.exp(v - top) for v in logits.values())
    return {k: math.exp(v - top) / z for k, v in logits.items()}


def calibrate(task_key, probabilities, *, params=None, provider=None):
    """Shift each option's log-probability by its fitted scalar and renormalize. Identity when the task has no params,
    or when the params were fit on a different provider than `provider` (a Jev shift never recalibrates Perplexity)."""
    if not probabilities:
        return {}
    params = params or (_load().get('tasks', {}).get(task_key) or {} if task_key else {})
    fit_on = (params.get('source') or {}).get('provider') if isinstance(params.get('source'), dict) else None
    shift = params.get('shift')
    if not shift or (provider and fit_on and provider != fit_on):
        return dict(probabilities)
    return _shifted(probabilities, shift)


def ece(rows, *, shift=None, bins=10):
    """Top-label expected calibration error over rows [{probabilities, label}]."""
    if not rows:
        return None
    buckets = [[0, 0.0, 0.0] for _ in range(bins)]
    for r in rows:
        q = _shifted(r['probabilities'], shift or {})
        top = max(q, key=q.get)
        b = buckets[min(int(q[top] * bins), bins - 1)]
        b[0] += 1; b[1] += q[top]; b[2] += 1.0 if top == r['label'] else 0.0
    return sum(abs(c - a) for n, c, a in buckets if n) / len(rows)


def _nll(rows, shift):
    return sum(-math.log(max(_shifted(r['probabilities'], shift)[r['label']], _EPS)) for r in rows) / len(rows)


def _fit_shift(rows, labels, l2, steps, lr):
    shift = {k: 0.0 for k in labels}
    for _ in range(steps):  # gradient of mean NLL wrt b_k is mean(q_k - y_k); convex, so plain descent converges
        grad = {k: l2 * shift[k] for k in labels}
        for r in rows:
            q = _shifted(r['probabilities'], shift)
            for k in q:
                grad[k] += (q[k] - (1.0 if k == r['label'] else 0.0)) / len(rows)
        for k in labels:
            shift[k] -= lr * grad[k]
        if max(abs(g) for g in grad.values()) < 1e-7:
            break
    mean = sum(shift.values()) / len(shift)  # shifts are defined up to a constant; store them centered
    return {k: round(v - mean, 6) for k, v in shift.items()}


def fit_calibration(task_key, rows, *, l2=0.01, steps=2000, lr=0.5, save=True, source=None):
    """Fit one log-odds shift per option by minimizing mean NLL (+ tiny L2 toward identity) on labeled rows
    [{probabilities, label}]. Returns {'shift', 'n', 'ece_before', 'ece_after', 'nll_before', 'nll_after', ...} plus, for
    n <= 200, leave-one-out 'ece_after_loo' / 'nll_after_loo' (each row scored by a shift fit without it). With save=True
    it is written under tasks[task_key] in the calibration file. Rows whose label is not an option are skipped."""
    rows = [r for r in rows if r.get('probabilities') and r.get('label') in r['probabilities']]
    if not rows:
        raise ValueError('fit_calibration needs labeled rows whose label is one of the options')
    labels = sorted({k for r in rows for k in r['probabilities']})
    shift = _fit_shift(rows, labels, l2, steps, lr)
    params = {'shift': shift, 'n': len(rows), 'label_counts': {k: sum(r['label'] == k for r in rows) for k in labels},
              'ece_before': ece(rows), 'ece_after': ece(rows, shift=shift),
              'nll_before': _nll(rows, {}), 'nll_after': _nll(rows, shift), 'method': 'per-option log-odds shift, NLL + L2',
              'l2': l2, 'fitted_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'source': source}
    if 1 < len(rows) <= 200:
        held = [{'probabilities': _shifted(r['probabilities'], _fit_shift(rows[:i] + rows[i + 1:], labels, l2, steps, lr)),
                 'label': r['label']} for i, r in enumerate(rows)]
        params.update(ece_after_loo=ece(held), nll_after_loo=_nll(held, {}))
    if save:
        with _LOCK:
            data = _load()
            data.setdefault('schema', 'system-one-calibration/1')
            data.setdefault('tasks', {})[task_key] = params
            calibration_path().parent.mkdir(parents=True, exist_ok=True)
            calibration_path().write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    return params


# -- attribution -----------------------------------------------------------------------------------------------------
_SENT = re.compile(r'(?<=[.!?;])\s+(?=[A-Z0-9(\"\'])|\n+')


def sentences(evidence, *, max_sentences=12):
    """[(evidence_id, index, sentence)] in document order, capped at max_sentences."""
    out = []
    for e in evidence:
        for i, s in enumerate(x.strip() for x in _SENT.split(e['text'])):
            if s:
                out.append((e['id'], i, s))
    return out[:max_sentences]


def _without(evidence, eid, index):
    out = []
    for e in evidence:
        if e['id'] == eid:
            rest = [s.strip() for i, s in enumerate(_SENT.split(e['text'])) if i != index and s.strip()]
            if rest:
                out.append({**e, 'text': ' '.join(rest)})
        else:
            out.append(e)
    return out


def _batch(jobs, workers):
    """Run zero-arg callables concurrently; a failure yields None for that job."""
    def run(job):
        try:
            return job()
        except Exception:
            return None
    if workers <= 1 or len(jobs) <= 1:
        return [run(j) for j in jobs]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, jobs))


def attribute(question, options, evidence, label, *, as_of, ledger=None, allow_live=True, max_sentences=12, provider='jev',
              workers=None):
    """Deletion attribution of `label` to evidence sentences: [{'evidence_id','sentence','necessity','sufficiency'}]
    sorted by necessity (desc; None last). Uses raw provider probabilities. 2N+2 typed calls, run concurrently (each
    worker opens its own connection to the same ledger), cache hits free. A failed or prepared call leaves None."""
    as_of = str(as_of)[:10]
    ev = _evidence(evidence, as_of)
    opts = _options(options)
    parts = sentences(ev, max_sentences=max_sentences)
    path = getattr(ledger, 'path', ledger)  # per-worker connections: sqlite handles are not shared across threads
    path = str(path) if path else None
    workers = workers or int(os.environ.get('AGERE_SYSTEM_ONE_WORKERS', '8'))

    def job(items):
        return lambda: choice(question, opts, items, as_of=as_of, provider=provider, ledger=path, allow_live=allow_live)
    meta = {e['id']: e for e in ev}
    jobs = [job(ev), job([])]
    jobs += [job(_without(ev, eid, i)) for eid, i, _ in parts]
    jobs += [job([{**meta[eid], 'text': s}]) for eid, i, s in parts]
    out = _batch(jobs, workers)

    def p(r):
        return None if not r or r.get('status') == 'prepared' else float(r['probabilities'].get(label, 0.0))
    full, empty, n = p(out[0]), p(out[1]), len(parts)
    rows = []
    for k, (eid, _, s) in enumerate(parts):
        minus, alone = p(out[2 + k]), p(out[2 + n + k])
        rows.append({'evidence_id': eid, 'sentence': s,
                     'necessity': None if full is None or minus is None else round(full - minus, 6),
                     'sufficiency': None if alone is None or empty is None else round(alone - empty, 6)})
    rows.sort(key=lambda r: (r['necessity'] is None, -(r['necessity'] or 0.0)))
    return rows


# -- seed calibration set: chart_assertion ---------------------------------------------------------------------------
# Handcrafted, labels exact by construction (no warehouse stream carries a ground-truth assertion label for note text).
# yes = the note documents the finding for the patient; no = the note explicitly denies it; unknown = not addressed
# (absent, family-member only, or a different finding).
CHART_ASSERTION_OPTIONS = {'yes': 'The evidence documents this for the patient', 'no': 'The evidence explicitly states this is absent for the patient',
                           'unknown': UNKNOWN}
CHART_ASSERTION_SEED = [
    ('statin intolerance', 'Stopped atorvastatin in March because of severe myalgias; rosuvastatin also caused muscle pain.', 'yes'),
    ('statin intolerance', 'Tolerating atorvastatin 40 mg without muscle symptoms. LDL at goal.', 'no'),
    ('statin intolerance', 'Seen for knee pain after a fall. Ice and ibuprofen recommended.', 'unknown'),
    ('statin intolerance', "Patient's brother could not take statins due to muscle aches. Patient takes pravastatin.", 'no'),
    ('penicillin allergy', 'Allergies: penicillin (hives as a child).', 'yes'),
    ('penicillin allergy', 'No known drug allergies.', 'no'),
    ('penicillin allergy', 'Here for blood pressure check. BP 128/76.', 'unknown'),
    ('tobacco use', 'Smokes one pack per day for 20 years; counseled on cessation.', 'yes'),
    ('tobacco use', 'Denies tobacco, alcohol or drug use.', 'no'),
    ('tobacco use', 'Follow-up of type 2 diabetes; A1c 7.2%.', 'unknown'),
    ('heart failure', 'Chronic HFrEF, EF 30% on last echo, on carvedilol and sacubitril-valsartan.', 'yes'),
    ('heart failure', 'Echo normal, EF 60%, no evidence of heart failure.', 'no'),
    ('heart failure', 'Mother had congestive heart failure. Patient here for annual exam.', 'unknown'),
    ('chest pain', 'Reports substernal chest pressure on exertion for two weeks.', 'yes'),
    ('chest pain', 'Denies chest pain, palpitations or shortness of breath.', 'no'),
    ('chest pain', 'Medication refill visit for levothyroxine.', 'unknown'),
    ('fall in the last year', 'Fell twice at home since January; uses a cane now.', 'yes'),
    ('fall in the last year', 'Falls screen negative: no falls in the past 12 months.', 'no'),
    ('fall in the last year', 'Discussed colon cancer screening options.', 'unknown'),
    ('depression', 'PHQ-9 score 16, consistent with moderately severe depression; starting sertraline.', 'yes'),
    ('depression', 'PHQ-2 negative. Mood good.', 'no'),
    ('depression', 'Seen for sinus congestion for three days.', 'unknown'),
    ('insulin use', 'Takes insulin glargine 20 units nightly.', 'yes'),
    ('insulin use', 'Diabetes managed with metformin alone; not on insulin.', 'no'),
]


def seed_chart_assertion(*, as_of='2026-10-01', ledger=None, allow_live=True, provider='jev'):
    """Run the provider on CHART_ASSERTION_SEED and fit the 'chart_assertion' shift. Returns the fit params, or
    {'status': 'prepared'} when calls could not be made."""
    def job(subject, text):
        q = f'Does the evidence document {subject} for the patient?'
        return lambda: choice(q, CHART_ASSERTION_OPTIONS, [{'id': 'n1', 'text': text, 'date': as_of}], as_of=as_of,
                              provider=provider, ledger=str(getattr(ledger, 'path', ledger) or default_ledger_path()), allow_live=allow_live)
    out = _batch([job(s, t) for s, t, _ in CHART_ASSERTION_SEED], 8)
    rows = [{'probabilities': r['probabilities'], 'label': lab} for r, (_, _, lab) in zip(out, CHART_ASSERTION_SEED)
            if r and r.get('status') != 'prepared']
    if not rows:
        return {'status': 'prepared'}
    accuracy = sum(max(r['probabilities'], key=r['probabilities'].get) == r['label'] for r in rows) / len(rows)
    return fit_calibration('chart_assertion', rows, source={'set': 'CHART_ASSERTION_SEED (system_one.py, handcrafted)',
                           'provider': provider, 'model': PersistentGateway.MODELS[provider], 'rows_answered': len(rows),
                           'raw_top_label_accuracy': accuracy, 'cost_usd': sum((r or {}).get('cost_usd') or 0.0 for r in out)})
