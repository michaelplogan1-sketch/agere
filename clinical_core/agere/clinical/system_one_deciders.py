"""Decider bake-off: the same labeled evidence to every typed decider, one primary per System One task_key.

  order_for(task_key, allowed)  the decide() provider order for a task: the bake-off primary first, then the others by
                                accuracy (config/agere/system_one_deciders.json; AGERE_SYSTEM_ONE_DECIDERS overrides the path).
                                Tasks never baked off keep the default Jev -> Perplexity order.
  bakeoff(task_keys)            runs every candidate decider (Jev, Perplexity, and the local healthcare student under
                                k-fold cross-fitting so it never scores rows it trained on) on each task's labeled set,
                                scores accuracy / NLL / ECE / cost / latency, picks the primary (accuracy, then cost, then
                                NLL), fits system_one.fit_calibration for the primary, and writes the config.
Labeled sets (synthetic, labels exact by construction unless marked silver):
  chart_assertion / atomic_yes_no   system_one.CHART_ASSERTION_SEED (24 handcrafted)
  atomic_why_factor                 WHY_FACTOR_SEED (handcrafted: why-not-on-therapy factor questions)
  atomic_gdmt_pillar                PILLAR_SEED (handcrafted: one GDMT pillar status per excerpt)
  hcc_confirm                       HCC_CONFIRM_SEED (handcrafted: does the evidence support a suspected HCC condition)
  therapy_barrier_reason            silver: therapy_barrier tier-1 high-precision rule labels + rule-rejected sentences from
                                    the active warehouse build (therapy_barrier_candidate), sampled deterministically
"""
import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = 'system-one-deciders/1'
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ORDER = ('jev', 'perplexity')
_LOCK = threading.Lock()
_CACHE = {}

YES_NO = {'yes': 'Explicitly established by the evidence', 'no': 'Explicitly contradicted by the evidence',
          'unknown': 'Insufficient, contradictory or unresolved evidence'}
WHY_FACTOR_SEED = [  # (factor question, excerpt, label)
    ('Is there documented intolerance, an adverse effect or an allergy to sglt2 inhibitor?', 'Dapagliflozin stopped after two genital yeast infections; patient unwilling to retry.', 'yes'),
    ('Is there documented intolerance, an adverse effect or an allergy to sglt2 inhibitor?', 'Tolerating empagliflozin 10 mg daily without side effects.', 'no'),
    ('Is there documented intolerance, an adverse effect or an allergy to beta blocker?', 'Seen for ankle sprain; ice and rest advised.', 'unknown'),
    ('Is there a documented contraindication to mra (for example kidney function, potassium, blood pressure or heart rate)?', 'Spironolactone held for potassium 5.9 mmol/L on 3/2.', 'yes'),
    ('Is there a documented contraindication to mra (for example kidney function, potassium, blood pressure or heart rate)?', 'Potassium 4.2, eGFR 68; no barrier to adding spironolactone.', 'no'),
    ('Is there a documented contraindication to sglt2 inhibitor (for example kidney function, potassium, blood pressure or heart rate)?', 'eGFR 14 on dialysis; empagliflozin not started.', 'yes'),
    ('Did the patient decline or refuse beta blocker?', 'Discussed carvedilol; patient declines, worried about fatigue.', 'yes'),
    ('Did the patient decline or refuse beta blocker?', 'Patient agreed to start carvedilol 3.125 mg twice daily.', 'no'),
    ('Did the patient decline or refuse sglt2 inhibitor?', 'Annual eye exam completed; no retinopathy.', 'unknown'),
    ('Is cost, coverage or access documented as a barrier to sglt2 inhibitor?', 'Could not afford the Jardiance copay; prior authorization denied.', 'yes'),
    ('Is cost, coverage or access documented as a barrier to sglt2 inhibitor?', 'Empagliflozin covered by plan, $10 copay, picked up today.', 'no'),
    ('Is mra documented as not indicated for this patient?', 'EF recovered to 60%; MRA no longer indicated per cardiology.', 'yes'),
    ('Was beta blocker stopped or held by a clinician?', 'Metoprolol held for heart rate 42 and dizziness.', 'yes'),
    ('Was beta blocker stopped or held by a clinician?', 'Continue metoprolol succinate 50 mg daily.', 'no'),
    ('Is there no record that sglt2 inhibitor was ever prescribed for this patient?', 'Medication history: dapagliflozin 10 mg filled monthly since 2027.', 'no'),
    ('Is there documented intolerance, an adverse effect or an allergy to ace inhibitor?', "Patient's mother had a lisinopril cough. Patient takes lisinopril 10 mg without problems.", 'no'),
]
PILLAR_OPTIONS = {'on': 'Currently taking: a current fill, or an active listing with no later stop',
                  'stopped': 'Documented as stopped, held or discontinued',
                  'intolerant': 'Documented intolerance, adverse effect, allergy or contraindication',
                  'not_on': 'Not taking: never prescribed, or a lapsed listing with no current use',
                  'unknown': 'Insufficient or contradictory evidence'}
