"""Therapy-barrier facts and the live tier-3 cascade, on the question path ("why is P-1 not on an SGLT2 inhibitor?").

therapy_barriers.build runs at build time: tiers 1-2 (rules, local classifier + context student) write therapy_barrier rows
and leave the sentences they could not settle in therapy_barrier_candidate with decided_by='pending_typed' (tier 3 is off
at build by default). Here, per question:
  facts(stack, patients, subject)       the build's decided barrier rows for these patients and this drug/pillar (free).
  live_cascade(stack, patients, subject) the patients' pending tier-3 candidates for this drug, each a System One typed
                                        choice over therapy_barriers.TYPED_LABELS (Jev first, Perplexity when Jev is unsure;
                                        system_one.decide order from the decider bake-off). Paid only with allow_live; cache
                                        hits are free; every call's cost is returned for the receipt.
  evidence(...)                         both as System One evidence items (id, text, date, note_id) for atomic sub-questions.
Nothing is written back to the build: the warehouse stays read-only on the question path.
"""
import sqlite3
import time
from pathlib import Path

VERSION = 'therapy-barrier-answers/1'
TASK_KEY = 'therapy_barrier_reason'
MAX_LIVE = 12  # pending candidates read live per question (the rest stay pending and are named in the receipt)
FACT_TYPES = {'intolerance_side_effect': 'intolerance', 'allergy_angioedema': 'intolerance', 'contraindication_lab': 'contraindication',
              'contraindication_vitals': 'contraindication', 'cost_access': 'cost_access', 'patient_declined': 'declined',
              'not_indicated': 'not_indicated', 'other': 'other'}


def classes(subject):
    """Drug classes and GDMT pillar names a subject covers (sglt2_inhibitor <-> sglt2i, rasi -> ace/arb/arni)."""
    from .medication_state import PILLARS
    from .therapy_barriers import GDMT_PILLAR
    cls = set(PILLARS.get(subject) or [subject])
    if 'hf_evidence_based_beta_blocker' in cls: cls.add('beta_blocker')
    pillars = {subject} if subject in PILLARS else {GDMT_PILLAR[c] for c in cls if c in GDMT_PILLAR}
    return cls, pillars


def semantic_path(stack):
    path = Path(stack.path)
    if (path/'run.json').exists():
        from . import semantic_layer
        return semantic_layer.ensure(path)
    return path/'semantic.sqlite3'


def _db(stack):
    target = semantic_path(stack)
    if not Path(target).exists(): return None
    return sqlite3.connect(f'file:{target}?mode=ro', uri=True)


