"""Clinical workflow tasks CT-01..CT-10: known-answer care-gap workflows answered from governed definitions.

Each task (docs/agere/REQUEST_CLINICAL_WORKFLOW_TASKS.md; generator definitions in releases/<rel>/questions/
clinical_tasks.jsonl) is a numbered set of steps evaluated on an index date (default: the snapshot as-of):
  CT-01 HFrEF GDMT gaps                 a HFrEF patients; b the pillars each is NOT on
  CT-02 documented reasons for gaps     per patient x missing pillar: reason category and where it is documented
  CT-03 GDMT under-dosing               current pillar drug below the ACC/AHA target daily dose
  CT-04 diabetes cardio-renal gap       a T2D with ASCVD or CKD; b on neither SGLT2i nor GLP-1 RA (+ reason)
  CT-05 AFib anticoagulation gap        a AFib with CHA2DS2-VASc >= 2; b not on an oral anticoagulant (+ contraindication)
  CT-06 CKD protection gap              a CKD 3+ with UACR >= 30; b not on ACEi/ARB/ARNI; c not on SGLT2i
  CT-07 statin gap                      a statin-indicated; b not on a statin; c the subset with documented intolerance
  CT-08 post-MI secondary prevention    MI in the prior 12 months not on a beta-blocker and/or a high-intensity statin
  CT-09 hyperkalemia safety             on an active MRA with latest potassium >= 5.5 mmol/L
  CT-10 notes-versus-orders conflict    documented intolerance/allergy, same drug class filled/ordered after it
Authoritative pieces stay authoritative: "on" is medication_state (picked-up fill covering the index date + grace;
91 days = the task's 90-day lookback on fill + days supply; a documented stop after the last fill means not on),
reasons are therapy_barrier / note_therapy_change (build-time note cascade) and allergy entries, doses use dose.dose_mg
and dose.per_day. Documented condition = an ICD-10 prefix (dots ignored) on an EHR diagnosis/problem-list row, a counted
payer-claim diagnosis or an encounter diagnosis dated on or before the index date. Persons who died on or before the
index date are excluded. Gaps with no structured or extracted reason can be read by System One (review(): atomic typed
questions over the patient's notes, Jev first, Perplexity fallback; only with allow_live, cost on the receipt).
Registered engine 'clinical_task' (analysis_graph CLINICAL_LOGIC); local_question() is the deterministic recognizer
the exact-local tier uses, so these tasks answer with no model call.
"""
from pathlib import Path
import re
import sqlite3

VERSION = 'clinical-tasks/1'
TASKS = {
    'CT-01': ('HFrEF GDMT gaps', ('a', 'b')), 'CT-02': ('Documented reasons for GDMT gaps', ('a',)),
    'CT-03': ('GDMT under-dosing', ('a',)), 'CT-04': ('Diabetes cardio-renal protection gap', ('a', 'b')),
    'CT-05': ('AFib anticoagulation gap', ('a', 'b')), 'CT-06': ('CKD protection gap', ('a', 'b', 'c')),
    'CT-07': ('Statin gap', ('a', 'b', 'c')), 'CT-08': ('Post-MI secondary prevention', ('a', 'b')),
    'CT-09': ('Hyperkalemia safety', ('a',)), 'CT-10': ('Notes-versus-orders safety conflict', ('a',)),
}
GRACE_DAYS = 91  # fill + days supply >= index - 90 days  <=>  covered_through (fill + supply - 1) + 91 >= index
OUTPUTS = ('rows', 'patients', 'summary')
PILLARS = {'beta-blocker': ['hf_evidence_based_beta_blocker'], 'RAAS': ['ace_inhibitor', 'arb', 'arni'],
           'MRA': ['mra'], 'SGLT2i': ['sglt2_inhibitor']}
PILLAR_STATE = {'beta-blocker': 'beta_blocker', 'RAAS': 'rasi', 'MRA': 'mra', 'SGLT2i': 'sglt2i'}  # therapy_barrier.gdmt_pillar
SUBJECTS = {**PILLARS, 'GLP-1 RA': ['glp1_agonist'], 'statin': ['statin'], 'oral anticoagulant': ['anticoagulant'],
            'any beta-blocker': ['beta_blocker']}
ASCVD = ['I20', 'I21', 'I22', 'I23', 'I24', 'I25', 'I63', 'G45', 'I70', 'I739', 'Z951', 'Z955']
CKD3 = ['N183', 'N184', 'N185', 'N186']
# 2022 AHA/ACC/HFSA target daily doses (mg/day), the table the task names. Name pattern -> (drug, target).
TARGETS = [(r'sacubitril', 'sacubitril/valsartan', 400.0), (r'carvedilol', 'carvedilol', 50.0),
           (r'metoprolol', 'metoprolol succinate', 200.0), (r'bisoprolol', 'bisoprolol', 10.0),
           (r'lisinopril', 'lisinopril', 20.0), (r'enalapril', 'enalapril', 20.0), (r'ramipril', 'ramipril', 10.0),
           (r'captopril', 'captopril', 150.0), (r'losartan', 'losartan', 150.0), (r'valsartan', 'valsartan', 320.0),
           (r'candesartan', 'candesartan', 32.0), (r'spironolactone', 'spironolactone', 25.0),
           (r'eplerenone', 'eplerenone', 50.0), (r'dapagliflozin', 'dapagliflozin', 10.0), (r'empagliflozin', 'empagliflozin', 10.0)]
HIGH_INTENSITY = [(r'atorvastatin', 40.0, 80.0), (r'rosuvastatin', 20.0, 40.0)]
REASONS = ('intolerance_adverse_effect', 'hyperkalemia', 'hypotension', 'aki_renal', 'bradycardia', 'patient_refusal',
           'cost_access', 'bleeding', 'falls', 'contraindication_other', 'none_documented')
_KEYWORDS = [(r'hyperkal|potassium|\bk\+', 'hyperkalemia'), (r'hypotens|low blood pressure|dizz|light-?head|orthostat', 'hypotension'),
             (r'\baki\b|kidney|renal|creatinine|egfr', 'aki_renal'), (r'bradycard|heart rate|slow pulse|heart block', 'bradycardia'),
             (r'bleed|hemorrhag|haemorrhag|\bgi bleed', 'bleeding'), (r'\bfalls?\b|fall risk', 'falls'),
             (r'declin|refus|preference|does not want|doesn.t want', 'patient_refusal'),
             (r'cost|afford|insurance|coverage|copay|prior auth|formulary|access', 'cost_access'),
             (r'angioedema|allerg|rash|cough|side effect|intoleran|upset|nausea|myalg|muscle|liver|lft|transaminase|'
              r'sedation|fatigue|sexual|uti|yeast|mycotic|gyneco', 'intolerance_adverse_effect'),
             (r'contraindicat|not indicated|no longer indicated', 'contraindication_other')]