PILLAR_TEXT = {'mra': 'a mineralocorticoid receptor antagonist (spironolactone or eplerenone)',
               'sglt2i': 'an SGLT2 inhibitor (dapagliflozin, empagliflozin or sotagliflozin)',
               'beta_blocker': 'an evidence-based heart-failure beta blocker (carvedilol, metoprolol succinate or bisoprolol)',
               'rasi': 'an ACE inhibitor, ARB or ARNI (sacubitril/valsartan)'}
PILLAR_SEED = [  # (pillar, excerpt, label)
    ('sglt2i', 'Pharmacy: empagliflozin 10 mg, 30-day supply filled 2030-05-01.', 'on'),
    ('sglt2i', 'Dapagliflozin discontinued 2029-11 after recurrent UTIs.', 'intolerant'),
    ('sglt2i', 'Problem list: HFrEF. Medications: carvedilol, sacubitril-valsartan, spironolactone.', 'not_on'),
    ('mra', 'Spironolactone stopped by nephrology in January; no restart planned.', 'stopped'),
    ('mra', 'Takes eplerenone 25 mg daily; refilled last week.', 'on'),
    ('mra', 'Hyperkalemia (K 6.1) on spironolactone; MRA contraindicated going forward.', 'intolerant'),
    ('beta_blocker', 'Carvedilol 12.5 mg twice daily, adherent per pill count.', 'on'),
    ('beta_blocker', 'Metoprolol succinate discontinued by cardiology at the March visit; no restart.', 'stopped'),
    ('beta_blocker', 'Seen for a dental cleaning.', 'unknown'),
    ('rasi', 'Sacubitril-valsartan 49/51 mg twice daily filled 2030-04-20.', 'on'),
    ('rasi', 'Lisinopril caused angioedema in 2028; ACE inhibitors and ARNI avoided.', 'intolerant'),
    ('rasi', 'Losartan last filled 2028-02; no fills since and not on the current medication list.', 'not_on'),
]
HCC_CONFIRM_SEED = [  # (condition, excerpt, label)
    ('chronic kidney disease stage 3b (N18.32)', 'eGFR 38 on 2029-03-02 and 36 on 2029-07-15; creatinine stable.', 'yes'),
    ('chronic kidney disease stage 3b (N18.32)', 'eGFR 88, urine albumin normal; no kidney disease.', 'no'),
    ('type 2 diabetes mellitus (E11.9)', 'HbA1c 7.4% (2029-02) and 7.1% (2029-08); on metformin.', 'yes'),
    ('type 2 diabetes mellitus (E11.9)', 'HbA1c 5.4%; fasting glucose 92. No diabetes.', 'no'),
    ('heart failure with reduced ejection fraction (I50.22)', 'Echo: LVEF 30%, global hypokinesis; on carvedilol and spironolactone.', 'yes'),
    ('heart failure with reduced ejection fraction (I50.22)', 'Echo: normal LV size and function, LVEF 62%.', 'no'),
    ('morbid obesity (E66.01)', 'BMI 42.3 at the January visit and 41.8 in June.', 'yes'),
    ('major depressive disorder (F32.A)', 'PHQ-9 = 2. Mood good, sleeping well.', 'no'),
    ('major depressive disorder (F32.A)', 'Seen for a sore throat for two days; rapid strep negative.', 'unknown'),
    ('chronic obstructive pulmonary disease (J44.9)', 'Spirometry FEV1/FVC 0.58 post-bronchodilator; uses tiotropium daily.', 'yes'),
    ('atrial fibrillation (I48.91)', "Patient's father has atrial fibrillation. Patient's ECG: normal sinus rhythm.", 'no'),
    ('atrial fibrillation (I48.91)', 'Holter: paroxysmal atrial fibrillation, 11% burden; started apixaban.', 'yes'),
]


