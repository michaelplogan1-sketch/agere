"""Healthcare-specific System One student: a local classifier distilled from Jev / Perplexity labels, asked BEFORE the paid call.

Per task_key (the most common typed tasks: atomic_yes_no, atomic_why_factor, atomic_gdmt_pillar, chart_assertion,
therapy_barrier_reason, hcc_confirm):
  record()    keeps full typed decision state, cutoff and teacher identity after a paid provider clears its bar.
              These are machine observations, not independently reviewed clinical truth. Older truncated rows stay
              stored but cannot qualify under the new input contract.
  fit()       word + character TF-IDF and healthcare features -> logistic regression, trained on the store (teacher labels
              with p >= TEACHER_MIN) plus the task's exact labeled seed (system_one_deciders.labeled). The acceptance threshold
              is chosen on cross-fitted predictions over unique complete decision states: the lowest confidence at
              which accepted rows agree with the label >= TARGET_AGREEMENT on >= MIN_ACCEPTED rows; none -> the student never
              decides (it still reports its reading).
  predict()   used by system_one.decide: returns a decision only when input/option meanings match the trained contract and the
              student's probability clears its threshold; otherwise None and the paid providers run. Free, local, < 5 ms.
Healthcare features: drug ingredient names map to class tokens (drug_classes), negation / family / historical /
hypothetical cues, and lab / vital thresholds (eGFR, potassium, LVEF, BMI, HbA1c, heart rate, systolic BP) as tokens, so
'empagliflozin' and 'Jardiance' share 'CLS_sglt2_inhibitor' and 'K 5.9' reads as 'LAB_potassium_high'.
AGERE_SYSTEM_ONE_STUDENT=off disables predict() (record() still runs).
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

VERSION = 'system-one-student/2'
ROOT = Path(__file__).resolve().parents[3]
TEACHER_MIN = 0.8
TARGET_AGREEMENT = 0.95
MIN_ACCEPTED = 5
MIN_ROWS = 8
FOLDS = 4
_LOCK, _MODELS = threading.Lock(), {}
NEG = re.compile(r"\b(?:no|not|denies|denied|without|negative for|never|free of|resolved|tolerat\w+)\b", re.I)
FAM = re.compile(r"\b(?:mother|father|brother|sister|son|daughter|wife|husband|family history|relative)'?s?\b", re.I)
HIST = re.compile(r'\b(?:history of|previously|prior|in the past|years ago|resolved)\b', re.I)
HYPO = re.compile(r'\b(?:if|monitor for|return if|call if|consider|may|risk of|counsel(?:l)?ed)\b', re.I)
LABS = [('egfr', r'\begfr\D{0,12}(\d+(?:\.\d+)?)', lambda v: 'low' if v < 30 else 'mid' if v < 60 else 'ok'),
        ('potassium', r'\b(?:potassium|k)\b\D{0,6}(\d+(?:\.\d+)?)', lambda v: 'high' if v >= 5.5 else 'ok' if v >= 3.5 else 'low'),
        ('lvef', r'\b(?:lvef|ef)\D{0,6}(\d+)\s*%', lambda v: 'reduced' if v <= 40 else 'mid' if v < 50 else 'preserved'),
        ('bmi', r'\bbmi\D{0,6}(\d+(?:\.\d+)?)', lambda v: 'morbid' if v >= 40 else 'obese' if v >= 30 else 'nonobese'),
        ('a1c', r'\b(?:hba1c|a1c)\D{0,6}(\d+(?:\.\d+)?)', lambda v: 'diabetic' if v >= 6.5 else 'pre' if v >= 5.7 else 'normal'),
        ('hr', r'\b(?:hr|heart rate)\D{0,6}(\d+)', lambda v: 'brady' if v < 50 else 'ok'),
        ('sbp', r'\b(?:bp|blood pressure)\D{0,6}(\d{2,3})/', lambda v: 'hypo' if v < 90 else 'high' if v >= 140 else 'ok')]


def store_path():
    return Path(os.environ.get('AGERE_SYSTEM_ONE_DISTILL') or ROOT/'artifacts/system-one-students/distill.sqlite3')


def model_dir():
    return Path(os.environ.get('AGERE_SYSTEM_ONE_STUDENTS') or ROOT/'artifacts/system-one-students')


def enabled():
    return os.environ.get('AGERE_SYSTEM_ONE_STUDENT', 'on').lower() not in ('0', 'off', 'false', 'no')


# -- features ---------------------------------------------------------------------------------------------------------
def healthcare_tokens(text):
    toks = []
    try:
        from . import drug_classes
        for c in drug_classes.classify(text) or []: toks.append('CLS_' + c)
        for c in drug_classes.brand_classes(text) or []: toks.append('CLS_' + c)
    except Exception:  # noqa: BLE001 - no lexicon: lexical features only
        pass
    for name, rx in (('NEGCUE', NEG), ('FAMCUE', FAM), ('HISTCUE', HIST), ('HYPOCUE', HYPO)):
        if rx.search(text): toks.append(name)
    for name, rx, bucket in LABS:
        for m in re.finditer(rx, text, re.I):
            try: toks.append(f'LAB_{name}_{bucket(float(m.group(1)))}')
            except ValueError: pass
    return ' '.join(dict.fromkeys(toks))


def input_contract():
    """Bind saved lessons/models to their actual state and feature implementation."""
    from . import system_one, drug_classes
    files=[Path(__file__),Path(system_one.__file__),Path(drug_classes.__file__)]
    return hashlib.sha256(json.dumps([VERSION,*[hashlib.sha256(p.read_bytes()).hexdigest() for p in files]]).encode()).hexdigest()


def decision_state(question, options, evidence, *, as_of=None):
    """Use the same options/evidence normalization as the paid typed decision.

    Keep the complete supplied text, ordering, dates, IDs and option meanings.
    A missing cutoff is explicit; a student's input never silently supplies one.
    """
    from . import system_one
    cutoff=str(as_of)[:10] if as_of is not None else None
    if cutoff is not None:date.fromisoformat(cutoff)
    normalized=system_one._evidence(evidence,cutoff)
    for e in normalized:
        if e['date']!='unknown':
            date.fromisoformat(e['date'])
            if cutoff is not None and e['date']>cutoff:raise ValueError('Student evidence beyond cutoff')
    return {'subject':str(question),'as_of':cutoff,'options':system_one._options(options),'evidence':normalized}


def featurize(question, options, evidence, *, as_of=None):
    state=decision_state(question,options,evidence,as_of=as_of)
    text=json.dumps(state,sort_keys=True,ensure_ascii=False,allow_nan=False)
    return text+' || HC: '+healthcare_tokens(text)


def example(question, options, evidence, label, *, weight=1.0, source='gold', as_of=None):
    state=decision_state(question,options,evidence,as_of=as_of)
    key=hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'text': featurize(question,options,evidence,as_of=as_of), 'label': label,
            'options':sorted(state['options']),'option_meanings':state['options'],
            'weight':weight,'source':source,'decision_key':key,'input_contract':input_contract()}


def train_rows(task_key, examples):
    """Fit one model on examples -> {'pipeline','classes','options'} or None when untrainable (few rows / one class)."""
    examples = [e for e in examples if e['label'] in e['options']]
    labels = {e['label'] for e in examples}
    if len(examples) < MIN_ROWS or len(labels) < 2: return None
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline
    pipe = Pipeline([('features', FeatureUnion([('word', TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)),
                                                ('char', TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5), sublinear_tf=True,
                                                                         max_features=40000))])),
                     ('lr', LogisticRegression(C=4.0, class_weight='balanced', max_iter=3000, random_state=812))])
    pipe.fit([e['text'] for e in examples], [e['label'] for e in examples], lr__sample_weight=[e['weight'] for e in examples])
    options = sorted({o for e in examples for o in e['options']})
    meanings={json.dumps(e['option_meanings'],sort_keys=True) for e in examples}
    return {'pipeline': pipe, 'classes': [str(c) for c in pipe.classes_], 'options': options, 'task_key': task_key,
            'option_contracts':sorted(meanings),'input_contract':input_contract()}


def probabilities(model, question, options, evidence, *, as_of=None):
    """{option: p} over the caller's options (labels the model never saw get 0), or None when the options differ."""
    state=decision_state(question,options,evidence,as_of=as_of)
    if (not model or model.get('input_contract')!=input_contract() or sorted(state['options'])!=model['options'] or
        json.dumps(state['options'],sort_keys=True) not in model.get('option_contracts',[])):return None
    p = model['pipeline'].predict_proba([featurize(question, options, evidence,as_of=as_of)])[0]
    out = {o: 0.0 for o in state['options']}
    for c, v in zip(model['classes'], p): out[c] = float(v)
    z = sum(out.values()) or 1.0
    return {k: v / z for k, v in out.items()}