_BARRIER_TYPES = {'intolerance_side_effect': 'intolerance_adverse_effect', 'allergy_angioedema': 'intolerance_adverse_effect',
                  'patient_declined': 'patient_refusal', 'cost_access': 'cost_access', 'contraindication_lab': 'contraindication_other',
                  'other': 'contraindication_other'}
INTOLERANCE_TYPES = ('intolerance_side_effect', 'allergy_angioedema')
DISCLOSURE = ('Clinical workflow task (governed clinical-tasks/1): documented condition = ICD-10 prefix on an EHR diagnosis or '
              'problem-list row, a counted payer-claim diagnosis or an encounter diagnosis on or before the index date; on a drug = '
              'medication_state filled_current with a 91-day grace (a picked-up fill whose fill date + days supply reaches the index '
              'date - 90 days; a documented stop after the last fill means not on); persons who died on or before the index date '
              'are excluded; reasons come from therapy barriers, note therapy changes and allergy entries (build-time note '
              'extraction), plus System One note review when live. A pickup is recorded supply, not confirmed ingestion.')


def _q(v): return "'" + str(v).replace("'", "''") + "'"


def _day(v): return str(v)[:10] if v else None


def reason_category(barrier_type, text):
    """Reason category from a barrier type and its free text (keywords first: 'hyperkalemia' beats 'contraindication')."""
    t = (text or '').lower()
    for rx, cat in _KEYWORDS:
        if re.search(rx, t): return cat
    return _BARRIER_TYPES.get(barrier_type or '', 'contraindication_other' if barrier_type else 'none_documented')


class Data:
    """Read-only governed lookups on one semantic-layer connection, all evaluated on the index date."""

    def __init__(self, db, index, keep=None, grace=GRACE_DAYS):
        from .medication_state import _tables
        self.db, self.index, self.grace = db, index, grace
        self.tables = _tables(db)
        dead = {r[0] for r in db.execute('SELECT patient_id FROM death WHERE death_date IS NOT NULL AND substr(death_date, 1, 10) <= ?',
                                         (index,))} if 'death' in self.tables else set()
        self.patients = {r[0]: {'birth_date': r[1], 'sex': r[2]} for r in db.execute('SELECT patient_id, birth_date, sex_reported FROM patient')
                         if r[0] not in dead and (keep is None or r[0] in keep)}
        self._states = {}

    def coded(self, prefixes, since=None):
        """{patient: latest documented date} for ICD-10 prefixes (dots ignored) dated in (since, index]."""
        like = lambda col: '(' + ' OR '.join(f"replace(upper({col}), '.', '') LIKE {_q(p + '%')}" for p in prefixes) + ')'
        lo = f" AND d > {_q(since)}" if since else ''
        parts = []
        if 'condition' in self.tables:
            diag = 'is_diagnosis = 1' + (" OR (is_diagnosis IS NULL AND diagnosis_basis = 'ehr_problem_list')"
                                         if 'diagnosis_basis' in self.tables['condition'] else '')
            parts.append(f"SELECT patient_id, substr(coalesce(event_date, recorded_date), 1, 10) AS d FROM condition WHERE {like('icd10')} AND ({diag})")
        if 'payer_claim_diagnosis' in self.tables:
            parts.append(f"SELECT patient_id, substr(service_from, 1, 10) FROM payer_claim_diagnosis WHERE counts_in_metrics = 1 AND {like('icd10')}")
        if 'encounter_diagnosis' in self.tables:
            parts.append(f"SELECT patient_id, substr(event_date, 1, 10) FROM encounter_diagnosis WHERE {like('icd10')}")
        if 'problem_list_entry' in self.tables:
            parts.append(f"SELECT patient_id, substr(first_recorded, 1, 10) FROM problem_list_entry WHERE {like('icd10')}")
        if not parts: return {}
        sql = (f"SELECT patient_id, max(d) FROM ({' UNION ALL '.join(parts)}) WHERE d IS NOT NULL AND d <= {_q(self.index)}{lo} GROUP BY 1")
        return {p: d for p, d in self.db.execute(sql) if p in self.patients}

    def states(self, subject):
        """{patient: medication_state row} for one SUBJECTS key (cached)."""
        if subject not in self._states:
            from .medication_state import state_sql
            sql = state_sql({subject: SUBJECTS[subject]}, self.index, self.tables, grace=self.grace)
            self.db.row_factory = sqlite3.Row
            try: rows = [dict(r) for r in self.db.execute(sql)]
            finally: self.db.row_factory = None
            self._states[subject] = {r['patient_id']: r for r in rows if r['patient_id'] in self.patients}
        return self._states[subject]

    def on(self, subject, pid): return (self.states(subject).get(pid) or {}).get('state') == 'filled_current'

    def latest_lab(self, concept):
        """{patient: (value, date)}: latest current, non-excluded result on or before the index date; same-day ties -> highest."""
        out = {}
        for pid, day, value in self.db.execute(
                "SELECT patient_id, substr(event_date, 1, 10), value_canonical FROM measurement WHERE concept = ? AND "
                "coalesce(result_current, 1) = 1 AND excluded_reason IS NULL AND typeof(value_canonical) IN ('integer', 'real') "
                "AND event_date IS NOT NULL AND substr(event_date, 1, 10) <= ?", (concept, self.index)):
            if pid in self.patients and (pid not in out or (day, value) > (out[pid][1], out[pid][0])): out[pid] = (value, day)
        return out

    def ever_lab(self, concept, threshold):
        return {pid for pid, value in self.db.execute(
            "SELECT patient_id, value_canonical FROM measurement WHERE concept = ? AND coalesce(result_current, 1) = 1 AND "
            "excluded_reason IS NULL AND value_canonical >= ? AND substr(event_date, 1, 10) <= ?", (concept, threshold, self.index))
            if pid in self.patients}

    def latest_ef(self):
        """{patient: (EF %, date)}: most recent documented ejection fraction (notes / reports), ties -> lowest."""
        out = {}
        if 'note_fact' not in self.tables: return out
        for pid, day, value in self.db.execute(
                "SELECT patient_id, substr(event_date, 1, 10), CAST(value AS REAL) FROM note_fact WHERE fact_type = 'ejection_fraction' "
                "AND coalesce(negated, 0) = 0 AND coalesce(experiencer, 'patient') = 'patient' AND typeof(value) IN ('integer', 'real') "
                "AND event_date IS NOT NULL AND substr(event_date, 1, 10) <= ?", (self.index,)):
            if pid in self.patients and (pid not in out or day > out[pid][1] or (day == out[pid][1] and value < out[pid][0])):
                out[pid] = (value, day)
        return out

    def age(self, pid):
        b = _day(self.patients[pid]['birth_date'])
        if not b: return None
        y, m, d = map(int, self.index.split('-')); by, bm, bd = map(int, b.split('-'))
        return y - by - ((m, d) < (bm, bd))

    def barriers(self, classes=None, pillar=None):
        """Documented reasons (non-negated barriers, note stops/declines) dated on or before the index date (or undated)."""
        out = []
        if 'therapy_barrier' in self.tables:
            cond = []
            if classes: cond.append(f"drug_class IN ({', '.join(map(_q, classes))})")
            if pillar: cond.append(f'gdmt_pillar = {_q(pillar)}')
            where = ('(' + ' OR '.join(cond) + ') AND ') if cond else ''
            for r in self.db.execute(
                    f"SELECT patient_id, drug_class, barrier_type, coalesce(detail, evidence_text), substr(coalesce(event_date, last_date), 1, 10), "
                    f"source_type, record_id, drug_key FROM therapy_barrier WHERE {where}coalesce(negated, 0) = 0"):
                if r[0] in self.patients and (r[4] is None or r[4] <= self.index):
                    out.append({'patient_id': r[0], 'drug_class': r[1], 'barrier_type': r[2], 'text': r[3], 'date': r[4],
                                'location': 'structured' if r[5] in ('allergy_entry', 'measurement') else 'note_text',
                                'source': r[5], 'record_id': r[6], 'drug_key': r[7], 'category': reason_category(r[2], r[3])})
        if 'note_therapy_change' in self.tables and classes:
            for r in self.db.execute(
                    f"SELECT patient_id, drug_class, action, coalesce(reason, evidence_text), substr(event_date, 1, 10), record_id, drug_key "
                    f"FROM note_therapy_change WHERE action IN ('stop', 'declined') AND drug_class IN ({', '.join(map(_q, classes))})"):
                if r[0] in self.patients and (r[4] is None or r[4] <= self.index):
                    cat = reason_category('patient_declined' if r[2] == 'declined' else None, r[3])
                    out.append({'patient_id': r[0], 'drug_class': r[1], 'barrier_type': 'note_' + r[2], 'text': r[3], 'date': r[4],
                                'location': 'note_text', 'source': 'note_therapy_change', 'record_id': r[5], 'drug_key': r[6],
                                'category': cat if cat != 'none_documented' else 'contraindication_other'})
        return out

    def fills(self, classes):
        """Picked-up fills on or before the index date: [{patient_id, drug_class, drug_name, date, record_id, payload}]."""
        from .concepts import picked_up_sql
        fill = picked_up_sql('m', reversed_column='fill_reversed' in self.tables['medication'])
        join = 'LEFT JOIN record r ON r.record_id = m.record_id' if 'record' in self.tables else ''
        payload = 'r.payload' if join else 'NULL'
        rows = self.db.execute(
            f"SELECT m.patient_id, m.drug_class, m.drug_name, substr(m.event_date, 1, 10), m.record_id, {payload} FROM medication m {join} "
            f"WHERE {fill} AND m.drug_class IN ({', '.join(map(_q, classes))}) AND m.event_date IS NOT NULL AND substr(m.event_date, 1, 10) <= ?",
            (self.index,))
        return [{'patient_id': r[0], 'drug_class': r[1], 'drug_name': r[2], 'date': r[3], 'record_id': r[4], 'payload': r[5]}
                for r in rows if r[0] in self.patients]