def config_path():
    return Path(os.environ.get('AGERE_SYSTEM_ONE_DECIDERS') or ROOT / 'config/agere/system_one_deciders.json')


def load():
    path = config_path()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {'schema': VERSION, 'tasks': {}}
    with _LOCK:
        hit = _CACHE.get(str(path))
        if hit and hit[0] == stamp: return hit[1]
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        doc = {'schema': VERSION, 'tasks': {}}
    with _LOCK:
        _CACHE[str(path)] = (stamp, doc)
    return doc


def order_for(task_key, allowed=None):
    """Provider order for decide(): the bake-off order for this task (paid providers only), filtered by `allowed`."""
    allowed = tuple(allowed) if allowed else DEFAULT_ORDER
    order = (load().get('tasks', {}).get(task_key) or {}).get('order') or list(DEFAULT_ORDER)
    out = [p for p in order if p in allowed and p in DEFAULT_ORDER]
    return tuple(out + [p for p in allowed if p not in out and p in DEFAULT_ORDER]) or tuple(allowed)


# -- labeled sets -----------------------------------------------------------------------------------------------------
def labeled(task_key, *, stack_path=None, limit=40):
    """[(question, options, evidence[], label)] for a task's labeled set."""
    from . import system_one as s1
    if task_key == 'chart_assertion':
        return [(f'Does the evidence document {sub} for the patient?', s1.CHART_ASSERTION_OPTIONS, [{'id': 'n1', 'text': t}], lab)
                for sub, t, lab in s1.CHART_ASSERTION_SEED]
    if task_key == 'atomic_yes_no':
        return [(f'For this patient only, does the submitted evidence establish: the patient has {sub}?', YES_NO, [{'id': 'n1', 'text': t}], lab)
                for sub, t, lab in s1.CHART_ASSERTION_SEED]
    if task_key == 'atomic_why_factor':
        return [(q, YES_NO, [{'id': 'n1', 'text': t}], lab) for q, t, lab in WHY_FACTOR_SEED]
    if task_key == 'atomic_gdmt_pillar':
        return [(f"Currently, what is this patient's status for {PILLAR_TEXT[p]}?", PILLAR_OPTIONS, [{'id': 'n1', 'text': t}], lab)
                for p, t, lab in PILLAR_SEED]
    if task_key == 'hcc_confirm':
        from .hcc_suspecting import CONFIRM_OPTIONS, confirm_question
        return [(confirm_question(c), CONFIRM_OPTIONS, [{'id': 'n1', 'text': t}], lab) for c, t, lab in HCC_CONFIRM_SEED]
    if task_key == 'therapy_barrier_reason':
        return therapy_barrier_silver(stack_path, limit=limit)
    raise ValueError(f'No labeled set for {task_key}')