# -- distillation store -----------------------------------------------------------------------------------------------
def _db():
    path = store_path(); path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS labels(id TEXT PRIMARY KEY, task_key TEXT, question TEXT, options TEXT, evidence TEXT, '
               'label TEXT, probability REAL, provider TEXT, recorded_at TEXT)')
    columns={r[1] for r in db.execute('PRAGMA table_info(labels)')}
    for name in ('input_contract','as_of','teacher_model','request_hash'):
        if name not in columns:db.execute('ALTER TABLE labels ADD COLUMN '+name+' TEXT')
    return db


def record(task_key, question, options, evidence, label, probability, provider, *, as_of=None, model=None, request_hash=None):
    """Keep one teacher label (never raises: the store is a learning side effect, not part of the answer)."""
    if not task_key or label in (None, 'unknown') or probability is None: return False
    try:
        state=decision_state(question,options,evidence,as_of=as_of);contract=input_contract()
        if label not in state['options'] or not 0<=float(probability)<=1:return False
        # Same provider/state/label repeats add no independent example. Opposing
        # labels remain separate observations rather than overwriting history.
        key=hashlib.sha256(json.dumps([contract,task_key,state,provider,model,label],sort_keys=True).encode()).hexdigest()
        db = _db()
        try:
            with db:
                db.execute('INSERT OR IGNORE INTO labels (id,task_key,question,options,evidence,label,probability,provider,recorded_at,'
                           'input_contract,as_of,teacher_model,request_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (key,task_key,state['subject'],json.dumps(state['options']),json.dumps(state['evidence']),label,
                            float(probability),provider,datetime.now(timezone.utc).isoformat(timespec='seconds'),
                            contract,state['as_of'],model,request_hash))
        finally:
            db.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def teacher_examples(task_key):
    if not store_path().exists(): return []
    db = _db()
    try:
        rows = db.execute('SELECT question,options,evidence,label,probability,provider,as_of FROM labels '
                          'WHERE task_key=? AND probability>=? AND input_contract=?',
                          (task_key,TEACHER_MIN,input_contract())).fetchall()
    finally:
        db.close()
    return [example(q,json.loads(o),json.loads(ev),lab,weight=float(p),source=f'teacher:{prov}',as_of=cutoff)
            for q,o,ev,lab,p,prov,cutoff in rows]


