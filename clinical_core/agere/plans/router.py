"""Deterministic enrollment router: a patient list + a program kind -> a per-patient routed plan.

    plan = route(semantic_db, patient_ids, 'pharmacy_gdmt', '2030-05-27')

Per patient it reads the workspace semantic layer (read-only) and produces
- coverage at as_of: the primary span (active, available by as_of, not medigap / COB secondary), payer, product, LOB
- attribution: enrollment_span attribution_npi / attribution_practice_id, else the most recent primary care visit, else
  the patient table's PCP columns
- the gap the program closes (missing GDMT pillars from fills or the active medication list in the lookback; or the most
  recent discharged ED low-back-pain visit and whether PT followed within the window)
- a routing decision: enroll (program + vendor slot), coverage_check queue, care_team_review (no program accepts the
  coverage), or exclude (deceased, hospice, no gap, no qualifying event, age)
- an outreach task (channel, owner, priority, due date, script template id, required fields present / to collect)
Every field is {'value', 'sources'} where sources are 'table:record_id' strings (lineage). A summary block counts patients
by route, program, vendor slot, payer, line of business, practice and priority.

No model call happens here: the same inputs always give the same plan.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from . import catalog as catmod

SCHEMA = 'agere-routed-plan/1'
CHUNK = 500


# --- small helpers ----------------------------------------------------------------------------------------------------------

def F(value, sources=()):
    """A lineage-carrying field."""
    return {'value': value, 'sources': sorted({s for s in sources if s})}


def _d(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _iso(d):
    return d.isoformat() if d else None


def _src(table, rid):
    return f'{table}:{rid}' if rid not in (None, '') else None


def _norm_icd(code):
    return str(code or '').replace('.', '').strip().upper()


def _icd_match(code, prefixes):
    c = _norm_icd(code)
    return bool(c) and any(c.startswith(_norm_icd(p)) for p in prefixes)


class _DB:
    """Read-only access with tolerance for absent tables / columns (small workspaces, fixtures)."""

    def __init__(self, semantic_db):
        if isinstance(semantic_db, sqlite3.Connection):
            self.db, self.own = semantic_db, False
        else:
            p = Path(semantic_db)
            if not p.is_file():
                raise FileNotFoundError(f'No semantic layer at {p}')
            self.db, self.own = sqlite3.connect(f'file:{p}?mode=ro', uri=True), True
        self.db.row_factory = sqlite3.Row
        self._cols = {}
        self.absent = set()

    def cols(self, table):
        if table not in self._cols:
            self._cols[table] = {r[1] for r in self.db.execute(f'PRAGMA table_info({table})')}
            if not self._cols[table]:
                self.absent.add(table)
        return self._cols[table]

    def has(self, table, *columns):
        c = self.cols(table)
        return bool(c) and all(x in c for x in columns)

    def rows(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, params)]

    def by_patient(self, table, ids, where='', params=(), columns='*'):
        """Rows of table for the given patient ids (chunked IN lists)."""
        if not self.has(table, 'patient_id'):
            return []
        out = []
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i + CHUNK]
            sql = f"SELECT {columns} FROM {table} WHERE patient_id IN ({','.join('?' * len(chunk))})"
            if where:
                sql += f' AND ({where})'
            out += self.rows(sql, tuple(chunk) + tuple(params))
        return out

    def meta_as_of(self):
        if not self.has('meta', 'key', 'value'):
            return None
        r = self.db.execute("SELECT value FROM meta WHERE key='as_of'").fetchone()
        return r[0] if r else None

    def close(self):
        if self.own:
            self.db.close()


def _group(rows, key='patient_id'):
    g = defaultdict(list)
    for r in rows:
        g[r[key]].append(r)
    return g


# --- per-patient facts -------------------------------------------------------------------------------------------------------

def _active_span(spans, as_of, cov):
    """The primary span at as_of, or (None, reason)."""
    bad_p, bad_l = set(cov.get('exclude_products') or []), set(cov.get('exclude_lines_of_business') or [])
    active, secondary_only = [], []
    for s in spans:
        start, end, avail = _d(s.get('coverage_start')), _d(s.get('coverage_end')), _d(s.get('available_date'))
        if not start or start > as_of or (end and end < as_of) or (avail and avail > as_of):
            continue
        if s.get('product') in bad_p or s.get('line_of_business') in bad_l:
            secondary_only.append(s)
            continue
        active.append(s)
    if not active:
        if secondary_only:
            return None, 'only secondary coverage (medigap / COB) is active at as_of'
        return None, 'no enrollment span active at as_of' if spans else 'no enrollment spans received'
    active.sort(key=lambda s: (s.get('coverage_start') or '', s.get('record_id') or ''), reverse=True)
    return active[0], None


def _coverage(spans, patient, as_of, cov):
    span, why = _active_span(spans, as_of, cov)
    if span is None:
        src = [_src('enrollment_span', s.get('record_id')) for s in spans]
        return {'active': F(False, src), 'reason': why, 'payer_id': F(None), 'product': F(None),
                'line_of_business': F(None), 'member_id': F(None), 'plan_id': F(None), 'coverage_start': F(None),
                'coverage_end': F(None), 'dual_status': F(None), 'span': None}
    s = [_src('enrollment_span', span.get('record_id'))]
    return {'active': F(True, s), 'reason': None, 'payer_id': F(span.get('payer_id'), s), 'product': F(span.get('product'), s),
            'line_of_business': F(span.get('line_of_business'), s), 'member_id': F(span.get('member_id'), s),
            'plan_id': F(span.get('plan_id') or None, s), 'coverage_start': F(span.get('coverage_start'), s),
            'coverage_end': F(span.get('coverage_end') or None, s), 'dual_status': F(span.get('dual_status') or None, s),
            'span': span}


def _attribution(span, visits, patient, npi_of, as_of):
    if span and (span.get('attribution_npi') or span.get('attribution_practice_id')):
        s = [_src('enrollment_span', span.get('record_id'))]
        return {'method': 'enrollment_span', 'npi': F(span.get('attribution_npi') or None, s),
                'practice_id': F(span.get('attribution_practice_id') or None, s), 'last_pcp_visit': F(None)}
    past = [v for v in visits if _d(v.get('event_date')) and _d(v['event_date']) <= as_of]
    if past:
        v = max(past, key=lambda v: (v['event_date'], v.get('record_id') or ''))
        s = [_src('encounter', v.get('record_id'))]
        clin = v.get('attending_clinician_id')
        npi = npi_of.get(clin) if clin else None
        npi_src = s + ([_src('clinician', clin)] if npi else [])
        return {'method': 'most_recent_pcp_visit', 'npi': F(npi or clin or None, npi_src),
                'practice_id': F(v.get('organization_id') or None, s), 'last_pcp_visit': F(v['event_date'], s)}
    if patient and (patient.get('pcp_clinician_id') or patient.get('pcp_practice_id')):
        s = [_src('patient', patient['patient_id'])]
        clin = patient.get('pcp_clinician_id')
        return {'method': 'patient_pcp', 'npi': F(npi_of.get(clin) or clin or None, s),
                'practice_id': F(patient.get('pcp_practice_id') or None, s), 'last_pcp_visit': F(None)}
    return {'method': 'unattributed', 'npi': F(None), 'practice_id': F(None), 'last_pcp_visit': F(None)}


def _age(patient, as_of):
    if not patient:
        return F(None)
    s = [_src('patient', patient['patient_id'])]
    b = _d(patient.get('birth_date'))
    if b:
        return F(as_of.year - b.year - ((as_of.month, as_of.day) < (b.month, b.day)), s)
    for k in ('age_latest', 'age_reported'):
        if patient.get(k) not in (None, ''):
            try:
                return F(int(float(patient[k])), s)
            except (TypeError, ValueError):
                pass
    return F(None)


def _exclusions(pid, spans, claims, encounters, as_of, cfg):
    """-> list of {'reason', 'detail', 'sources'} (deceased first, then hospice)."""
    out = []
    dec, hos = cfg.get('deceased') or {}, cfg.get('hospice') or {}
    death_src = [_src('enrollment_span', s.get('record_id')) for s in spans
                 if s.get('termination_reason') in set(dec.get('enrollment_termination_reasons') or [])
                 and (not _d(s.get('coverage_end')) or _d(s['coverage_end']) <= as_of)
                 and (not _d(s.get('available_date')) or _d(s['available_date']) <= as_of)]
    death_src += [_src('payer_claim', c.get('record_id') or c.get('payer_claim_id')) for c in claims
                  if str(c.get('discharge_status') or '') in set(dec.get('claim_discharge_statuses') or [])]
    death_src += [_src('encounter', e.get('record_id')) for e in encounters
                  if str(e.get('disposition') or '').lower() in set(dec.get('encounter_dispositions') or [])]
    if death_src:
        out.append({'reason': 'deceased', 'detail': 'death recorded on enrollment, claim discharge status or encounter disposition',
                    'sources': sorted(set(filter(None, death_src)))})
    since = as_of - timedelta(days=int(hos.get('lookback_days') or 365))
    tob = tuple(hos.get('type_of_bill_prefixes') or ())
    hos_src = []
    for c in claims:
        when = _d(c.get('service_to')) or _d(c.get('service_from'))
        if not when or when < since or (_d(c.get('service_from')) or when) > as_of:
            continue
        if (tob and str(c.get('type_of_bill') or '').startswith(tob)) or \
                str(c.get('discharge_status') or '') in set(hos.get('claim_discharge_statuses') or []):
            hos_src.append(_src('payer_claim', c.get('record_id') or c.get('payer_claim_id')))
    hos_src += [_src('encounter', e.get('record_id')) for e in encounters
                if str(e.get('disposition') or '').lower() in set(hos.get('encounter_dispositions') or [])
                and _d(e.get('event_date')) and since <= _d(e['event_date']) <= as_of]
    if hos_src:
        out.append({'reason': 'hospice', 'detail': f'hospice claim or disposition since {since.isoformat()}',
                    'sources': sorted(set(filter(None, hos_src)))})
    return out


# --- gap evaluators ------------------------------------------------------------------------------------------------------------

def _ingredient_hit(entry, rules):
    key = str(entry.get('drug_key') or '').lower().strip()
    name = ' ' + str(entry.get('name') or '').lower() + ' '
    for r in rules:
        if isinstance(r, str):
            if key == r or key == r + ' brand':
                return True
        elif key in (r['key'], r['key'] + ' brand') and any(t in name for t in r.get('name_any') or []):
            return True
    return False


class _GDMT:
    gap_type = 'gdmt_pillar_missing'

    def __init__(self, db, ids, as_of, cfg):
        self.cfg, self.as_of = cfg, as_of
        self.start = as_of - timedelta(days=int(cfg['lookback_days']))
        pillars = cfg['pillars']
        classes = sorted({c for p in pillars.values() for c in p.get('fill_classes') or []})
        self.fills = _group(db.by_patient(
            'fill', ids, f"(class_key IN ({','.join('?' * len(classes))}) OR drug_class IN ({','.join('?' * len(classes))})) "
            "AND fill_date <= ? AND COALESCE(supply_end, fill_date) >= ?",
            tuple(classes) * 2 + (as_of.isoformat(), self.start.isoformat()),
            'fill_id, patient_id, fill_date, supply_end, class_key, drug_class, drug_name, record_ids')) \
            if classes and db.has('fill', 'class_key', 'drug_class', 'supply_end', 'record_ids') else {}
        self.meds = _group(db.by_patient('medication_list_entry', ids)) if db.has('medication_list_entry', 'drug_key') else {}
        pref = cfg.get('diagnosis_icd10_prefixes') or []
        conds = db.by_patient('condition', ids, ' OR '.join("REPLACE(UPPER(icd10), '.', '') LIKE ?" for _ in pref),
                              tuple(_norm_icd(p) + '%' for p in pref),
                              'record_id, patient_id, icd10, event_date') if pref and db.has('condition', 'icd10') else []
        self.dx = _group([c for c in conds if not _d(c.get('event_date')) or _d(c['event_date']) <= as_of])
        # Documented barriers (therapy_barrier, clinical/therapy_barriers.py) through the catalog's barrier_policy
        # (plans/barriers.py): drug-scoped, recency-windowed, with source locations. Without a policy, the reader's own
        # pillar-level documented() view is used.
        self.barriers, self.policy = {}, cfg.get('_barrier_policy')
        if db.has('therapy_barrier', 'gdmt_pillar', 'barrier_type'):
            if self.policy:
                from . import barriers as barriermod
                self.barriers = barriermod.assess(db.db, ids, as_of, self.policy)
            else:
                from ..clinical import therapy_barriers
                legacy = therapy_barriers.documented(db.db, ids, as_of.isoformat())
                self.barriers = {pid: {pl: {'status': 'explained', 'barriers': rs, 'stale_barriers': [], 'excluded_agents': [],
                                            'remaining_agents': [], 'usable_agents': [], 'flags': [], 'action': None}
                                       for pl, rs in v.items()} for pid, v in legacy.items()}

    def _med_recent(self, m):
        dates = [_d(m.get(k)) for k in ('last_listed', 'last_order_date', 'last_fill_date')]
        dates = [x for x in dates if x and x <= self.as_of]
        return bool(dates) and max(dates) >= self.start

    def evaluate(self, pid, details):
        statuses = set(self.cfg.get('med_list_statuses') or ['active'])
        present, missing, pillars = [], [], {}
        for name, p in self.cfg['pillars'].items():
            classes = set(p.get('fill_classes') or [])
            fills = [f for f in self.fills.get(pid, []) if f.get('class_key') in classes or f.get('drug_class') in classes]
            meds = [m for m in self.meds.get(pid, []) if (m.get('list_status') or '') in statuses and self._med_recent(m)
                    and _ingredient_hit(m, p.get('ingredients') or [])]
            src = [_src('fill', r.strip()) for f in fills for r in str(f.get('record_ids') or f.get('fill_id')).split(',')]
            src += [_src('medication_list_entry', m.get('record_id')) for m in meds]
            on = bool(fills or meds)
            last_fill = max((f['fill_date'] for f in fills), default=None)
            pillars[name] = {'label': p.get('label'), 'on_therapy': F(on, src), 'last_fill_date': F(last_fill, src),
                             'evidence': ('fill' if fills else '') + ('+' if fills and meds else '') + ('med_list' if meds else '') or None}
            (present if on else missing).append(name)
        # A missing pillar whose barriers explain it (every agent excluded, or a pillar-wide reason) is barrier_documented,
        # not an open gap. A pillar with a current barrier and an agent still possible stays an open gap with the barrier
        # attached (open_with_barrier); old barriers ride along as history (stale_barriers) and never close a gap.
        keep = ('barrier_type', 'detail', 'drug_class', 'drug_key', 'event_date', 'last_date', 'evidence_text', 'record_id',
                'source_type', 'method', 'confidence', 'assertion', 'scope', 'state', 'effective_date', 'span_start', 'span_end')
        open_gaps, documented, with_barrier, stale_pillars = [], [], [], []
        for name in missing:
            a = (self.barriers.get(pid) or {}).get(name)
            cur = sorted((a or {}).get('barriers') or [], key=lambda r: (-(r.get('confidence') or 1), r.get('event_date') or ''))
            if a and a['status'] == 'explained' and cur:
                pillars[name]['status'] = 'barrier_documented'
                documented.append(name)
            else:
                pillars[name]['status'] = 'gap'
                open_gaps.append(name)
            if cur:
                with_barrier.append(name)
                pillars[name]['barriers'] = F([{k: r.get(k) for k in keep} for r in cur[:5]],
                                              [_src('therapy_barrier', r.get('record_id')) for r in cur[:5]])
            if a and a.get('stale_barriers'):
                stale_pillars.append(name)
                pillars[name]['stale_barriers'] = F([{k: r.get(k) for k in keep} | {'flag': 'stale_barrier'} for r in a['stale_barriers'][:5]],
                                                    [_src('therapy_barrier', r.get('record_id')) for r in a['stale_barriers'][:5]])
            if a:
                pillars[name]['barrier_assessment'] = {k: a.get(k) for k in ('status', 'excluded_agents', 'usable_agents', 'flags', 'action')}
        for name in present:
            pillars[name]['status'] = 'on_therapy'
        dx_rows = self.dx.get(pid, [])
        dx_src = [_src('condition', c.get('record_id')) for c in dx_rows]
        barrier_src = [s for n in documented for s in pillars[n]['barriers']['sources']]
        open_barrier_src = [s for n in with_barrier if n not in documented for s in pillars[n]['barriers']['sources']]
        gap = {'type': self.gap_type,
               'present': F(bool(missing), [s for v in pillars.values() for s in v['on_therapy']['sources']] + dx_src + barrier_src),
               'missing_pillars': F(open_gaps), 'present_pillars': F(present), 'missing_pillar_count': F(len(open_gaps)),
               'barrier_documented_pillars': F(documented, barrier_src),
               'barrier_pillars': F(with_barrier, barrier_src + open_barrier_src),
               'stale_barrier_pillars': F(stale_pillars, [s for n in stale_pillars for s in pillars[n]['stale_barriers']['sources']]),
               'all_missing_barrier_documented': bool(documented) and not open_gaps,
               'pillars': pillars, 'lookback_start': self.start.isoformat(),
               'hf_diagnosis': F(bool(dx_rows), dx_src),
               'reason': None if missing else 'no_gap: all four GDMT pillars have a fill or active list entry in the lookback'}
        metrics = {'missing_pillar_count': len(open_gaps), 'barrier_documented_count': len(with_barrier), 'always': 1}
        _caller(gap, details, {'missing_pillars': open_gaps})
        return gap, metrics


class _PTAfterED:
    gap_type = 'pt_after_ed_low_back_pain'

    def __init__(self, db, ids, as_of, cfg):
        self.cfg, self.as_of = cfg, as_of
        self.since = as_of - timedelta(days=int(cfg['event_lookback_days']))
        pref = cfg.get('diagnosis_icd10_prefixes') or []
        events = defaultdict(list)  # pid -> [{'date', 'sources', 'codes'}]
        idset = set(ids)
        if db.has('payer_claim_diagnosis', 'icd10', 'payer_claim_id') and db.has('payer_claim', 'place_of_service'):
            pos = cfg.get('ed_place_of_service') or ['23']
            rng, params = [], []
            for p in pref:
                for form in {p, _norm_icd(p)}:
                    rng.append('(d.icd10 >= ? AND d.icd10 < ?)')
                    params += [form, form[:-1] + chr(ord(form[-1]) + 1)]
            live = ' AND COALESCE(c.counts_in_metrics, 1) != 0' if db.has('payer_claim', 'counts_in_metrics') else ''
            sql = (f"SELECT c.payer_claim_id, c.record_id, c.patient_id, c.service_from, d.icd10 FROM payer_claim_diagnosis d "
                   f"JOIN payer_claim c ON c.payer_claim_id = d.payer_claim_id WHERE ({' OR '.join(rng)}) "
                   f"AND c.place_of_service IN ({','.join('?' * len(pos))}) AND c.service_from BETWEEN ? AND ?{live}")
            for r in db.rows(sql, tuple(params) + tuple(pos) + (self.since.isoformat(), as_of.isoformat())):
                if r['patient_id'] in idset and _icd_match(r['icd10'], pref):
                    events[r['patient_id']].append({'date': r['service_from'][:10], 'codes': [r['icd10']],
                                                    'sources': [_src('payer_claim', r['record_id'] or r['payer_claim_id'])]})
        if db.has('encounter_diagnosis', 'encounter_id', 'icd10') and db.has('encounter', 'encounter_id', 'type_concept'):
            concepts = cfg.get('ed_encounter_concepts') or ['emergency_visit']
            sql = (f"SELECT e.record_id, e.patient_id, e.event_date, x.icd10, x.record_id AS dx_record FROM encounter e "
                   f"JOIN encounter_diagnosis x ON x.encounter_id = e.encounter_id "
                   f"WHERE e.type_concept IN ({','.join('?' * len(concepts))}) AND e.event_date BETWEEN ? AND ?")
            for r in db.rows(sql, tuple(concepts) + (self.since.isoformat(), as_of.isoformat())):
                if r['patient_id'] in idset and _icd_match(r['icd10'], pref):
                    events[r['patient_id']].append({'date': r['event_date'][:10], 'codes': [r['icd10']],
                                                    'sources': [_src('encounter', r['record_id']),
                                                                _src('encounter_diagnosis', r['dx_record'])]})
        self.events = events
        # admissions that turn an ED visit into an admitted (not discharged) visit
        adm = defaultdict(list)
        if db.has('encounter', 'type_concept'):
            for r in db.by_patient('encounter', ids, "type_concept = 'inpatient_admission'", (), 'patient_id, event_date, record_id'):
                if _d(r.get('event_date')):
                    adm[r['patient_id']].append((_d(r['event_date']), _src('encounter', r['record_id'])))
        if db.has('payer_claim', 'admission_date'):
            inpatient = " AND (type_of_bill LIKE '011%' OR type_of_bill LIKE '11%' OR COALESCE(drg, '') != '')" \
                if db.has('payer_claim', 'type_of_bill', 'drg') else ''
            for r in db.by_patient('payer_claim', ids, "admission_date IS NOT NULL AND admission_date != ''" + inpatient, (),
                                   'patient_id, admission_date, record_id, payer_claim_id'):
                if _d(r.get('admission_date')):
                    adm[r['patient_id']].append((_d(r['admission_date']), _src('payer_claim', r['record_id'] or r['payer_claim_id'])))
        self.admissions = adm
        codes = cfg.get('pt_cpt') or []
        pt = defaultdict(list)
        if codes and db.has('payer_claim_line', 'cpt', 'service_date'):
            hc = ' OR hcpcs IN (' + ','.join('?' * len(codes)) + ')' if db.has('payer_claim_line', 'hcpcs') else ''
            live = ' AND COALESCE(counts_in_metrics, 1) != 0' if db.has('payer_claim_line', 'counts_in_metrics') else ''
            for r in db.by_patient('payer_claim_line', ids, f"(cpt IN ({','.join('?' * len(codes))}){hc}) AND service_date <= ?{live}",
                                   tuple(codes) + (tuple(codes) if hc else ()) + (as_of.isoformat(),),
                                   'patient_id, service_date, cpt, hcpcs, record_id, payer_claim_id'):
                if _d(r.get('service_date')):
                    pt[r['patient_id']].append((_d(r['service_date']), r.get('cpt') or r.get('hcpcs'),
                                                _src('payer_claim_line', r['record_id'] or r['payer_claim_id'])))
        self.pt = pt

    def evaluate(self, pid, details):
        window = int(self.cfg['followup_window_days'])
        adm_days = self.cfg.get('exclude_if_admitted_within_days')
        by_date = {}
        for e in self.events.get(pid, []):
            x = by_date.setdefault(e['date'], {'date': e['date'], 'codes': set(), 'sources': set()})
            x['codes'].update(e['codes'])
            x['sources'].update(filter(None, e['sources']))
        visits, admitted = [], []
        for v in sorted(by_date.values(), key=lambda v: v['date']):
            d0 = _d(v['date'])
            adm = [s for (a, s) in self.admissions.get(pid, []) if adm_days is not None and d0 <= a <= d0 + timedelta(days=int(adm_days))]
            (admitted if adm else visits).append({**v, 'admitted_sources': adm})
        pts = sorted(self.pt.get(pid, []))
        last_pt = pts[-1] if pts else None
        base = {'type': self.gap_type, 'qualifying_ed_visits': F(len(visits), [s for v in visits for s in v['sources']]),
                'admitted_ed_visits_skipped': F(len(admitted), [s for v in admitted for s in list(v['sources']) + v['admitted_sources']]),
                'last_pt_date': F(_iso(last_pt[0]) if last_pt else None, [last_pt[2]] if last_pt else []),
                'followup_window_days': window}
        if not visits:
            why = ('ed_visit_admitted: every ED visit with the diagnosis in the lookback led to an admission' if admitted else
                   'no_qualifying_event: no discharged ED visit with the diagnosis in the lookback')
            base.update(present=F(False, base['admitted_ed_visits_skipped']['sources']), ed_visit_date=F(None),
                        days_since_ed=F(None), pt_within_window=F(None), window_open=F(None), reason=why)
            return base, {'always': 1}
        v = visits[-1]
        d0 = _d(v['date'])
        after = [p for p in pts if d0 <= p[0] <= d0 + timedelta(days=window)]
        days = (self.as_of - d0).days
        src = sorted(v['sources'])
        base.update(ed_visit_date=F(v['date'], src), ed_diagnosis_codes=F(sorted(v['codes']), src),
                    days_since_ed=F(days, src), window_open=F(days <= window, src),
                    pt_within_window=F(bool(after), [p[2] for p in after]),
                    first_pt_after_ed=F(_iso(after[0][0]) if after else None, [after[0][2]] if after else []))
        if after:
            base.update(present=F(False, src + [p[2] for p in after]),
                        reason=f'pt_completed: PT ({after[0][1]}) on {after[0][0].isoformat()} within {window} days of the ED visit')
        else:
            base.update(present=F(True, src + ([last_pt[2]] if last_pt else [])), reason=None)
        _caller(base, details, {'ed_visit_date': v['date']})
        return base, {'days_since_ed': days, 'always': 1}


def _caller(gap, details, computed):
    """Attach caller-supplied gap details (e.g. from the cohort definition) without letting them override the evidence."""
    if not details:
        return
    gap['caller_details'] = F(details, ['caller:gap_details'])
    diff = {k: {'caller': details[k], 'computed': computed[k]} for k in computed
            if k in details and _same(details[k]) != _same(computed[k])}
    if diff:
        gap['caller_disagrees'] = diff


def _same(v):
    return sorted(v) if isinstance(v, list) else v


EVALUATORS = {'gdmt_pillar_missing': _GDMT, 'pt_after_ed_low_back_pain': _PTAfterED}


# --- eligibility, assignment, tasks ----------------------------------------------------------------------------------------

def _eligibility(program, cov, age):
    """-> list of checks {'rule', 'passed', 'detail'}."""
    e = program.get('eligibility') or {}
    checks = []
    active = cov['active']['value']
    if e.get('requires_active_coverage'):
        checks.append({'rule': 'requires_active_coverage', 'passed': bool(active), 'detail': cov.get('reason') or 'active'})
    if e.get('products'):
        p = cov['product']['value']
        checks.append({'rule': 'product', 'passed': p in e['products'], 'detail': f'{p!r} in {e["products"]}'})
    if e.get('lines_of_business'):
        lob = cov['line_of_business']['value']
        checks.append({'rule': 'line_of_business', 'passed': lob in e['lines_of_business'], 'detail': f'{lob!r} in {e["lines_of_business"]}'})
    if e.get('age'):
        a, lo, hi = age['value'], e['age'].get('min'), e['age'].get('max')
        ok = a is not None and (lo is None or a >= lo) and (hi is None or a <= hi)
        checks.append({'rule': 'age', 'passed': ok, 'detail': f'age {a} in [{lo}, {hi}]'})
    return checks


def _slot_fits(slot, facts):
    f = slot.get('filters') or {}
    for key, fact in (('payer_ids', 'payer_id'), ('products', 'product'), ('lines_of_business', 'line_of_business'),
                      ('practice_ids', 'practice_id')):
        if f.get(key) and facts.get(fact) not in f[key]:
            return False
    return True


def _assign(program, pid, facts):
    fits = [s for s in program['vendor_slots'] if _slot_fits(s, facts)]
    if not fits:
        return None, 'no vendor slot accepts this patient (slot filters)'
    if fits[0].get('assignment') == 'hash':
        pool = [s for s in fits if s.get('assignment') == 'hash']
        h = int(hashlib.sha256(f"{program['id']}|{pid}".encode()).hexdigest(), 16)
        return pool[h % len(pool)], f'stable hash over {len(pool)} slots'
    return fits[0], 'first matching slot by catalog order'


def _priority(program, metrics):
    for r in program.get('priority_rules') or []:
        v = metrics.get(r.get('metric'))
        if v is None:
            continue
        if ('gte' in r and v < r['gte']) or ('lte' in r and v > r['lte']):
            continue
        return r['priority'], r
    return 'medium', None


def _task(program, slot, pid, as_of, priority, rule, due_days, fields, owner=None):
    due = as_of + timedelta(days=int(due_days.get(priority, 7)))
    req = {f: fields.get(f, F(None)) for f in program.get('required_fields') or []}
    return {'task_id': 'TASK-' + hashlib.sha1(f"{program['id']}|{pid}|{as_of.isoformat()}".encode()).hexdigest()[:12],
            'patient_id': pid, 'program_id': program['id'], 'channel': program['outreach_channel'],
            'enrollment_channel': program['enrollment_channel'],
            'owner': owner or (slot or {}).get('slot_id'), 'priority': priority,
            'priority_rule': rule, 'due_date': due.isoformat(), 'script_template_id': program['script_template_id'],
            'required_fields': req, 'collect_at_outreach': [f for f, v in req.items() if v['value'] in (None, '', [])]}


# --- the router ---------------------------------------------------------------------------------------------------------------

def resolve_kind(cat, program_kind=None, plan_request=None):
    if isinstance(plan_request, dict) and plan_request.get('program_kind'):
        program_kind = plan_request['program_kind']
    if program_kind and program_kind in catmod.kinds(cat):
        return program_kind
    text = program_kind or (plan_request if isinstance(plan_request, str) else (plan_request or {}).get('question'))
    if text:
        from .request import infer_kind
        k = infer_kind(str(text), cat)
        if k:
            return k
    raise ValueError(f'Unknown program kind {program_kind!r}; the catalog has {catmod.kinds(cat)}')


def route(semantic_db, patient_ids, program_kind=None, as_of=None, gap_details=None, *, plan_request=None,
          catalog=None, catalog_path=None, outreach_policy=None):
    """The routed plan for patient_ids (see module doc). program_kind: a catalog kind (pharmacy_gdmt, virtual_pt,
    coverage_check, ...) or request text; plan_request: a parsed request dict (request.parse) or its text.
    gap_details: optional {patient_id: {...}} from the cohort step, recorded beside the computed gap (never overriding it).
    outreach_policy: an approved evolved policy (clinical_core/agere/evolve) to rank / fill capacity; by default the
    config switch config/agere/evolve_policy.json decides, and it is off, so routing is unchanged."""
    cat = catalog or catmod.load(catalog_path)
    kind = resolve_kind(cat, program_kind, plan_request)
    db = _DB(semantic_db)
    try:
        as_of_s = as_of or db.meta_as_of()
        if not as_of_s:
            raise ValueError('as_of is required (the semantic layer has no meta as_of)')
        as_of_d = _d(as_of_s)
        if as_of_d is None:
            raise ValueError(f'as_of must be an ISO date, not {as_of_s!r}')
        ids = list(dict.fromkeys(str(p) for p in patient_ids or [] if p not in (None, '')))
        programs = [p for p in catmod.programs_of(cat, kind) if p.get('role') != 'barrier_review']
        barrier_prog = next((p for p in catmod.programs_of(cat, kind) if p.get('role') == 'barrier_review'), None)
        gap_type = catmod.gap_type_for(cat, kind)
        gap_cfg = {**cat['gaps'][gap_type], '_barrier_policy': cat.get('barrier_policy')} if gap_type else None
        evaluator = EVALUATORS[gap_type](db, ids, as_of_d, gap_cfg) if gap_type else None
        patients = {r['patient_id']: r for r in db.by_patient('patient', ids)}
        spans = _group(db.by_patient('enrollment_span', ids))
        visits = _group(db.by_patient('encounter', ids, "type_concept = 'primary_care_visit'", (),
                                      'patient_id, record_id, event_date, attending_clinician_id, organization_id')) \
            if db.has('encounter', 'type_concept', 'attending_clinician_id') else {}
        npi_of = {}
        clin_ids = sorted({v['attending_clinician_id'] for vs in visits.values() for v in vs if v.get('attending_clinician_id')}
                          | {p['pcp_clinician_id'] for p in patients.values() if p.get('pcp_clinician_id')})
        if clin_ids and db.has('clinician', 'clinician_id', 'npi'):
            for i in range(0, len(clin_ids), CHUNK):
                ch = clin_ids[i:i + CHUNK]
                for r in db.rows(f"SELECT clinician_id, npi FROM clinician WHERE clinician_id IN ({','.join('?' * len(ch))})", tuple(ch)):
                    if r['npi']:
                        npi_of[r['clinician_id']] = r['npi']
        exc_cfg = cat.get('exclusions') or {}
        dstat = sorted(set((exc_cfg.get('deceased') or {}).get('claim_discharge_statuses') or [])
                       | set((exc_cfg.get('hospice') or {}).get('claim_discharge_statuses') or []))
        tob = (exc_cfg.get('hospice') or {}).get('type_of_bill_prefixes') or []
        claim_where = ' OR '.join(([f"discharge_status IN ({','.join('?' * len(dstat))})"] if dstat else [])
                                  + ["type_of_bill LIKE ?" for _ in tob])
        claims = _group(db.by_patient('payer_claim', ids, f'({claim_where}) AND service_from <= ?',
                                      tuple(dstat) + tuple(t + '%' for t in tob) + (as_of_d.isoformat(),),
                                      'patient_id, payer_claim_id, record_id, discharge_status, type_of_bill, service_from, service_to')) \
            if claim_where and db.has('payer_claim', 'discharge_status', 'type_of_bill') else {}
        disp = sorted({x.lower() for k in ('deceased', 'hospice') for x in (exc_cfg.get(k) or {}).get('encounter_dispositions') or []})
        encs = _group(db.by_patient('encounter', ids, f"LOWER(disposition) IN ({','.join('?' * len(disp))}) AND event_date <= ?",
                                    tuple(disp) + (as_of_d.isoformat(),), 'patient_id, record_id, disposition, event_date')) \
            if disp and db.has('encounter', 'disposition') else {}
        cov_prog = catmod.coverage_program(cat)
        due_days = (cat.get('priority') or {}).get('due_days') or {'high': 3, 'medium': 7, 'low': 14}
        rows = []
        for pid in ids:
            patient = patients.get(pid)
            psp = spans.get(pid, [])
            cov = _coverage(psp, patient, as_of_d, cat.get('coverage') or {})
            att = _attribution(cov['span'], visits.get(pid, []), patient, npi_of, as_of_d)
            age = _age(patient, as_of_d)
            psrc = [_src('patient', pid)] if patient else []
            fields = {'patient_id': F(pid, psrc), 'member_id': cov['member_id'], 'payer_id': cov['payer_id'],
                      'product': cov['product'], 'line_of_business': cov['line_of_business'], 'plan_id': cov['plan_id'],
                      'attributed_npi': att['npi'], 'attributed_practice_id': att['practice_id'], 'age': age,
                      'birth_date': F((patient or {}).get('birth_date') or None, psrc),
                      'preferred_language': F((patient or {}).get('preferred_language') or None, psrc),
                      'interpreter_needed': F((patient or {}).get('interpreter_needed'), psrc)}
            gap, metrics = (evaluator.evaluate(pid, (gap_details or {}).get(pid)) if evaluator else
                            ({'type': None, 'present': F(True), 'reason': None}, {'always': 1}))
            for k in ('missing_pillars', 'barrier_documented_pillars', 'barrier_pillars', 'ed_visit_date', 'days_since_ed', 'last_pt_date'):
                if k in gap:
                    fields[k] = gap[k]
            row = {'patient_id': pid, 'known_patient': bool(patient or psp), 'coverage': {k: v for k, v in cov.items() if k != 'span'},
                   'attribution': att, 'age': age, 'gap': gap, 'exclusions': [], 'eligibility': {}, 'route': None,
                   'program_id': None, 'vendor_slot': None, 'task': None,
                   'patient_facts': {'preferred_language': fields['preferred_language']['value'],
                                     'interpreter_needed': fields['interpreter_needed']['value']}}
            exc = _exclusions(pid, psp, claims.get(pid, []), encs.get(pid, []), as_of_d, exc_cfg)
            if not row['known_patient']:
                exc.insert(0, {'reason': 'unknown_patient', 'detail': 'no patient or enrollment record in this workspace', 'sources': []})
            if not exc and not gap['present']['value']:
                exc.append({'reason': (gap.get('reason') or 'no_gap').split(':')[0], 'detail': gap.get('reason'),
                            'sources': gap['present']['sources']})
            if exc:
                row.update(route='exclude', exclusions=exc, decision=F(f"exclude: {exc[0]['reason']}", exc[0]['sources']))
                rows.append(row)
                continue
            # Every missing pillar explained by a barrier, or a current barrier on a pillar the program would start: PharmD
            # review of the barriers (listed on the task), not standard outreach.
            to_review = bool(gap.get('all_missing_barrier_documented') or (gap.get('barrier_pillars') or {}).get('value'))
            offered = [barrier_prog] if barrier_prog and to_review else programs
            for p in offered:
                checks = _eligibility(p, cov, age)
                row['eligibility'][p['id']] = {'eligible': all(c['passed'] for c in checks), 'checks': checks}
            chosen = next((p for p in offered if row['eligibility'][p['id']]['eligible']), None)
            facts = {k: fields[k]['value'] for k in ('payer_id', 'product', 'line_of_business')} | {'practice_id': att['practice_id']['value']}
            if chosen is not None:
                slot, why = _assign(chosen, pid, facts)
                if slot is not None:
                    pr, rule = _priority(chosen, metrics)
                    row.update(route='enroll', program_id=chosen['id'], vendor_slot={**slot, 'assignment_reason': why},
                               task=_task(chosen, slot, pid, as_of_d, pr, rule, due_days, fields))
                    row['decision'] = F(f"enroll: {chosen['id']} via {slot['slot_id']}", cov['active']['sources'] + gap['present']['sources'])
                    rows.append(row)
                    continue
                row['eligibility'][chosen['id']]['checks'].append({'rule': 'vendor_slot', 'passed': False, 'detail': why})
            age_fails_everywhere = bool(offered) and all(
                any(c['rule'] == 'age' and not c['passed'] for c in row['eligibility'][p['id']]['checks']) for p in offered)
            if age_fails_everywhere:
                row.update(route='exclude', exclusions=[{'reason': 'age_ineligible', 'detail': f"age {age['value']}",
                                                         'sources': age['sources']}],
                           decision=F('exclude: age_ineligible', age['sources']))
            elif not cov['active']['value']:
                pr, rule = _priority(cov_prog, metrics)
                slot = cov_prog['vendor_slots'][0]
                row.update(route='coverage_check', program_id=cov_prog['id'], vendor_slot={**slot, 'assignment_reason': 'coverage queue'},
                           task=_task(cov_prog, slot, pid, as_of_d, pr, rule, due_days, fields),
                           decision=F(f"coverage_check: {cov['reason']}", cov['active']['sources']))
            else:
                failed = sorted({c['rule'] for e in row['eligibility'].values() for c in e['checks'] if not c['passed']})
                anchor = offered[0] if offered else cov_prog
                pr, rule = _priority(anchor, metrics)
                review = {**anchor, 'id': f"care_team_review:{kind}", 'outreach_channel': 'work_queue',
                          'enrollment_channel': 'care_team_review', 'script_template_id': 'care_team_review_v1'}
                row.update(route='care_team_review', program_id=None,
                           task=_task(review, None, pid, as_of_d, pr, rule, due_days, fields, owner='care_team'),
                           decision=F(f"care_team_review: no {kind} program accepts this coverage ({', '.join(failed)})",
                                      cov['active']['sources']))
            rows.append(row)
        plan = {'schema': SCHEMA, 'generated_by': 'deterministic_router', 'as_of': as_of_d.isoformat(), 'program_kind': kind,
                'gap_type': gap_type, 'catalog_version': cat.get('version'),
                'programs': [{k: p.get(k) for k in ('id', 'name', 'kind', 'context', 'enrollment_channel', 'vendor_slots')}
                             for p in programs + ([barrier_prog] if barrier_prog else []) + [cov_prog]],
                'contexts': {p['context']: cat['contexts'][p['context']] for p in programs if p.get('context')},
                'inputs': {'absent_tables': sorted(db.absent), 'patients_requested': len(ids)},
                'patients': rows, 'summary': summarize(rows)}
    finally:
        db.close()
    return _outreach_policy(plan, semantic_db, outreach_policy, cat)


def _outreach_policy(plan, semantic_db, policy, cat):
    """Approved evolved outreach policy (evolve/routing.py): explicit argument, else the config switch (default off)."""
    from ..evolve import routing as policy_routing
    if policy is None:
        policy = policy_routing.configured_policy(plan['program_kind'])
    if not policy:
        return plan
    return policy_routing.apply(plan, semantic_db, policy, catalog=cat)


def summarize(rows):
    def count(fn):
        c = Counter(fn(r) for r in rows)
        return dict(sorted(((k if k is not None else 'unknown'), v) for k, v in c.items()))
    routed = [r for r in rows if r['route'] != 'exclude']
    tasks = [r['task'] for r in rows if r.get('task')]
    gap_counts, barrier_pillars, barrier_types, open_barrier, stale = Counter(), Counter(), Counter(), Counter(), Counter()
    for r in rows:
        for m in (r['gap'].get('missing_pillars') or {}).get('value') or []:
            gap_counts[m] += 1
        for m in (r['gap'].get('barrier_documented_pillars') or {}).get('value') or []:
            barrier_pillars[m] += 1
        for m in (r['gap'].get('barrier_pillars') or {}).get('value') or []:
            if m not in ((r['gap'].get('barrier_documented_pillars') or {}).get('value') or []):
                open_barrier[m] += 1
            for b in {b['barrier_type'] for b in r['gap']['pillars'][m]['barriers']['value']}:
                barrier_types[b] += 1
        for m in (r['gap'].get('stale_barrier_pillars') or {}).get('value') or []:
            stale[m] += 1
    return {'patients': len(rows), 'by_route': count(lambda r: r['route']),
            'by_program': dict(Counter(r['program_id'] for r in routed if r['program_id'])),
            'by_vendor_slot': dict(Counter((r['vendor_slot'] or {}).get('slot_id') for r in routed if r.get('vendor_slot'))),
            'by_exclusion_reason': dict(Counter(r['exclusions'][0]['reason'] for r in rows if r['route'] == 'exclude')),
            'by_payer': count(lambda r: r['coverage']['payer_id']['value']),
            'by_line_of_business': count(lambda r: r['coverage']['line_of_business']['value']),
            'by_practice': count(lambda r: r['attribution']['practice_id']['value']),
            'by_attribution_method': count(lambda r: r['attribution']['method']),
            'tasks': len(tasks), 'tasks_by_priority': dict(Counter(t['priority'] for t in tasks)),
            'missing_pillar_counts': dict(gap_counts) or None,
            **({'barrier_documented_pillar_counts': dict(barrier_pillars), 'open_with_barrier_pillar_counts': dict(open_barrier),
                'barriers_by_type': dict(barrier_types), 'stale_barrier_pillar_counts': dict(stale),
                'patients_with_barrier': sum(1 for r in rows if (r['gap'].get('barrier_pillars') or {}).get('value')),
                'patients_with_stale_barrier_only': sum(1 for r in rows if (r['gap'].get('stale_barrier_pillars') or {}).get('value')
                                                        and not (r['gap'].get('barrier_pillars') or {}).get('value')),
                'all_missing_barrier_documented': sum(1 for r in rows if r['gap'].get('all_missing_barrier_documented'))}
               if barrier_pillars or open_barrier or stale else {})}


def tasks(plan):
    """The outreach task list (flat rows) of a plan."""
    out = []
    for r in plan['patients']:
        t = r.get('task')
        if not t:
            continue
        out.append({'task_id': t['task_id'], 'patient_id': r['patient_id'], 'route': r['route'], 'program_id': t['program_id'],
                    'owner': t['owner'], 'channel': t['channel'], 'priority': t['priority'], 'due_date': t['due_date'],
                    'script_template_id': t['script_template_id'],
                    'payer_id': r['coverage']['payer_id']['value'], 'product': r['coverage']['product']['value'],
                    'line_of_business': r['coverage']['line_of_business']['value'],
                    'member_id': r['coverage']['member_id']['value'],
                    'attributed_npi': r['attribution']['npi']['value'], 'attributed_practice_id': r['attribution']['practice_id']['value'],
                    'gap': _gap_text(r['gap']), 'collect_at_outreach': ';'.join(t['collect_at_outreach']),
                    'sources': ';'.join(sorted({s for f in (r['coverage']['active'], r['gap']['present'], r['attribution']['npi'])
                                                for s in f['sources']}))})
    return out


def _gap_text(gap):
    if gap.get('type') == 'gdmt_pillar_missing':
        text = 'missing ' + ','.join(gap['missing_pillars']['value'] or [])
        documented = (gap.get('barrier_documented_pillars') or {}).get('value') or []
        if documented:
            text += '; barrier_documented ' + ','.join(
                f"{n}({gap['pillars'][n]['barriers']['value'][0]['barrier_type']}: {gap['pillars'][n]['barriers']['value'][0]['detail']})"
                for n in documented)
        opened = [n for n in (gap.get('barrier_pillars') or {}).get('value') or [] if n not in documented]
        if opened:
            text += '; open_with_barrier ' + ','.join(
                f"{n}({gap['pillars'][n]['barriers']['value'][0]['barrier_type']}: {gap['pillars'][n]['barriers']['value'][0]['detail']}"
                f" [{gap['pillars'][n]['barriers']['value'][0].get('drug_key') or gap['pillars'][n]['barriers']['value'][0].get('drug_class')}])"
                for n in opened)
        return text
    if gap.get('type') == 'pt_after_ed_low_back_pain':
        return f"ED {gap['ed_visit_date']['value']} ({gap['days_since_ed']['value']}d ago), last PT {gap['last_pt_date']['value']}"
    return ''


def tasks_csv(plan):
    import csv
    import io
    rows = tasks(plan)
    cols = ['task_id', 'patient_id', 'route', 'program_id', 'owner', 'channel', 'priority', 'due_date', 'script_template_id',
            'payer_id', 'product', 'line_of_business', 'member_id', 'attributed_npi', 'attributed_practice_id', 'gap',
            'collect_at_outreach', 'sources']
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def digest(plan):
    """A stable digest of the routing decisions (route, program, slot, task essentials per patient)."""
    core = [(r['patient_id'], r['route'], r['program_id'], (r.get('vendor_slot') or {}).get('slot_id'),
             (r.get('task') or {}).get('priority'), (r.get('task') or {}).get('due_date')) for r in plan['patients']]
    return hashlib.sha256(json.dumps(core).encode()).hexdigest()


def candidates(semantic_db, program_kind, as_of=None, *, catalog=None, catalog_path=None):
    """The deterministic candidate cohort for a program kind's gap (used when a plan request names no cohort, or the
    cohort path returns no patient list): HF diagnosis on or before as_of for GDMT; a discharged ED visit with the
    diagnosis in the lookback for PT. The router then confirms each patient's gap."""
    cat = catalog or catmod.load(catalog_path)
    kind = resolve_kind(cat, program_kind)
    gap_type = catmod.gap_type_for(cat, kind)
    db = _DB(semantic_db)
    try:
        as_of_d = _d(as_of or db.meta_as_of())
        if as_of_d is None:
            raise ValueError('as_of is required (the semantic layer has no meta as_of)')
        cfg = cat['gaps'].get(gap_type) or {}
        if gap_type == 'gdmt_pillar_missing':
            pref = cfg.get('diagnosis_icd10_prefixes') or []
            if not pref or not db.has('condition', 'icd10', 'patient_id'):
                return []
            where = ' OR '.join("REPLACE(UPPER(icd10), '.', '') LIKE ?" for _ in pref)
            date_ok = " AND (event_date IS NULL OR event_date = '' OR event_date <= ?)" if db.has('condition', 'event_date') else ''
            rows = db.rows(f'SELECT DISTINCT patient_id FROM condition WHERE ({where}){date_ok} ORDER BY patient_id',
                           tuple(_norm_icd(p) + '%' for p in pref) + ((as_of_d.isoformat(),) if date_ok else ()))
            return [r['patient_id'] for r in rows if r['patient_id']]
        if gap_type == 'pt_after_ed_low_back_pain':
            ids = []
            for t in ('payer_claim', 'encounter'):
                if db.has(t, 'patient_id'):
                    ids += [r['patient_id'] for r in db.rows(f'SELECT DISTINCT patient_id FROM {t}') if r['patient_id']]
            ids = sorted(set(ids))
            ev = _PTAfterED(db, ids, as_of_d, cfg)
            return sorted(ev.events)
        return []
    finally:
        db.close()