def drug_of(name):
    """(drug, target daily mg) for an HF GDMT drug name; metoprolol counts only as succinate/ER/XL."""
    t = (name or '').lower()
    for rx, drug, target in TARGETS:
        if re.search(rx, t):
            if drug == 'metoprolol succinate' and not re.search(r'succinate|\ber\b|\bxl\b|extended|24 ?hr', t): return None, None
            if drug == 'valsartan' and 'sacubitril' in t: continue
            return drug, target
    return None, None


def fill_daily_mg(fill):
    """Daily mg from a fill: claim = mg per unit x quantity / days supply; dispense = label dose x frequency. None if unstated."""
    import json
    from .dose import dose_mg, per_day
    p = {}
    try: p = json.loads(fill['payload']) if fill.get('payload') else {}
    except ValueError: p = {}
    name = (fill.get('drug_name') or p.get('drug_name') or '').lower()
    combo = re.search(r'(\d+(?:\.\d+)?)\s*(?:mg)?\s*/\s*(?:[a-z]+\s*)?(\d+(?:\.\d+)?)\s*mg', name) if 'sacubitril' in name else None
    unit = (float(combo.group(1)) + float(combo.group(2))) if combo else dose_mg(name)
    qty, days = p.get('quantity_dispensed') or p.get('quantity'), p.get('days_supply') or p.get('days_supply_reported')
    if unit and qty and days:
        try: return round(unit * float(qty) / float(days), 2)
        except (TypeError, ValueError, ZeroDivisionError): pass
    label = p.get('label_dose')
    if label:
        per = per_day(p.get('label_frequency') or '')
        mg = dose_mg(str(label))
        if mg is None and unit and re.fullmatch(r'\s*\d+(?:\.\d+)?\s*(?:tab\w*|cap\w*)?\s*', str(label)):
            mg = unit * float(re.findall(r'\d+(?:\.\d+)?', str(label))[0])
        if mg and per: return round(mg * per, 2)
    return None


# ---- tasks -----------------------------------------------------------------------------------------------------------
def hfref(d):
    hf, coded, ef = d.coded(['I50']), d.coded(['I502', 'I504']), d.latest_ef()
    out = {}
    for pid in hf:
        if pid in coded: out[pid] = 'coded_hfref' + (f' (latest EF {ef[pid][0]:g}% {ef[pid][1]})' if pid in ef else '')
        elif pid in ef and ef[pid][0] <= 40: out[pid] = f'heart failure + latest EF {ef[pid][0]:g}% on {ef[pid][1]}'
    return out


def _state_text(d, subject, pid):
    r = d.states(subject).get(pid) or {}
    return f"{r.get('state', 'never_recorded')}: {r.get('basis') or 'no record'}"