def learning_units(gold, teachers):
    """One decision state is one unit, including paired provider judgments.

    Explicit supplied references resolve teacher disagreement. Conflicting
    references, or teachers without a resolving reference, stay out of fitting.
    This does not establish independence between related clinical case families.
    """
    groups={};unbound=0;contract=input_contract()
    for reference,rows in ((True,gold),(False,teachers)):
        for row in rows:
            if row.get('input_contract')!=contract or not row.get('decision_key'):
                unbound+=1;continue
            groups.setdefault(row['decision_key'],[]).append((reference,row))
    units=[];conflicts=resolved=0
    for key,items in sorted(groups.items()):
        refs=[r for ref,r in items if ref];candidates=refs or [r for _,r in items]
        if len({r['label'] for r in candidates})!=1:conflicts+=1;continue
        if refs and any(r['label']!=refs[0]['label'] for _,r in items):resolved+=1
        row=dict(candidates[0]);row['weight']=sum(r['weight'] for r in candidates)/len(candidates)
        row['observation_sources']=sorted({r['source'] for _,r in items});units.append(row)
    return units,{'observations':len(gold)+len(teachers),'decision_groups':len(groups),'fit_units':len(units),
                  'unbound_rows_excluded':unbound,'conflicting_groups_excluded':conflicts,
                  'teacher_conflicts_resolved_by_supplied_reference':resolved}


