"""Barriers that change the action: drug-scoped, recency-windowed reading of therapy_barrier for GDMT plans.

    pillars = assess(db, patient_ids, as_of, policy)      # {patient_id: {pillar: PillarBarriers}}

therapy_barrier (clinical/therapy_barriers.py) records why a patient is not on a drug, one row per patient + drug + type +
evidence. The earlier router treated any affirmed row as closing the whole pillar, with no recency rule, so one losartan
intolerance from 2026 closed the ARNI / ACE inhibitor / ARB gap for a 2030 plan. This module applies the catalog's
barrier_policy (programs.json) instead:

- Scope. A barrier excludes the agent it names (drug_key: losartan excludes losartan, not valsartan or sacubitril/
  valsartan). With no named agent it excludes its class. Types that explain the whole pillar (contraindicating lab or
  vital sign, documented decline, not indicated) exclude every agent. Cross-class rules: angioedema on an ACE inhibitor
  excludes ACE inhibitors and flags sacubitril/valsartan as contraindicated (an ARB stays possible with caution).
- Recency. A barrier counts when its event_date, or a restatement (last_date: the reader folds copy-forward repeats into
  mentions + last_date), is within the window before as_of (recency_days.default, overrides per type). Mentions after
  as_of are never used. Older barriers are kept as history (stale) and never close a gap.
- Pillar status. explained: every agent excluded, or a pillar-explaining type -> the gap is barrier_documented (closed)
  and the action is the barrier-specific one. open_with_barrier: some agent remains -> the gap stays open and the
  barrier rides with it (the action names the remaining agents). stale_only: only old barriers -> open gap, history.
- Source location. Every barrier keeps record_id, source_type, evidence_text and, when the source record's text is in
  the semantic layer (record.payload.text), the character span of the evidence (span_start, span_end).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import json

COLUMNS = ('patient_id', 'drug_class', 'gdmt_pillar', 'drug_key', 'barrier_type', 'detail', 'event_date', 'last_date', 'mentions',
           'evidence_text', 'record_id', 'source_type', 'method', 'confidence', 'assertion')
KEEP = ('barrier_type', 'detail', 'drug_class', 'drug_key', 'event_date', 'last_date', 'mentions', 'evidence_text', 'record_id',
        'source_type', 'method', 'confidence', 'assertion')


def _d(s):
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _has_table(db, table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def rows(db, patient_ids, *, min_confidence=0.7):
    """Affirmed, patient-experienced GDMT barrier rows (type != other), any date: {patient_id: [row]}."""
    out = defaultdict(list)
    if not _has_table(db, 'therapy_barrier'):
        return out
    ids = list(patient_ids)
    for i in range(0, len(ids), 500):
        ch = ids[i:i + 500]
        q = (f"SELECT {', '.join(COLUMNS)} FROM therapy_barrier WHERE gdmt_pillar IS NOT NULL AND negated = 0 AND hypothetical = 0 "
             f"AND experiencer = 'patient' AND barrier_type <> 'other' AND coalesce(confidence, 1) >= ? "
             f"AND patient_id IN ({','.join('?' * len(ch))})")
        for r in db.execute(q, [min_confidence] + ch):
            r = dict(zip(COLUMNS, tuple(r)))
            out[r['patient_id']].append(r)
    return out


def _agents(policy, pillar):
    """[(class, agent)] of a pillar."""
    return [(c, a) for c, names in ((policy.get('pillar_agents') or {}).get(pillar) or {}).items() for a in names]


def _klass(policy, c):
    return (policy.get('class_aliases') or {}).get(c, c)


def _names_agent(drug_key, agent):
    k = str(drug_key or '').lower().strip()
    if not k:
        return False
    a = agent.lower()
    return a == k or a.split()[0] == k.split()[0]


def _window(policy, btype):
    days = (policy.get('recency_days') or {})
    return int(days.get(btype, days.get('default', 1095)))


def classify(row, as_of, policy):
    """-> (state, effective_date): state current | stale | future."""
    start = _d(row.get('event_date'))
    if start is None or start > as_of:
        return 'future', None
    last = _d(row.get('last_date'))
    eff = last if last and start <= last <= as_of else start
    return ('current' if eff >= as_of - timedelta(days=_window(policy, row['barrier_type'])) else 'stale'), eff


def _span(db, record_id, evidence, cache):
    """(span_start, span_end) of the evidence in the source record's text, or (None, None)."""
    if not record_id or not evidence or cache.get('no_record'):
        return None, None
    if record_id not in cache:
        try:
            r = db.execute('SELECT payload FROM record WHERE record_id = ?', (record_id,)).fetchone()
        except Exception:  # noqa: BLE001 - no record table: locations stay record-level
            cache['no_record'] = True
            return None, None
        text = None
        if r and r[0]:
            try:
                text = (json.loads(r[0]) or {}).get('text')
            except (TypeError, ValueError):
                text = None
        cache[record_id] = text if isinstance(text, str) else None
    text = cache[record_id]
    if not text:
        return None, None
    probe = evidence.split('...')[0].strip()
    at = text.find(probe)
    if at < 0 and len(probe) > 60:
        probe = probe[:60]
        at = text.find(probe)
    if at < 0:
        return None, None
    return at, at + len(evidence if text.find(evidence) == at else probe)