def ct01(d):
    a = hfref(d)
    rows_b = []
    for pid in sorted(a):
        missing = [p for p in PILLARS if not d.on(p, pid)]
        if missing:
            rows_b.append({'patient_id': pid, 'missing_pillars': missing, 'hfref_basis': a[pid],
                           **{f'{PILLAR_STATE[p]}_state': _state_text(d, p, pid) for p in PILLARS}})
    return {'a': [{'patient_id': p, 'hfref_basis': a[p]} for p in sorted(a)], 'b': rows_b}


def _reasons(d, pid, classes, pillar=None):
    found = [b for b in d.barriers(classes, pillar) if b['patient_id'] == pid]
    return sorted(found, key=lambda b: (b['date'] or '', b['record_id'] or ''), reverse=True)


def _reason_cols(found):
    if not found: return {'reason_category': 'none_documented', 'reason_location': None, 'reason_evidence': None,
                          'reason_record_id': None, 'reason_date': None}
    top = found[0]
    locations = sorted({b['location'] for b in found})
    return {'reason_category': top['category'], 'reason_location': 'structured' if 'structured' in locations else 'note_text_only',
            'reason_evidence': (top['text'] or top['barrier_type'] or '')[:200], 'reason_record_id': top['record_id'],
            'reason_date': top['date'], 'reason_categories': sorted({b['category'] for b in found})}


def ct02(d):
    gaps = ct01(d)['b']
    allb = d.barriers([c for cs in PILLARS.values() for c in cs])
    by = {}
    for b in allb: by.setdefault(b['patient_id'], []).append(b)
    rows = []
    for g in gaps:
        for p in g['missing_pillars']:
            found = sorted([b for b in by.get(g['patient_id'], []) if b['drug_class'] in PILLARS[p]],
                           key=lambda b: (b['date'] or '', b['record_id'] or ''), reverse=True)
            if not found and 'therapy_barrier' in d.tables:
                found = _reasons(d, g['patient_id'], None, PILLAR_STATE[p])
            rows.append({'patient_id': g['patient_id'], 'pillar': p, **_reason_cols(found), 'pillar_state': g[f'{PILLAR_STATE[p]}_state']})
    return {'a': rows}


def ct03(d, include_documented_reason=False):
    a = hfref(d)
    classes = [c for cs in PILLARS.values() for c in cs]
    fills = {}
    for f in d.fills(classes):
        if f['patient_id'] in a: fills.setdefault((f['patient_id'], f['drug_class']), []).append(f)
    tol = d.barriers(classes)
    rows = []
    for pid in sorted(a):
        for p, cs in PILLARS.items():
            if not d.on(p, pid): continue
            cand = [f for c in cs for f in fills.get((pid, c), []) if drug_of(f['drug_name'])[0]]
            if not cand: continue
            latest = max(cand, key=lambda f: (f['date'], drug_of(f['drug_name'])[0]))
            drug, target = drug_of(latest['drug_name'])
            dosed = sorted((f for f in cand if drug_of(f['drug_name'])[0] == drug and fill_daily_mg(f) is not None),
                           key=lambda f: f['date'])
            if not dosed: continue
            mg = fill_daily_mg(dosed[-1])
            if mg >= target: continue
            barrier = [b for b in tol if b['patient_id'] == pid and b['drug_class'] in cs]
            if barrier and not include_documented_reason: continue
            rows.append({'patient_id': pid, 'pillar': p, 'drug': drug, 'daily_dose_mg': mg, 'target_daily_dose_mg': target,
                         'fill_date': dosed[-1]['date'], 'fill_record_id': dosed[-1]['record_id'],
                         'documented_titration_barrier': (barrier[0]['category'] if barrier else None)})
    return {'a': rows}


def ct04(d):
    t2d, ascvd, ckd = d.coded(['E11']), d.coded(ASCVD), d.coded(['N18'])
    a = sorted(p for p in t2d if p in ascvd or p in ckd)
    b = []
    for pid in a:
        if d.on('SGLT2i', pid) or d.on('GLP-1 RA', pid): continue
        b.append({'patient_id': pid, 'basis': ' + '.join(x for x, s in (('ASCVD', ascvd), ('CKD', ckd)) if pid in s),
                  **_reason_cols(_reasons(d, pid, PILLARS['SGLT2i'] + SUBJECTS['GLP-1 RA'])),
                  'sglt2i_state': _state_text(d, 'SGLT2i', pid), 'glp1_state': _state_text(d, 'GLP-1 RA', pid)})
    return {'a': [{'patient_id': p} for p in a], 'b': b}


def cha2ds2_vasc(d, pid, codes):
    age, sex = d.age(pid), (d.patients[pid]['sex'] or '').upper()[:1]
    score = sum(w for k, w in (('hf', 1), ('htn', 1), ('dm', 1), ('stroke', 2), ('vascular', 1)) if pid in codes[k])
    score += 2 if (age or 0) >= 75 else 1 if (age or 0) >= 65 else 0
    return score + (sex == 'F')


def ct05(d):
    codes = {'hf': d.coded(['I50']), 'htn': d.coded(['I10', 'I11', 'I12', 'I13', 'I15', 'I16']), 'dm': d.coded(['E10', 'E11']),
             'stroke': d.coded(['I63', 'G45', 'I74', 'Z8673']), 'vascular': d.coded(['I21', 'I22', 'I252', 'I70', 'I739'])}
    af = d.coded(['I48'])
    a = [{'patient_id': p, 'cha2ds2_vasc': s} for p in sorted(af) for s in [cha2ds2_vasc(d, p, codes)] if s >= 2]
    b = []
    for r in a:
        if d.on('oral anticoagulant', r['patient_id']): continue
        found = _reasons(d, r['patient_id'], SUBJECTS['oral anticoagulant'])
        cols = _reason_cols(found)
        cat = cols['reason_category']
        cols['contraindication_category'] = cat if cat in ('bleeding', 'falls', 'none_documented') else 'other'
        b.append({**r, **cols, 'anticoagulant_state': _state_text(d, 'oral anticoagulant', r['patient_id'])})
    return {'a': a, 'b': b}


def ct06(d):
    ckd, uacr = d.coded(CKD3), d.latest_lab('uacr')
    a = sorted(p for p in ckd if p in uacr and uacr[p][0] >= 30)
    row = lambda p: {'patient_id': p, 'latest_uacr_mg_g': uacr[p][0], 'uacr_date': uacr[p][1]}
    return {'a': [row(p) for p in a],
            'b': [{**row(p), 'raas_state': _state_text(d, 'RAAS', p)} for p in a if not d.on('RAAS', p)],
            'c': [{**row(p), 'sglt2i_state': _state_text(d, 'SGLT2i', p)} for p in a if not d.on('SGLT2i', p)]}