# -- fit / predict ----------------------------------------------------------------------------------------------------
def threshold_from(scored):
    """Lowest confidence whose accepted rows agree >= TARGET_AGREEMENT on >= MIN_ACCEPTED rows; None when no level does."""
    for t in sorted({round(c, 4) for c, _ in scored}):
        acc = [ok for c, ok in scored if c >= t]
        if len(acc) >= MIN_ACCEPTED and sum(acc) / len(acc) >= TARGET_AGREEMENT:
            return t, len(acc), sum(acc) / len(acc)
    return None, 0, None


def fit(task_key, *, gold=None, save=True, stack_path=None):
    """Train the task's student from teacher labels + exact seed; save it with a cross-fitted threshold. -> manifest."""
    import joblib
    if gold is None:
        from .system_one_deciders import labeled
        try: gold = [example(q, o, ev, lab, weight=1.0, source='gold') for q, o, ev, lab in labeled(task_key, stack_path=stack_path)]
        except Exception:  # noqa: BLE001 - no seed for this task: teacher labels only
            gold = []
    teachers=teacher_examples(task_key)
    rows,units=learning_units(gold,teachers)
    started = time.perf_counter()
    scored = []
    for k in range(FOLDS):
        model = train_rows(task_key, [r for i, r in enumerate(rows) if i % FOLDS != k])
        if not model: continue
        for r in (r for i, r in enumerate(rows) if i % FOLDS == k):
            if r['options'] != model['options']: continue
            p = model['pipeline'].predict_proba([r['text']])[0]
            top = model['classes'][int(p.argmax())]
            scored.append((float(p.max()), top == r['label']))
    threshold, accepted, agreement = threshold_from(scored)
    model = train_rows(task_key, rows)
    manifest = {'schema': VERSION, 'task_key': task_key, 'rows': len(rows), 'gold_rows': len(gold), 'teacher_rows': len(teachers),
                'input_contract':input_contract(),'learning_units':units,
                'classes': model['classes'] if model else [], 'options': model['options'] if model else [],
                'cross_fitted': {'scored': len(scored), 'accuracy': round(sum(ok for _, ok in scored) / len(scored), 4) if scored else None,
                                 'threshold': threshold, 'accepted': accepted, 'accepted_agreement': agreement,
                                 'target_agreement': TARGET_AGREEMENT, 'min_accepted': MIN_ACCEPTED,
                                 'split_unit':'unique_complete_decision_state','independent_clinical_family_validation':False},
                'decides': model is not None and threshold is not None, 'trained_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'seconds': round(time.perf_counter() - started, 3), 'features': 'word 1-2g + char_wb 3-5g TF-IDF + healthcare tokens; LR C=4',
                'scope': 'Agreement with teacher/seed labels, not clinical accuracy; synthetic data only.'}
    if save and model:
        d = model_dir(); d.mkdir(parents=True, exist_ok=True)
        joblib.dump({'model': model, 'manifest': manifest}, d/f'{task_key}.joblib', compress=3)
        (d/f'{task_key}.json').write_text(json.dumps(manifest, indent=2) + '\n')
        with _LOCK: _MODELS.pop(task_key, None)
    return manifest


def load(task_key):
    path = model_dir()/f'{task_key}.joblib'
    try: stamp = path.stat().st_mtime
    except OSError: return None
    with _LOCK:
        hit = _MODELS.get(task_key)
        if hit and hit[0] == stamp: return hit[1]
    import joblib
    try: obj = joblib.load(path)
    except Exception:  # noqa: BLE001 - unreadable artifact: no student
        obj = None
    with _LOCK: _MODELS[task_key] = (stamp, obj)
    return obj


def predict(task_key, question, options, evidence, *, as_of=None):
    """-> {'label','probabilities','threshold','reading'} when the student decides, {'reading': ...} when it only reads, or None."""
    if not task_key or not enabled(): return None
    obj = load(task_key)
    if not obj: return None
    if obj['manifest'].get('input_contract')!=input_contract():return None
    p = probabilities(obj['model'], question, options, evidence,as_of=as_of)
    if p is None: return None
    label = max(p, key=p.get)
    t = obj['manifest']['cross_fitted']['threshold']
    reading = {'label': label, 'probability': round(p[label], 4), 'threshold': t, 'model': f'{VERSION}:{task_key}'}
    if t is None or p[label] < t or label == 'unknown': return {'reading': reading}
    return {'label': label, 'probabilities': p, 'threshold': t, 'reading': reading}