def assess(db, patient_ids, as_of, policy, *, missing=None, with_spans=True):
    """{patient_id: {pillar: {...}}} for the given patients. missing: optional {patient_id: [pillar]} restricts the work to
    missing pillars (a barrier on a pillar the patient is on changes nothing). Each pillar entry:
      status: explained | open_with_barrier | stale_only
      barriers: current barriers (dicts with KEEP fields, state, effective_date, scope, span_start, span_end)
      stale_barriers: older barriers kept as history
      excluded_agents / remaining_agents / flags: what the barriers rule out and what is still possible
      action: the barrier-specific next step"""
    as_of = as_of if isinstance(as_of, date) else _d(as_of)
    by_patient = rows(db, patient_ids, min_confidence=float(policy.get('min_confidence', 0.7)))
    explain_types = set(policy.get('pillar_explaining_types') or [])
    class_wide = set(policy.get('class_wide_types') or [])
    actions = policy.get('actions') or {}
    cross = policy.get('cross_class_flags') or []
    cache = {}
    out = {}
    for pid, prow in by_patient.items():
        per = defaultdict(list)
        for r in prow:
            per[r['gdmt_pillar']].append(r)
        res = {}
        for pillar, brs in per.items():
            if missing is not None and pillar not in (missing.get(pid) or []):
                continue
            agents = _agents(policy, pillar)
            current, stale = [], []
            excluded, flags, explained_by = set(), [], []
            for r in sorted(brs, key=lambda r: (r.get('event_date') or '', r.get('record_id') or '')):
                state, eff = classify(r, as_of, policy)
                if state == 'future':
                    continue
                item = {k: r.get(k) for k in KEEP}
                item.update(state=state, effective_date=eff.isoformat() if eff else None, pillar=pillar)
                if with_spans:
                    item['span_start'], item['span_end'] = _span(db, r.get('record_id'), r.get('evidence_text'), cache)
                if state == 'stale':
                    item['flag'] = 'stale_barrier'
                    stale.append(item)
                    continue
                c = _klass(policy, r.get('drug_class'))
                btype = r['barrier_type']
                if btype in class_wide:
                    hit = set(agents)
                    item['scope'] = 'pillar'
                elif r.get('drug_key') and any(_names_agent(r['drug_key'], a) for _, a in agents):
                    hit = {(cc, a) for cc, a in agents if _names_agent(r['drug_key'], a)}
                    item['scope'] = 'agent'
                else:
                    hit = {(cc, a) for cc, a in agents if cc == c}
                    item['scope'] = 'class'
                for rule in cross:
                    if c == rule.get('if_class') and btype in (rule.get('barrier_types') or []):
                        hit |= {(cc, a) for cc, a in agents if cc in (rule.get('exclude_classes') or [])}
                        flags.append({'classes': rule.get('flag_classes') or [], 'flag': rule.get('flag'), 'record_id': r.get('record_id')})
                        item['scope'] = 'class+cross_class'
                excluded |= hit
                if btype in explain_types:
                    explained_by.append(btype)
                current.append(item)
            if not current and not stale:
                continue
            flagged = {cc for f in flags for cc in f['classes']}
            remaining = [a for cc, a in agents if (cc, a) not in excluded]
            usable = [a for cc, a in agents if (cc, a) not in excluded and cc not in flagged]
            if current and (explained_by or not remaining):
                status = 'explained'
            elif current:
                status = 'open_with_barrier'
            else:
                status = 'stale_only'
            types = sorted({b['barrier_type'] for b in current})
            if status == 'explained':
                action = '; '.join(actions.get(t, t) for t in (explained_by or types))
            elif status == 'open_with_barrier':
                by_class = {}
                for cc, ag in agents:
                    if (cc, ag) not in excluded and cc not in flagged:
                        by_class.setdefault(cc, []).append(ag)
                still = '; '.join(f"{cc.replace('_', ' ')}: {', '.join(v[:3])}{' ...' if len(v) > 3 else ''}" for cc, v in by_class.items())
                action = '; '.join(actions.get(t, t) for t in types) + \
                    (f" -- still possible: {still}" if still else '') + \
                    (f" (needs clinician review: {', '.join(a for cc, a in agents if cc in flagged and (cc, a) not in excluded)})"
                     if flagged else '')
            else:
                action = 'barrier history only (outside the recency window): treat as an open gap and re-ask the patient'
            res[pillar] = {'status': status, 'barriers': current, 'stale_barriers': stale,
                           'excluded_agents': sorted(a for _, a in excluded), 'remaining_agents': remaining,
                           'usable_agents': usable, 'flags': flags, 'action': action}
        if res:
            out[pid] = res
    return out