def ct07(d):
    ascvd, dm, ldl = d.coded(ASCVD), d.coded(['E10', 'E11']), d.ever_lab('ldl', 190)
    basis = {}
    for p in d.patients:
        why = [w for w, ok in (('ASCVD', p in ascvd), ('diabetes age 40-75', p in dm and 40 <= (d.age(p) or 0) <= 75),
                               ('LDL >= 190', p in ldl)) if ok]
        if why: basis[p] = ' + '.join(why)
    a = sorted(basis)
    b = [{'patient_id': p, 'indication': basis[p], 'statin_state': _state_text(d, 'statin', p)} for p in a if not d.on('statin', p)]
    intol = {x['patient_id'] for x in d.barriers(['statin']) if x['barrier_type'] in INTOLERANCE_TYPES
             or x['category'] == 'intolerance_adverse_effect'}
    c = [{**r, **_reason_cols(_reasons(d, r['patient_id'], ['statin']))} for r in b if r['patient_id'] in intol]
    return {'a': [{'patient_id': p, 'indication': basis[p]} for p in a], 'b': b, 'c': c}


def _year_before(index):
    y, rest = index[:4], index[4:]
    return f'{int(y) - 1}{rest}' if rest != '-02-29' else f'{int(y) - 1}-02-28'


def ct08(d):
    mi = d.coded(['I21', 'I22'], since=_year_before(d.index))
    statin_fills = {}
    for f in d.fills(['statin']):
        if f['patient_id'] in mi: statin_fills.setdefault(f['patient_id'], []).append(f)
    rows = []
    for pid in sorted(mi):
        st = d.states('statin').get(pid) or {}
        hi = False
        if d.on('statin', pid) and statin_fills.get(pid):
            latest = max(statin_fills[pid], key=lambda f: f['date'])
            from .dose import dose_mg
            mg = dose_mg((latest['drug_name'] or '').lower())
            hi = any(re.search(rx, (latest['drug_name'] or '').lower()) and mg is not None and lo <= mg <= hiv for rx, lo, hiv in HIGH_INTENSITY)
        no_bb, no_hi = not d.on('any beta-blocker', pid), not hi
        if no_bb or no_hi:
            rows.append({'patient_id': pid, 'mi_date': mi[pid], 'missing': [x for x, m in (('beta-blocker', no_bb), ('high-intensity statin', no_hi)) if m],
                         'beta_blocker_state': _state_text(d, 'any beta-blocker', pid), 'statin_state': st.get('state', 'never_recorded')})
    return {'a': [{'patient_id': p, 'mi_date': mi[p]} for p in sorted(mi)], 'b': rows}


def ct09(d):
    k = d.latest_lab('potassium')
    return {'a': [{'patient_id': p, 'latest_potassium_mmol_l': k[p][0], 'potassium_date': k[p][1], 'mra_state': _state_text(d, 'MRA', p)}
                  for p in sorted(d.patients) if p in k and k[p][0] >= 5.5 and d.on('MRA', p)]}


ALLERGY_CLASSES = {'sulfa': 'sulfonamide_antibiotic', 'sulfonamide': 'sulfonamide_antibiotic', 'statins': 'statin', 'statin': 'statin',
                   'ace inhibitors': 'ace_inhibitor', 'ace inhibitor': 'ace_inhibitor', 'nsaids': 'nsaid', 'nsaid': 'nsaid',
                   'opioids': 'opioid', 'beta blockers': 'beta_blocker', 'cephalosporins': 'cephalosporin',
                   'fluoroquinolones': 'fluoroquinolone', 'macrolides': 'macrolide', 'tetracyclines': 'tetracycline_antibiotic'}


def allergy_documents(d):
    """Structured allergy entries naming a drug (substance -> drug class via drug_classes or the class-word table)."""
    if 'allergy_entry' not in d.tables: return []
    from . import drug_classes
    out = []
    for pid, sub, raw, react, day, rid in d.db.execute(
            "SELECT patient_id, lower(substance), substance_received, reaction, substr(coalesce(first_recorded, last_confirmed), 1, 10), "
            "record_id FROM allergy_entry WHERE coalesce(no_known_allergies, 0) = 0 AND coalesce(status, 'active') NOT IN "
            "('inactive', 'resolved', 'entered_in_error', 'refuted')"):
        if pid not in d.patients or not sub or not day or day > d.index: continue
        classes = drug_classes.classify(sub) or ([ALLERGY_CLASSES[sub]] if sub in ALLERGY_CLASSES else [])
        for c in classes:
            out.append({'patient_id': pid, 'drug_class': c, 'barrier_type': 'allergy_entry', 'text': raw or sub, 'date': day,
                        'location': 'structured', 'source': 'allergy_entry', 'record_id': rid, 'drug_key': sub,
                        'category': 'intolerance_adverse_effect'})
    return out


def ct10(d):
    docs = [b for b in d.barriers() if b['drug_class'] and b['date'] and
            (b['barrier_type'] in INTOLERANCE_TYPES or b['category'] in ('intolerance_adverse_effect',))] + allergy_documents(d)
    if not docs: return {'a': []}
    classes = sorted({b['drug_class'] for b in docs})
    later = {}
    meds = d.db.execute(
        f"SELECT patient_id, drug_class, drug_name, substr(event_date, 1, 10), evidence, record_id FROM medication WHERE drug_class IN "
        f"({', '.join(map(_q, classes))}) AND evidence IN ('dispensed', 'claim', 'payer_claim', 'ordered') AND event_date IS NOT NULL "
        "AND substr(event_date, 1, 10) <= ?", (d.index,))
    from .concepts import FILL_STATUSES
    for pid, cls, name, day, ev, rid in meds:
        later.setdefault((pid, cls), []).append((day, ev, name, rid))
    rows, seen = [], set()
    for b in sorted(docs, key=lambda b: b['date']):
        after = [x for x in later.get((b['patient_id'], b['drug_class']), []) if x[0] > b['date']]
        if not after or (b['patient_id'], b['drug_class']) in seen: continue
        seen.add((b['patient_id'], b['drug_class']))
        last = max(after)
        rows.append({'patient_id': b['patient_id'], 'drug': b['drug_key'] or b['drug_class'], 'drug_class': b['drug_class'],
                     'documentation_date': b['date'], 'documentation': (b['text'] or b['barrier_type'])[:200],
                     'documentation_location': b['location'], 'latest_fill_or_order_date': last[0], 'latest_evidence': last[1],
                     'latest_drug_name': last[2], 'record_id': last[3]})
    return {'a': rows}