def _has(db, table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def facts(stack, patients, subject, *, as_of=None):
    """{patient: [barrier rows]} decided at build for this subject (affirmed, patient-experienced, dated <= as_of)."""
    if not patients or not subject: return {}
    db = _db(stack)
    if db is None: return {}
    try:
        if not _has(db, 'therapy_barrier'): return {}
        cls, pillars = classes(subject)
        q = (f"SELECT patient_id, drug_class, gdmt_pillar, barrier_type, detail, event_date, evidence_text, record_id, source_type, "
             f"method, decided_by, confidence FROM therapy_barrier WHERE negated = 0 AND hypothetical = 0 AND experiencer = 'patient' "
             f"AND patient_id IN ({','.join('?' * len(patients))}) AND (drug_class IN ({','.join('?' * len(cls))}) OR gdmt_pillar IN "
             f"({','.join('?' * len(pillars)) or 'NULL'}))" + (' AND (event_date IS NULL OR event_date <= ?)' if as_of else '') +
             ' ORDER BY patient_id, event_date')
        out = {}
        keys = ('patient_id', 'drug_class', 'gdmt_pillar', 'barrier_type', 'detail', 'event_date', 'evidence_text', 'record_id',
                'source_type', 'method', 'decided_by', 'confidence')
        for row in db.execute(q, [*patients, *sorted(cls), *sorted(pillars)] + ([as_of[:10]] if as_of else [])):
            r = dict(zip(keys, row))
            out.setdefault(r['patient_id'], []).append(r)
        return out
    finally:
        db.close()


def pending(stack, patients, subject, *, limit=MAX_LIVE):
    """Tier-3 candidates the build left for the typed cascade, for these patients and this subject."""
    if not patients or not subject: return [], 0
    db = _db(stack)
    if db is None: return [], 0
    try:
        if not _has(db, 'therapy_barrier_candidate'): return [], 0
        cls, _ = classes(subject)
        q = (f"SELECT case_id, patient_id, record_id, event_date, drug_class, drug_key, sentence FROM therapy_barrier_candidate "
             f"WHERE decided_by = 'pending_typed' AND patient_id IN ({','.join('?' * len(patients))}) AND drug_class IN "
             f"({','.join('?' * len(cls))}) ORDER BY event_date DESC, case_id")
        rows = [dict(zip(('case_id', 'patient_id', 'record_id', 'event_date', 'drug_class', 'drug_key', 'sentence'), r))
                for r in db.execute(q, [*patients, *sorted(cls)])]
    finally:
        db.close()
    seen, out = set(), []
    for r in rows:  # copy-forward repeats of one sentence are one decision
        k = (r['patient_id'], r['drug_class'], r['sentence'])
        if k in seen: continue
        seen.add(k); out.append(r)
    return out[:limit], len(out)


def live_cascade(stack, patients, subject, *, as_of, decide=None, allow_live=None):
    """System One over pending tier-3 candidates -> {'decisions':[...], 'new_paid_api_calls', 'estimated_usd', 'pending_total'}."""
    from .therapy_barriers import TYPED_LABELS, NO_BARRIER
    started = time.perf_counter()
    rows, total = pending(stack, patients, subject)
    live = getattr(stack, 'allow_live', False) if allow_live is None else allow_live
    if decide is None:
        from . import system_one
        decide = system_one.decide
    from . import system_one_deciders
    decisions, calls, usd = [], 0, 0.0
    for r in rows:
        day = (r['event_date'] or as_of)[:10]
        if day > as_of[:10]: continue
        q = (f"Why is this patient not taking or did they stop {r['drug_key'] or r['drug_class'].replace('_', ' ')}? Decide from this "
             'excerpt only; a denied, hypothetical or precautionary mention, or one about a relative, is no_barrier.')
        ev = [{'id': r['case_id'], 'text': r['sentence'][:700], 'date': day, 'note_id': r['record_id']}]
        try:
            d = decide(q, TYPED_LABELS, ev, as_of=as_of[:10], order=system_one_deciders.order_for(TASK_KEY), task_key=TASK_KEY,
                       allow_live=live)
        except Exception as exc:  # noqa: BLE001 - a failed decision is an unknown; the rest still run
            d = {'label': 'unknown', 'error': f'{type(exc).__name__}: {exc}'[:200], 'steps': []}
        for s in d.get('steps') or []:
            if s.get('status') == 'answered' and not s.get('cache_hit'):
                calls += 1; usd += float(s.get('cost_usd') or 0.0)
        label = d.get('label') or 'unknown'
        decisions.append({**r, 'label': label, 'barrier': label not in (NO_BARRIER, 'unknown', None),
                          'factor': FACT_TYPES.get(label), 'probability': (d.get('calibrated_probabilities') or {}).get(label),
                          'decided_by': d.get('decided_by'), 'status': d.get('status', 'answered'), 'error': d.get('error')})
    return {'schema': VERSION, 'decisions': decisions, 'pending_total': total, 'read': len(decisions),
            'new_paid_api_calls': calls, 'estimated_usd': round(usd, 6), 'allow_live': bool(live),
            'seconds': round(time.perf_counter() - started, 3)}


def fact_evidence(row):
    detail = f" ({row['detail']})" if row.get('detail') else ''
    text = (f"Therapy barrier ({row['method'] or 'rule'}, build {row['decided_by'] or 'tier 1-2'}): {row['barrier_type'].replace('_', ' ')}"
            f"{detail} for {row['drug_class'].replace('_', ' ')}. Source text: {row.get('evidence_text') or ''}")
    return {'id': f"barrier:{row['patient_id']}:{row['record_id']}:{row['barrier_type']}", 'text': text[:700],
            'date': (row.get('event_date') or 'undated')[:10], 'note_id': row.get('record_id') or 'therapy_barrier'}


def decision_evidence(d):
    text = (f"Therapy barrier (live System One read of a pending tier-3 sentence, {d['decided_by'] or 'undecided'}): "
            f"{str(d['label']).replace('_', ' ')} for {d['drug_class'].replace('_', ' ')}. Source text: {d['sentence']}")
    return {'id': f"barrier-live:{d['case_id']}", 'text': text[:700], 'date': (d.get('event_date') or 'undated')[:10],
            'note_id': d.get('record_id') or 'therapy_barrier_candidate'}


def evidence(stack, patients, subject, *, as_of, live=True, decide=None):
    """{patient: [System One evidence items]} from build facts plus (when live) the live cascade, and the cascade receipt."""
    out = {}
    for pid, rows in facts(stack, patients, subject, as_of=as_of).items():
        out.setdefault(pid, []).extend(fact_evidence(r) for r in rows if r['barrier_type'] != 'other' or r.get('evidence_text'))
    receipt = None
    if live:
        receipt = live_cascade(stack, patients, subject, as_of=as_of, decide=decide)
        for d in receipt['decisions']:
            if d['barrier']: out.setdefault(d['patient_id'], []).append(decision_evidence(d))
    return out, receipt