def therapy_barrier_silver(stack_path, *, limit=40):
    """Silver labels from the build: tier-1 high-precision rule decisions (label = barrier type) and rule-rejected sentences
    (no_barrier), balanced and deterministic (ordered by case_id)."""
    import sqlite3
    from .therapy_barriers import TYPED_LABELS
    path = Path(stack_path or json.loads((ROOT/'config/agere/active_warehouse.json').read_text())['build'])
    target = path/'semantic.sqlite3' if (path/'semantic.sqlite3').exists() else path/'stack'/'semantic.sqlite3'
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        pos = db.execute("SELECT case_id, drug_key, drug_class, sentence, label FROM therapy_barrier_candidate WHERE tier = 1 AND rule IS NOT NULL "
                         "AND label IS NOT NULL AND label NOT IN ('no_barrier','unknown') GROUP BY sentence ORDER BY case_id").fetchall()
        neg = db.execute("SELECT case_id, drug_key, drug_class, sentence, 'no_barrier' FROM therapy_barrier_candidate WHERE label = 'no_barrier' "
                         "GROUP BY sentence ORDER BY case_id").fetchall()
    finally:
        db.close()
    by_type = {}
    for r in pos: by_type.setdefault(r[4], []).append(r)
    picked = []
    while len(picked) < limit * 3 // 4 and any(by_type.values()):
        for t in sorted(by_type):
            if by_type[t] and len(picked) < limit * 3 // 4: picked.append(by_type[t].pop(0))
    picked += neg[:limit - len(picked)]
    out = []
    for cid, key, cls, sentence, label in picked:
        if label not in TYPED_LABELS: continue
        q = (f"Why is this patient not taking or did they stop {key or cls.replace('_', ' ')}? Decide from this excerpt only; a denied, "
             'hypothetical or precautionary mention, or one about a relative, is no_barrier.')
        out.append((q, TYPED_LABELS, [{'id': cid, 'text': sentence[:700]}], label))
    return out


TASKS = ('chart_assertion', 'atomic_yes_no', 'atomic_why_factor', 'atomic_gdmt_pillar', 'hcc_confirm', 'therapy_barrier_reason')


# -- scoring ----------------------------------------------------------------------------------------------------------
def score(rows):
    """rows [{'probabilities','label','cost_usd','seconds','error'}] -> accuracy / nll / ece / cost / latency."""
    from .system_one import ece
    ok = [r for r in rows if r.get('probabilities') and not r.get('error')]
    if not ok: return {'n': len(rows), 'answered': 0, 'accuracy': None}
    acc = sum(max(r['probabilities'], key=r['probabilities'].get) == r['label'] for r in ok) / len(ok)
    nll = sum(-math.log(max(float(r['probabilities'].get(r['label'], 0.0)), 1e-6)) for r in ok) / len(ok)
    return {'n': len(rows), 'answered': len(ok), 'accuracy': round(acc, 4), 'accuracy_over_all_rows': round(acc * len(ok) / len(rows), 4),
            'nll': round(nll, 4), 'ece': round(ece([{'probabilities': r['probabilities'], 'label': r['label']} for r in ok]) or 0, 4),
            'mean_cost_usd': round(sum(r.get('cost_usd') or 0.0 for r in ok) / len(ok), 8),
            'mean_seconds': round(sum(r.get('seconds') or 0.0 for r in ok) / len(ok), 3)}


def run_provider(provider, rows, *, as_of, ledger=None, allow_live=True, workers=8):
    from . import system_one as s1
    path = str(getattr(ledger, 'path', ledger) or '') or None

    def job(i):
        q, opts, ev, lab = rows[i]
        started = time.perf_counter()
        try:
            r = s1.choice(q, opts, [{**e, 'date': as_of} for e in ev], as_of=as_of, provider=provider, ledger=path, allow_live=allow_live)
            if r.get('status') == 'prepared':
                return {'label': lab, 'error': 'prepared (not live, not cached)'}
            return {'label': lab, 'probabilities': r['probabilities'], 'cost_usd': r['cost_usd'] or 0.0, 'cache_hit': r['cache_hit'],
                    'seconds': time.perf_counter() - started, 'predicted': r['label']}
        except Exception as exc:  # noqa: BLE001 - a failed call is scored as unanswered
            return {'label': lab, 'error': f'{type(exc).__name__}: {exc}'[:200]}
    return s1._batch([lambda i=i: job(i) for i in range(len(rows))], workers)