_ON = 'not on = no picked-up fill covering the index date within a 90-day lookback (medication_state filled_current, 91-day grace)'
_HFREF = 'HFrEF = heart failure I50 plus HFrEF code I50.2/I50.4 or latest documented EF <= 40%'
DEFINITIONS = {  # the governed definition each task's node carries (validated; read by the literal check)
    'CT-01': f'{_HFREF}; which of the four (4) GDMT pillars (beta-blocker, RAAS ACEi/ARB/ARNI, MRA, SGLT2i) each is missing; {_ON}',
    'CT-02': f'{_HFREF}; for each missing GDMT pillar of the four (4): documented reason category and where documented (structured '
             f'allergy/barrier vs notes text: intolerance, hyperkalemia, hypotension, AKI/renal, bradycardia, refusal, cost, '
             f'contraindication, none documented); {_ON}',
    'CT-03': f'{_HFREF}; current GDMT drug daily dose from the latest fill below the 2022 ACC/AHA target daily dose (carvedilol 50, '
             'metoprolol succinate 200, bisoprolol 10, sacubitril/valsartan 400, lisinopril 20, enalapril 20, ramipril 10, '
             'captopril 150, losartan 150, valsartan 320, candesartan 32, spironolactone 25, eplerenone 50, dapagliflozin 10, '
             'empagliflozin 10 mg/day), no documented titration barrier',
    'CT-04': f'type 2 diabetes E11 with ASCVD (I20-I25, I63, G45, I70, I73.9, Z95.1, Z95.5) or CKD N18; not on SGLT2i and not on GLP-1 RA; {_ON}',
    'CT-05': f'atrial fibrillation afib I48 with CHA2DS2-VASc >= 2 (HF 1, hypertension 1, age >= 75 2, 65-74 1, diabetes 1, '
             f'stroke/TIA 2, vascular 1, female 1); not on an oral anticoagulant; {_ON}',
    'CT-06': f'CKD stage 3 or higher (N18.3-N18.6) with latest UACR >= 30 mg/g albuminuria; not on ACEi/ARB/ARNI; not on SGLT2i; {_ON}',
    'CT-07': f'statin indicated = ASCVD, or diabetes aged 40-75, or LDL >= 190 mg/dL; not on a statin; subset with documented statin intolerance; {_ON}',
    'CT-08': f'myocardial infarction MI (I21/I22) in the prior 12 months (last year, 365 days); not on a beta-blocker and/or not on '
             f'a high-intensity statin (atorvastatin 40-80, rosuvastatin 20-40 mg); {_ON}',
    'CT-09': 'on an active MRA (spironolactone, eplerenone; filled_current) with latest potassium >= 5.5 mmol/L (hyperkalemia); latest K and date',
    'CT-10': 'documented intolerance or allergy (structured allergy entry, therapy barrier or notes) to a drug class still filled or ordered after the documentation date',
}


RUN = {'CT-01': ct01, 'CT-02': ct02, 'CT-03': ct03, 'CT-04': ct04, 'CT-05': ct05, 'CT-06': ct06, 'CT-07': ct07,
       'CT-08': ct08, 'CT-09': ct09, 'CT-10': ct10}
FINAL = {'CT-01': 'b', 'CT-02': 'a', 'CT-03': 'a', 'CT-04': 'b', 'CT-05': 'b', 'CT-06': 'b', 'CT-07': 'b', 'CT-08': 'b',
         'CT-09': 'a', 'CT-10': 'a'}


# ---- registered engine -----------------------------------------------------------------------------------------------
def validate(p):
    if not isinstance(p, dict) or set(p) - {'task', 'step', 'index_date', 'grace_days', 'output', 'include_documented_reason', 'definition'}:
        raise ValueError('clinical_task parameters: task, step, index_date, grace_days, output, include_documented_reason, definition')
    task = str(p.get('task') or '').upper()
    if task not in TASKS: raise ValueError(f'task: one of {", ".join(TASKS)}')
    if p.get('definition') not in (None, '', DEFINITIONS[task]): raise ValueError(f'definition: the governed {task} definition or empty')
    step = p.get('step') or FINAL[task]
    if step not in TASKS[task][1] + ('all',): raise ValueError(f'step for {task}: {TASKS[task][1]} or all')
    idx = p.get('index_date') or ''
    if idx and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', idx): raise ValueError('index_date: YYYY-MM-DD or empty (= snapshot as-of)')
    grace = p.get('grace_days', GRACE_DAYS)
    if type(grace) is not int or not 0 <= grace <= 400: raise ValueError('grace_days: 0..400')
    if p.get('output', 'rows') not in OUTPUTS: raise ValueError(f'output: {OUTPUTS}')
    return {'task': task, 'step': step, 'index_date': idx, 'grace_days': grace, 'output': p.get('output', 'rows'),
            'include_documented_reason': bool(p.get('include_documented_reason', False)), 'definition': DEFINITIONS[task]}


def target_of(path):
    from . import semantic_layer
    return semantic_layer.ensure(path) if (Path(path)/'run.json').exists() else Path(path)/'semantic.sqlite3'


def run(db, params, as_of, keep=None):
    p = validate(params)
    index = p['index_date'] or str(as_of)[:10]
    if index > str(as_of)[:10]: raise ValueError('index_date is after the snapshot as-of date')
    d = Data(db, index, keep, p['grace_days'])
    steps = RUN[p['task']](d, p['include_documented_reason']) if p['task'] == 'CT-03' else RUN[p['task']](d)
    return report(steps, p, index, len(d.patients))


def execute(path, patient_ids, params, as_of):
    from .sql_query import register
    db = register(sqlite3.connect(f'file:{target_of(path)}?mode=ro', uri=True))
    try:
        return run(db, params, as_of, set(patient_ids) if patient_ids is not None else None)
    finally:
        db.close()


def report(steps, p, index, evaluated):
    chosen = list(TASKS[p['task']][1]) if p['step'] == 'all' else [p['step']]
    rows = [{'step': s, **r} for s in chosen for r in steps.get(s, [])] if p['step'] == 'all' else steps.get(p['step'], [])
    ids = sorted({r['patient_id'] for r in rows})
    summary = [{'step': s, 'rows': len(v), 'patients': len({r['patient_id'] for r in v})} for s, v in steps.items()]
    return {'schema': VERSION, 'task': p['task'], 'title': TASKS[p['task']][0], 'step': p['step'], 'index_date': index,
            'parameters': p, 'steps': steps, 'rows': rows, 'patient_ids': ids, 'summary': summary,
            'evaluated_patients': evaluated, 'method': DISCLOSURE, 'new_paid_api_calls': 0}