def run_student(task_key, rows, *, folds=4):
    """Cross-fitted local student predictions (each row scored by a model trained without its fold)."""
    from . import system_one_student as st
    out = []
    for k in range(folds):
        train = [r for i, r in enumerate(rows) if i % folds != k]
        test = [(i, r) for i, r in enumerate(rows) if i % folds == k]
        model = st.train_rows(task_key, [st.example(q, o, ev, lab) for q, o, ev, lab in train])
        for i, (q, o, ev, lab) in test:
            started = time.perf_counter()
            p = st.probabilities(model, q, o, ev) if model else None
            out.append((i, {'label': lab, 'probabilities': p, 'cost_usd': 0.0, 'seconds': time.perf_counter() - started,
                            **({} if p else {'error': 'student untrainable on this fold'})}))
    return [r for _, r in sorted(out, key=lambda x: x[0])]


def pick(results):
    """Primary: highest accuracy over all rows (unanswered = wrong), then lower mean cost, then lower NLL."""
    ranked = sorted((p for p, s in results.items() if s.get('accuracy') is not None),
                    key=lambda p: (-(results[p].get('accuracy_over_all_rows') or 0), results[p].get('mean_cost_usd') or 0,
                                   results[p].get('nll') or 9e9))
    return ranked


def bakeoff(task_keys=TASKS, *, as_of='2030-05-27', ledger=None, allow_live=True, stack_path=None, save=True, calibrate=True,
            candidates=('jev', 'perplexity', 'local_student')):
    """Run the bake-off; returns the config document (also written to config_path() when save)."""
    from . import system_one as s1
    doc = load() if save else {'schema': VERSION, 'tasks': {}}
    doc = {'schema': VERSION, 'tasks': dict(doc.get('tasks') or {})}
    for task in task_keys:
        rows = labeled(task, stack_path=stack_path)
        if not rows: continue
        per, raw = {}, {}
        for c in candidates:
            raw[c] = run_student(task, rows) if c == 'local_student' else run_provider(c, rows, as_of=as_of, ledger=ledger, allow_live=allow_live)
            per[c] = score(raw[c])
        ranked = pick(per)
        paid = [p for p in ranked if p in DEFAULT_ORDER] or list(DEFAULT_ORDER)
        entry = {'primary': ranked[0] if ranked else None, 'order': paid + [p for p in DEFAULT_ORDER if p not in paid],
                 'results': per, 'n': len(rows), 'set': (__name__ + ':' + task), 'as_of': as_of,
                 'decided_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                 'rule': 'primary = highest accuracy over all rows, then lower cost, then lower NLL; order lists paid providers only '
                         '(the local student runs ahead of them inside system_one.decide when its own held-out threshold clears)',
                 'cost_usd': round(sum(r.get('cost_usd') or 0.0 for c in raw.values() for r in c if not r.get('cache_hit')), 6)}
        if calibrate and paid and per.get(paid[0], {}).get('answered'):
            fit_rows = [{'probabilities': r['probabilities'], 'label': r['label']} for r in raw[paid[0]] if r.get('probabilities')]
            try:
                cal = s1.fit_calibration(task, fit_rows, save=save, source={'set': entry['set'], 'provider': paid[0],
                                         'model': s1.PersistentGateway.MODELS[paid[0]], 'rows_answered': len(fit_rows),
                                         'raw_top_label_accuracy': per[paid[0]]['accuracy'], 'via': VERSION})
                entry['calibration'] = {k: cal.get(k) for k in ('n', 'ece_before', 'ece_after', 'ece_after_loo', 'nll_before', 'nll_after_loo')}
            except ValueError as exc:
                entry['calibration'] = {'status': 'not_fit', 'reason': str(exc)}
        doc['tasks'][task] = entry
    doc['generated'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    doc['default_order'] = list(DEFAULT_ORDER)
    if save:
        path = config_path(); path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp'); tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n'); tmp.replace(path)
        with _LOCK: _CACHE.pop(str(path), None)
    return doc


if __name__ == '__main__':  # python -m clinical_core.agere.clinical.system_one_deciders [task ...]
    import sys
    sys.path.insert(0, str(ROOT))
    from run_integrated_stack import keys
    keys()
    tasks = tuple(sys.argv[1:]) or TASKS
    print(json.dumps({t: {k: v for k, v in e.items() if k in ('primary', 'order', 'results', 'calibration', 'cost_usd')}
                      for t, e in bakeoff(tasks)['tasks'].items()}, indent=1))