def per_patient(rows):
    """Collapse several rows per patient into one: equal values kept, differing values listed in row order."""
    out = {}
    for r in rows:
        o = out.setdefault(r['patient_id'], {})
        for k, v in r.items():
            if k not in o: o[k] = v
            elif o[k] != v:
                o[k] = (o[k] if isinstance(o[k], list) and o.get('__multi_' + k) else [o[k]]) + [v]; o['__multi_' + k] = True
    return [{k: v for k, v in o.items() if not k.startswith('__multi_')} for o in out.values()]


def answer(report_, plan, question):
    """Answer contract for analysis_graph.build_answer_core."""
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    out = report_['parameters']['output']
    table = report_['summary'] if out == 'summary' else ([{'patient_id': i} for i in report_['patient_ids']] if out == 'patients'
                                                         else report_['rows'])
    from .analysis_graph import requested_shape
    shape = requested_shape(question or '')
    if out == 'rows' and len(table) > len(report_['patient_ids']) and shape in ('patient_set', 'count'):
        table = per_patient(table)  # one row per patient
    kind = shape if shape in ('patient_set', 'count') else 'table'  # the requested shape; the table stays as evidence
    steps = '; '.join(f"step {s['step']}: {s['patients']} patients" for s in report_['summary'])
    review = report_.get('system_one_review')
    lim = [DISCLOSURE] + ([review['note']] if review else [])
    return {'status': 'partial' if partial else 'answered', 'answer_type': kind, 'table': table, 'count': len(report_['patient_ids']),
            'patient_ids': report_['patient_ids'],
            'value': f"{report_['task']} {report_['title']} on {report_['index_date']}: {len(report_['patient_ids'])} patients ({steps}).",
            'method': report_['method'], 'definitions': [{'term': f"{report_['task']} {report_['title']}", 'basis': DISCLOSURE}],
            'limitations': lim, 'membership_role': 'selected'}


# ---- System One note review (CT-02 gaps with no structured or extracted reason) --------------------------------------
REVIEW_OPTIONS = {**{r: r.replace('_', ' ') for r in REASONS if r not in ('bleeding', 'falls', 'none_documented')},
                  'none_documented': 'No reason for not taking it is documented', 'unknown': 'Insufficient or contradictory evidence'}
DRUG_TEXT = {'beta-blocker': 'an evidence-based heart-failure beta blocker (carvedilol, metoprolol succinate or bisoprolol)',
             'RAAS': 'an ACE inhibitor, ARB or sacubitril/valsartan', 'MRA': 'spironolactone or eplerenone',
             'SGLT2i': 'an SGLT2 inhibitor'}


def review(stack, report_, *, decide=None):
    """CT-02: ask System One (atomic typed questions over each patient's notes) for gaps still 'none_documented'.
    Only with stack.allow_live; capped by atomic_questions.max_patients(); cost and calls go on the report (receipt)."""
    if report_.get('task') != 'CT-02': return report_
    open_ = [r for r in report_['rows'] if r['reason_category'] == 'none_documented']
    if not open_:
        return report_
    if not getattr(stack, 'allow_live', False) and decide is None:
        report_['system_one_review'] = {'reviewed': 0, 'open_gaps': len(open_), 'note': (
            f"{len(open_)} gap(s) have no structured or extracted reason; System One note review runs only with live calls allowed.")}
        return report_
    from . import atomic_questions as aq
    pillars = sorted({r['pillar'] for r in open_})
    subs = [{'key': PILLAR_STATE[p], 'atom': f'Is a reason documented for this patient not taking {DRUG_TEXT[p]}? Which one?',
             'options': REVIEW_OPTIONS, 'subject': PILLAR_STATE[p], 'task_key': 'atomic_gap_reason'} for p in pillars]
    params = {'question': f"Documented reasons for GDMT gaps ({', '.join(pillars)})", 'sub_questions': subs, 'aggregation': 'latest',
              'returns': 'table', 'unit': 'patient', 'select_labels': [], 'sources': ['passages', 'medication_state']}
    ids = sorted({r['patient_id'] for r in open_})
    detail = aq.run(stack, ids, params, report_['index_date'], decide=decide)
    cells = {(c['patient_id'], c['key']): c for c in detail['cells']}
    filled = 0
    for r in open_:
        c = cells.get((r['patient_id'], PILLAR_STATE[r['pillar']]))
        if c and c['label'] not in ('unknown', 'none_documented'):
            src = (c.get('sources') or [{}])[0]
            r.update(reason_category=c['label'], reason_location='note_text_only (System One)', reason_evidence=(src.get('text') or '')[:200],
                     reason_record_id=src.get('record_id'), reason_date=src.get('date'), reason_decided_by=c.get('decided_by'))
            filled += 1
        elif c:
            r['reason_review'] = f"System One: {c['label']}"
    sys1 = detail['system_one']
    report_['new_paid_api_calls'] = report_.get('new_paid_api_calls', 0) + detail['new_paid_api_calls']
    report_['system_one_review'] = {
        'reviewed': len(detail['evaluated_patient_ids']), 'open_gaps': len(open_), 'reasons_found': filled, 'capped': detail['capped'],
        'new_paid_api_calls': detail['new_paid_api_calls'], 'estimated_usd': sys1['estimated_usd'], 'decided_by': sys1['decided_by'],
        'note': (f"System One read the notes of {len(detail['evaluated_patient_ids'])} of {len(ids)} patients with an unexplained gap "
                 f"and found {filled} documented reason(s) (~${sys1['estimated_usd']:.4f})"
                 + ('; capped, the rest were not read.' if detail['capped'] else '.'))}
    return report_


# ---- deterministic recognizer (exact-local tier) ---------------------------------------------------------------------
_NOT = r"\b(?:not|missing|without|isn'?t|aren'?t|no|lack\w*|gaps?|untreated|off)\b"
_HF = r'\b(?:hfref|hf ?r ?ef|reduced (?:ejection fraction|ef)|systolic heart failure|heart failure|\bhf\b|chf)\b'
_GDMT = r'\b(?:gdmt|guideline[- ]directed|pillars?|four (?:hf |heart failure )?(?:drugs|medications|therapies))\b'


def recognize(question):
    """Plain-English clinical task -> (task, step, flags) or None. Deliberately conservative: each task needs its
    population AND its therapy/measure words; a question the recognizer is unsure about goes to the other tiers."""
    q = ' '.join(str(question or '').lower().split())
    has = lambda rx: re.search(rx, q) is not None
    if has(r'\bct-?(\d{1,2})\b'):
        n = int(re.search(r'\bct-?(\d{1,2})\b', q).group(1))
        if 1 <= n <= 10: return f'CT-{n:02d}', None
    if has(r'\b(?:intoleran\w*|allerg\w*|adverse reaction)') and has(r'\b(?:still|after|continu\w*|again|despite|yet)\b') \
            and has(r'\b(?:fill\w*|order\w*|prescri\w*|dispens\w*)'):
        return 'CT-10', 'a'
    if has(r'\b(?:mras?|spironolactone|eplerenone|mineralocorticoid)\b') and has(r'\b(?:potassium|hyperkal\w*|k\+?|k level)\b'):
        return 'CT-09', 'a'
    if has(r'\b(?:mi|myocardial infarction|heart attacks?|stemi|nstemi)\b') and has(r'\b(?:beta[- ]?blockers?|statins?|secondary prevention)\b'):
        return 'CT-08', 'b'
    if has(r'\b(?:afib|a-?fib|atrial fibrillation|af)\b') and has(r'\b(?:anticoag\w*|oacs?|doacs?|noacs?|warfarin|blood thinners?)\b'):
        return 'CT-05', 'b' if has(_NOT) else 'a'
    if has(r'\b(?:ckd|chronic kidney disease|kidney disease)\b') and has(r'\b(?:albuminuria|uacr|albumin)\b'):
        ace, sg = has(r'\b(?:ace|acei|arbs?|rasi?|raas|ace inhibitors?)\b'), has(r'\bsglt-?2')
        return 'CT-06', 'all' if ace and sg else 'c' if sg else 'b' if ace else 'a'
    if has(r'\bdiabet\w*|\bt2dm\b') and has(r'\b(?:ascvd|cardiovascular|ckd|kidney|cardio-?renal|heart disease)\b') \
            and has(r'\b(?:sglt-?2\w*|glp-?1\w*|gliflozin|cardio-?renal protect\w*)'):
        return 'CT-04', 'b' if has(_NOT) or has(r'\bneither\b') else 'a'
    if has(r'\bstatins?\b') and has(r'\b(?:indicat\w*|eligible|should be|benefit|ascvd|ldl)\b'):
        return 'CT-07', 'c' if has(r'\bintoleran\w*') else 'b' if has(_NOT) else 'a'
    if has(_HF) and has(r'\b(?:target dose|below (?:the )?target|under-?dos\w*|titrat\w*|sub-?target|dose)\b') \
            and has(r'\b(?:gdmt|guideline|target|pillar|beta[- ]?blocker|carvedilol|metoprolol|arni|sacubitril|ace|arb|mra|sglt-?2)'):
        return 'CT-03', 'a'
    if has(_HF) and has(_GDMT) and has(r'\b(?:reasons?|why|documented|notes?|explain\w*)\b'):
        return 'CT-02', 'a'
    if has(_HF) and has(_GDMT):
        return 'CT-01', 'b' if has(_NOT) or has(r'\bwhich of\b') else 'a'
    return None


# The question must state the task's own population and thresholds; anything else is a different question (other tiers).
ALLOWED_NUMBERS = {'CT-01': {'4', '40'}, 'CT-02': {'4'}, 'CT-03': set(), 'CT-04': {'2', '1'}, 'CT-05': {'2'}, 'CT-06': {'3', '30'},
                   'CT-07': {'190', '40', '75'}, 'CT-08': {'12', '1'}, 'CT-09': {'5.5'}, 'CT-10': set()}
REQUIRED = {'CT-04': [r'type 2|t2dm|\bt2d\b|type ii', r'ascvd|cardiovascular|heart disease|cardio-?renal', r'ckd|kidney|cardio-?renal'],
            'CT-05': [r'cha2?ds2|chads|stroke risk'], 'CT-06': [r'stage (?:3|iii)|\b3\+|stage 3 or (?:higher|above|worse)|moderate'],
            'CT-08': [r'12 months|(?:past|last|prior) year|365 days|twelve months|past 12|last 12'],
            'CT-09': [], 'CT-07': [], 'CT-01': [], 'CT-02': [], 'CT-03': [], 'CT-10': []}
STRICT_ABOVE = re.compile(r'\b(?:above|over|greater than|more than|higher than|exceed\w*|>)\s*\d', re.I)


def fits(task, question):
    """None when the question states exactly the task's population/thresholds, else the reason it does not."""
    q = question.lower()
    nums = set(re.findall(r'(?<![\w.])\d+(?:\.\d+)?', re.sub(r'cha2?ds2-?vasc|sglt-?2|glp-?1|n18\.\d|i50\.\d|ct-?\d+', ' ', q)))
    extra = nums - ALLOWED_NUMBERS[task]
    if extra: return f'numbers {sorted(extra)} differ from the {task} definition'
    if STRICT_ABOVE.search(q): return f'a strict threshold differs from the {task} definition (>=)'
    miss = [rx for rx in REQUIRED[task] if not re.search(rx, q)]
    if miss: return f'the question does not state the {task} population ({", ".join(miss)})'
    if task == 'CT-04' and re.search(r'type (?:1|i)\b', q): return 'type 1 diabetes is outside CT-04'
    if task == 'CT-09' and not re.search(r'\bmras?\b|mineralocorticoid|(?=.*spironolactone)(?=.*eplerenone)', q): return 'CT-09 covers every MRA, not one drug'
    return None


def local_question(question):
    """Graph for the exact-local tier: one 'clinical_task' node. None when no task is recognized or it does not fit."""
    found = recognize(question)
    if found and fits(found[0], question): found = None
    if not found: return None
    task, step = found
    step = step or FINAL[task]
    params = {'task': task, 'step': step, 'index_date': '', 'grace_days': GRACE_DAYS, 'output': 'rows'}
    words = DEFINITIONS[task]
    return {'interpretation': f'{task} {TASKS[task][0]} (step {step}) on the snapshot as-of date', 'coverage': 'supported_observation',
            'unmet_requirements': [],
            'nodes': [{'id': 'task', 'operation': 'clinical_task', 'inputs': [], 'parameters': {**params, 'definition': words},
                       'purpose': f'{task} {TASKS[task][0]}: governed definitions (medication_state, coded conditions, labs, barriers)'}]}
