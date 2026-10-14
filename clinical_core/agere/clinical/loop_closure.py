"""Closing the loop on results, orders, recommendations and referrals (diagnostic safety).

Answers "which abnormal results have no follow-up?", "referrals never completed", "imaging / report recommendations not acted
on", "critical labs not acknowledged", "positive FIT without a colonoscopy", "ordered labs never resulted" and "close rates by
practice". Every loop is an item with a start date, an expected follow-up action, a window and an owner:

  item type        start                               expected action (default window)
  critical_result  lab result past a critical limit    acknowledgement or action (7 days)
  abnormal_result  lab result past an action limit     acknowledgement or action (30 days)
  positive_screen  BI-RADS 0/4/5 mammogram, + FIT      diagnostic imaging (30 days) / colonoscopy (180 days)
  recommendation   report recommendation, or a note    the recommended action (stated interval + grace, else 90 days)
                   follow-up instruction (text)
  test_order       lab / imaging / diagnostic /        a result or performed study linked to the order (30-90 days)
                   screening / procedure order
  referral         referral requested                  completion: consult report, delivered service, or a visit with the
                                                       receiving provider or specialty (90 days)
  report_review    abnormal diagnostic / imaging       documented review or communication (30 days)
                   report received

Closure evidence, earliest first within the window, always recorded with its type: structured_review (a record that cites the
result, its order or its report: decisions, follow-up contacts, telephone messages, re-orders), expected_action (the study,
procedure, specialist visit or service named), repeat_test, medication_change (an order or intent change), visit,
telephone_contact, note_acknowledgement (note or telephone-message text naming the test, or a note-fact follow-up
instruction: text evidence, extractor-dependent). Results and reports close on any of these; positive screens and
recommendations close only on the expected action, orders on a linked result, referrals on completion evidence.
`evidence='specific'` ignores visits and telephone contacts that do not cite the item. Owner = ordering / requesting clinician, else the attributed PCP; practice = the attributed practice.
Open loops are listed with age and days overdue; close rates (closed on time / due) by practice, owner or item type with
Wilson intervals; time to closure by Kaplan-Meier (censored at the snapshot) via survival.kaplan_meier.
"""
from collections import Counter, defaultdict
from datetime import date
import json, math, re

from . import safety_data as sd

VERSION = 'loop-closure/1'
ITEM_TYPES = ('critical_result', 'abnormal_result', 'positive_screen', 'recommendation', 'test_order', 'referral', 'report_review')
FOCUS = ('all', 'results', 'critical', 'screening', 'recommendations', 'orders', 'referrals', 'reports')
FOCUS_TYPES = {'all': ITEM_TYPES, 'results': ('critical_result', 'abnormal_result'), 'critical': ('critical_result',),
               'screening': ('positive_screen',), 'recommendations': ('recommendation',), 'orders': ('test_order',),
               'referrals': ('referral',), 'reports': ('report_review',)}
TIERS = ('critical', 'abnormal', 'incidental', 'routine')
GROUP_BY = ('practice', 'owner', 'item_type', 'none')
EVIDENCE = ('any', 'specific')
BASIS = ('actionable', 'reference_range')
WINDOWS = {'critical_result': 7, 'abnormal_result': 30, 'positive_screen': 30, 'recommendation': 90, 'test_order': 30,
           'referral': 90, 'report_review': 30}
FIELDS = {'focus', 'item_types', 'period', 'window_days', 'windows', 'group_by', 'evidence', 'abnormal_basis', 'concept', 'top', 'owner', 'practice'}
EXPECTED = {'critical_result': 'acknowledgement or action: result review, repeat test, visit or call, medication change, or a note naming the test',
            'abnormal_result': 'acknowledgement or action: result review, repeat test, visit or call, medication change, or a note naming the test',
            'positive_screen': 'diagnostic follow-up: diagnostic mammography/ultrasound (BI-RADS 0/4/5) or colonoscopy (positive FIT)',
            'recommendation': 'the recommended action (imaging, colonoscopy, lab, specialist or visit) within the stated interval',
            'test_order': 'a result or performed study linked to the order',
            'referral': 'completion: consult/visit report received, service delivered, or a visit with the receiving provider or specialty',
            'report_review': 'documented review or communication of the report (or a visit or call)'}
TIER_RANK = {t: i for i, t in enumerate(TIERS)}

# Lab limits in canonical units: critical (illustrative common critical-value limits) and actionable (abnormal enough to need a
# documented response). A lab's own critical list should replace CRITICAL where it differs.
CRITICAL = {'potassium': (2.8, 6.2), 'sodium': (120, 160), 'glucose': (50, 450), 'hemoglobin': (7.0, None), 'platelets': (20, 1000),
            'wbc': (1.5, 30), 'troponin': (None, 52), 'egfr': (10, None)}
ACTIONABLE = {'potassium': (3.0, 5.5), 'sodium': (130, 150), 'glucose': (60, 250), 'hba1c': (None, 9.0), 'hemoglobin': (10.0, None),
              'platelets': (100, 600), 'wbc': (3.0, 15.0), 'egfr': (30, None), 'creatinine': (None, 2.0), 'uacr': (None, 300),
              'ferritin': (15, None), 'tsh': (0.1, 10.0), 'troponin': (None, 14), 'ldl': (None, 190), 'triglycerides': (None, 500),
              'nt_probnp': (None, 900), 'bun': (None, 60)}
VITALS = {'heart_rate', 'systolic_bp', 'diastolic_bp', 'weight', 'spo2', 'phq9'}
ANALYTE_WORDS = {'potassium': r'potassium|hyperkal|hypokal|\bk\+', 'sodium': r'sodium|hyponatr|hypernatr', 'glucose': r'glucose|sugar|hyperglyc|hypoglyc',
                 'hba1c': r'a1c|hemoglobin a1c|glycated', 'hemoglobin': r'hemoglobin|haemoglobin|\bhgb\b|\bhb\b|anemi|anaemi',
                 'platelets': r'platelet|thrombocyt', 'wbc': r'white (?:blood )?cell|\bwbc\b|leukocyt|neutropen', 'egfr': r'egfr|kidney function|renal function|\bgfr\b',
                 'creatinine': r'creatinine|kidney function|renal function', 'uacr': r'albumin|uacr|microalbumin|proteinuria',
                 'ferritin': r'ferritin|iron', 'tsh': r'\btsh\b|thyroid', 'troponin': r'troponin', 'ldl': r'\bldl\b|cholesterol|lipid',
                 'triglycerides': r'triglycerid|lipid', 'nt_probnp': r'bnp|natriuretic', 'bun': r'\bbun\b|urea'}

LAB_SIDE = {'labs', 'laboratory_specimens', 'procedures', 'laboratory_result_notifications', 'laboratory_status_responses',
            'laboratory_demographic_amendments', 'laboratory_report_transmissions', 'medical_claim_lines', 'medical_claims',
            'billing_documents', 'billing_service_dispositions', 'patient_billing_accounts', 'imported_documents', 'pharmacy_history',
            'laboratory_collection_attempts', 'received_laboratory_status'}
REVIEW_FIELD = re.compile(r'review|report|result|decision|notification')
NOTE_STREAMS = ('notes', 'telephone_messages')
# orders: stream -> (default window, completion streams)
ORDERS = {'laboratory_orders': (30, ('labs', 'laboratory_result_notifications')),
          'imaging_orders': (30, ('imaging_studies', 'imaging_reports')),
          'diagnostic_orders': (60, ('diagnostic_test_sessions', 'diagnostic_test_reports', 'received_diagnostic_reports')),
          'cancer_screening_orders': (90, ('cancer_screening_results', 'received_screening_reports', 'screening_specimens')),
          'procedure_orders': (90, ('endoscopy_reports', 'received_procedure_reports', 'procedure_recovery_notes')),
          'breast_diagnostic_orders': (30, ('breast_diagnostic_sessions', 'breast_diagnostic_reports', 'received_breast_diagnostic_reports'))}
REPORTS = ('imaging_reports', 'diagnostic_test_reports', 'received_diagnostic_reports', 'endoscopy_reports', 'pathology_reports',
           'breast_diagnostic_reports', 'received_breast_diagnostic_reports', 'received_procedure_reports')
SCREEN_RESULTS = ('cancer_screening_results', 'received_screening_reports')
NORMAL = re.compile(r'^\s*(?:no acute|normal|negative|benign|no suspicious|unremarkable|no evidence|within normal)|no_acute|^normal', re.I)
ABNORMAL_TEXT = re.compile(r'fracture|reduced|dilat|mass|edema|effusion|stenosis|nodule|lesion|abnormal|suspicious|incomplete|limited|'
                           r'ejection fraction (?:[0-3]\d|40)%|polyp|carcinoma|malignan|obstruct|consolidation|opacity', re.I)
NEGATED = re.compile(r'\b(?:no|not|without|negative for|free of)\b[^.;]*', re.I)
ROUTINE_REC = re.compile(r'return to (?:routine )?screening|no further|routine (?:screening|follow)|continue (?:current|usual)', re.I)
DECLINED = re.compile(r'declin|refus|patient_declined|opted out|does not want', re.I)
COMPLETED_FLAGS = ('service_performed_confirmed', 'performed_service_documented', 'service_delivered', 'diagnosis_established')
ACTIONS = [('colonoscopy', r'colonoscop'), ('imaging', r'mammogra|ultrasound|\bct\b|\bmri\b|imaging|radiograph|x-?ray|scan|echocardiog|views'),
           ('lab', r'\b(?:cbc|bmp|cmp|a1c|labs?|blood (?:count|test)|potassium|creatinine|ferritin|tsh)\b'),
           ('specialist', r'refer|specialist|cardiolog|nephrolog|gastro|\bgi\b|orthop|pulmonolog|endocrin|oncolog|neurolog|hematolog|urolog|dermatolog'),
           ('review', r'review|discuss|pathology'), ('visit', r'visit|follow[- ]?up|appointment|reassess|see (?:the )?(?:patient|clinician)')]
COLONOSCOPY_CPT = re.compile(r'^(?:4537[89]|453[89]\d|G0105|G0121)$')
REFERRAL_SPECIALTY = [(r'audiolog|hearing', r'audiolog'), (r'dental|oral', r'dentist'), (r'vision|eye|optom', r'optometr|ophthalm'),
                      (r'orthop|fracture', r'orthop'), (r'colorectal|colonoscopy|gastro|\bgi\b', r'gastro'), (r'cardio', r'cardiolog'),
                      (r'nephro|kidney', r'nephrolog'), (r'pulmon|lung', r'pulmonolog'), (r'endocrin|diabet', r'endocrin'),
                      (r'behavioral|depression|psych', r'psychiat|social work|psycholog'), (r'hematolog|anemia', r'hematolog|oncolog'),
                      (r'breast', r'radiolog'), (r'developmental', r'pediatric')]


# ---- parameters ------------------------------------------------------------------------------------------------------------
def validate(p):
    if not isinstance(p, dict) or set(p) - FIELDS: raise ValueError(f'loop_closure parameters: {sorted(FIELDS)}')
    focus = p.get('focus') or 'all'
    if focus not in FOCUS: raise ValueError(f'focus must be one of {FOCUS}')
    types = p.get('item_types') or list(FOCUS_TYPES[focus])
    if not isinstance(types, list) or not types or any(t not in ITEM_TYPES for t in types): raise ValueError(f'item_types from {ITEM_TYPES}')
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or any(v and sd.day(v) != v for v in period.values()):
        raise ValueError('period takes ISO start/end dates')
    if period.get('start') and period.get('end') and period['start'] > period['end']: raise ValueError('period start after end')
    wd = p.get('window_days', 365)
    if type(wd) is not int or not 30 <= wd <= 3650: raise ValueError('window_days 30..3650 (look-back for item start dates)')
    windows = p.get('windows') or {}
    if not isinstance(windows, dict) or any(k not in ITEM_TYPES or type(v) is not int or not 1 <= v <= 730 for k, v in windows.items()):
        raise ValueError('windows: {item_type: days 1..730}')
    group_by = p.get('group_by') or 'practice'
    if group_by not in GROUP_BY: raise ValueError(f'group_by must be one of {GROUP_BY}')
    evidence = p.get('evidence') or 'any'
    if evidence not in EVIDENCE: raise ValueError(f'evidence must be one of {EVIDENCE}')
    basis = p.get('abnormal_basis') or 'actionable'
    if basis not in BASIS: raise ValueError(f'abnormal_basis must be one of {BASIS}')
    concept = p.get('concept') or ''
    if concept and concept not in ACTIONABLE: raise ValueError(f'concept must be one of {sorted(ACTIONABLE)}')
    top = p.get('top', 50)
    if type(top) is not int or not 1 <= top <= 1000: raise ValueError('top 1..1000')
    for k in ('owner', 'practice'):
        if not isinstance(p.get(k) or '', str) or len(p.get(k) or '') > 80: raise ValueError(f'{k}: one id')
    return {'focus': focus, 'item_types': [t for t in ITEM_TYPES if t in types], 'period': {'start': period.get('start', ''), 'end': period.get('end', '')},
            'window_days': wd, 'windows': {t: windows.get(t, WINDOWS[t]) for t in ITEM_TYPES}, 'group_by': group_by, 'evidence': evidence,
            'abnormal_basis': basis, 'concept': concept, 'top': top, 'owner': p.get('owner') or '', 'practice': p.get('practice') or ''}


# ---- pure rules ------------------------------------------------------------------------------------------------------------
def lab_tier(concept, value, flag=None, basis='actionable'):
    """'critical' | 'abnormal' | None for one result (canonical units)."""
    if value is None: return None
    lo, hi = CRITICAL.get(concept, (None, None))
    if (lo is not None and value <= lo) or (hi is not None and value >= hi): return 'critical'
    if (flag or '').upper() in ('HH', 'LL', 'AA', 'C', 'CRITICAL', 'PANIC'): return 'critical'
    if basis == 'reference_range':
        return 'abnormal' if (flag or '').upper() in ('H', 'L', 'A', 'ABNORMAL') else None
    lo, hi = ACTIONABLE.get(concept, (None, None))
    if (lo is not None and value < lo) or (hi is not None and value > hi): return 'abnormal'
    return None


def action_of(text):
    low = (text or '').lower()
    return next((a for a, rx in ACTIONS if re.search(rx, low)), 'visit')


def interval_days(text, default=90):
    m = re.search(r'\b(?:in|within|after)\s+(?:about\s+)?(\d{1,3})\s*(day|week|month|year)s?\b', (text or '').lower())
    if not m: return default
    n, unit = int(m.group(1)), m.group(2)
    return n * {'day': 1, 'week': 7, 'month': 30, 'year': 365}[unit]


def grace(days):
    return days + max(7, round(days * 0.25))


def classify(item, as_of):
    """Status of one item from its earliest evidence: closed_on_time | closed_late | declined | open_overdue | pending."""
    if item.get('declined'): return 'declined'
    c = item.get('closed_date')
    if c: return 'closed_on_time' if c <= item['due_date'] else 'closed_late'
    return 'open_overdue' if as_of > item['due_date'] else 'pending'


# ---- data ------------------------------------------------------------------------------------------------------------------
class Data:
    """Per-patient sorted activity used to look for closure evidence."""
    def __init__(self, db, pids, cap):
        self.db, self.cap = db, cap
        self.visits = sd.visits(db, pids)
        self.visit_dates = {p: [v[0] for v in vs if v[1] == 'visit'] for p, vs in self.visits.items()}
        self.call_dates = {p: [v[0] for v in vs if v[1] == 'telephone_contact'] for p, vs in self.visits.items()}
        self.clin = sd.clinicians(db)
        self.labs = defaultdict(list)  # (patient, concept) -> sorted dates
        cols = sd.columns(db, 'measurement')
        cur = ' AND result_current = 1' if 'result_current' in cols else ''
        exc = ' AND excluded_reason IS NULL' if 'excluded_reason' in cols else ''
        for pid, concept, d in db.execute(f'SELECT patient_id, concept, coalesce(event_date, recorded_date, available_date) FROM measurement '
                                          f'WHERE concept IS NOT NULL AND value_canonical IS NOT NULL{cur}{exc}'):
            if pids is None or pid in pids:
                d = sd.day(d)
                if d: self.labs[(pid, concept)].append(d)
        for v in self.labs.values(): v.sort()
        self.meds = defaultdict(list)
        if 'medication' in cap['tables']:
            for pid, d in db.execute("SELECT patient_id, event_date FROM medication WHERE evidence = 'ordered'"):
                d = sd.day(d)
                if d and (pids is None or pid in pids): self.meds[pid].append(d)
        if 'medication_intent_changes' in cap['streams']:
            for pid, d in db.execute("SELECT patient_id, event_date FROM record WHERE stream = 'medication_intent_changes'"):
                d = sd.day(d)
                if d and (pids is None or pid in pids): self.meds[pid].append(d)
        if 'note_therapy_change' in cap['tables']:
            for pid, d in db.execute('SELECT patient_id, event_date FROM note_therapy_change'):
                d = sd.day(d)
                if d and (pids is None or pid in pids): self.meds[pid].append(d)
        for v in self.meds.values(): v.sort()
        self.followup_facts = defaultdict(list)
        if 'note_fact' in cap['tables']:
            for pid, d in db.execute("SELECT patient_id, event_date FROM note_fact WHERE fact_type = 'follow_up' AND coalesce(negated, 0) = 0"):
                d = sd.day(d)
                if d and (pids is None or pid in pids): self.followup_facts[pid].append(d)
        for v in self.followup_facts.values(): v.sort()
        self._notes = {}

    def notes(self, pid):
        """[(date, lowercase text)] of the patient's notes and telephone messages (loaded on demand)."""
        if pid not in self._notes:
            rows = []
            for stream in NOTE_STREAMS:
                if stream not in self.cap['streams']: continue
                for d, av, payload in self.db.execute('SELECT event_date, available_date, payload FROM record WHERE stream = ? AND patient_id = ?', (stream, pid)):
                    d = sd.day(d) or sd.day(av)
                    if not d: continue
                    p = json.loads(payload)
                    text = ' '.join(str(p.get(k) or '') for k in ('text', 'note_text', 'message', 'body', 'summary', 'reason', 'subject'))
                    if text.strip(): rows.append((d, text.lower()))
            self._notes[pid] = sorted(rows)
        return self._notes[pid]

    def specialist_visit(self, pid, lo, hi, specialty_rx=None, orgs=(), clinicians=()):
        for d, kind, org, clin, tc, _ in self.visits.get(pid, ()):
            if d < lo or d > hi or kind != 'visit': continue
            if org and org in orgs: return d, f'visit at {org}'
            if clin and clin in clinicians: return d, f'visit with {clin}'
            spec = ((self.clin.get(clin) or {}).get('specialty') or '').lower()
            if specialty_rx and spec and re.search(specialty_rx, spec): return d, f'{spec} visit'
            if specialty_rx is None and tc == 'specialist_visit': return d, 'specialist visit'
        return None


def capability(db):
    t = sd.tables(db)
    st = sd.streams(db)
    cap = {'version': sd.meta(db).get('version'), 'tables': t, 'streams': st, 'link': 'link' in t, 'measurement': 'measurement' in t,
           'note_fact': 'note_fact' in t, 'notes': [s for s in NOTE_STREAMS if s in st]}
    gaps = []
    if not cap['link']: gaps.append('No record-link table: structured reviews, order results and referral updates cannot be tied to their items.')
    if 'critical_result_calls' not in st:
        gaps.append('No critical-result call log (time called, recipient, read-back): critical-result acknowledgement uses indirect evidence only.')
    if not ({'result_reviews', 'inbox_acknowledgements', 'result_acknowledgements'} & st):
        gaps.append('No result-acknowledgement (inbox sign-off) log: acknowledgement comes from decisions, contacts, re-orders and note text.')
    if not cap['notes']: gaps.append('No note or telephone-message text: note acknowledgement is not checked.')
    cap['data_gaps'] = gaps
    return cap


def _period(p, as_of):
    end = p['period']['end'] or as_of
    start = p['period']['start'] or sd.shift(end, -p['window_days'] + 1)
    return start, end


LINK_FIELDS = ('status', 'disposition', 'received_report_record_id') + COMPLETED_FLAGS


REVIEW_STREAM = re.compile(r'review|decision|contact|communication|message|follow_?up|order|plan|response|acknowledg|interview|assessment|referral')
REFERRAL_STREAM = re.compile(r'referral|status|outcome|report|record|document|contact|review|confirmation|session|order')


def citing_streams(streams, kind):
    """Streams whose records may close a loop of this kind (by name; lab-side plumbing and billing never count)."""
    if kind == 'test_order':
        done = {s for _, d in ORDERS.values() for s in d}
        return sorted((done & set(streams)) | {s for s in streams if re.search(r'follow_?up_contacts|status_responses', s)})
    rx = REFERRAL_STREAM if kind == 'referral' else REVIEW_STREAM
    return sorted(s for s in streams if rx.search(s) and s not in LAB_SIDE and not re.search(r'billing|claim|payment|cash|remittance', s))


def _links_dated(db, targets, streams):
    """{target: [(source stream, field, source id, date, {status fields})]}: records of the given streams citing a target (link
    table, probed on its (target, stream) index), with the citing record's date and the few payload fields closure reads."""
    out, ids, rows = defaultdict(list), list(targets), []
    if not ids or not streams: return out
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows += db.execute(f'SELECT target_record_id, stream, field, record_id FROM link WHERE target_record_id IN ({",".join("?" * len(chunk))}) '
                           f'AND stream IN ({",".join("?" * len(streams))})', chunk + list(streams)).fetchall()
    extract = ', '.join(f"json_extract(payload, '$.{f}')" for f in LINK_FIELDS)
    results = {s for _, d in ORDERS.values() for s in d}  # results only need their date: the payload (last column, overflow pages) is never read
    src = {}
    for sids, cols in ((sorted({r[3] for r in rows if r[1] in results}), 'NULL'), (sorted({r[3] for r in rows if r[1] not in results}), extract)):
        for i in range(0, len(sids), 500):
            chunk = sids[i:i + 500]
            for row in db.execute(f'SELECT record_id, coalesce(event_date, available_date), {cols} FROM record WHERE record_id IN ({",".join("?" * len(chunk))})', chunk):
                src[row[0]] = (sd.day(row[1]), {k: (bool(v) if k in COMPLETED_FLAGS else v) for k, v in zip(LINK_FIELDS, row[2:]) if v is not None})
    for tgt, stream, field, rid in rows:
        if rid in src: out[tgt].append((stream, field, rid, *src[rid]))
    return out


def _owner(p, pat):
    return p or (pat or {}).get('pcp') or 'unassigned'


# ---- item builders -----------------------------------------------------------------------------------------------------------
def lab_items(db, pids, people, p, start, end):
    cols = sd.columns(db, 'measurement')
    cur = ' AND result_current = 1' if 'result_current' in cols else ''
    exc = ' AND excluded_reason IS NULL' if 'excluded_reason' in cols else ''
    flag = 'abnormal_flag' if 'abnormal_flag' in cols else 'NULL'
    out = []
    for rid, pid, concept, v, unit, f, d, av in db.execute(
            f'SELECT record_id, patient_id, concept, value_canonical, unit_canonical, {flag}, coalesce(event_date, recorded_date), available_date '
            f'FROM measurement WHERE concept IS NOT NULL AND value_canonical IS NOT NULL{cur}{exc}'):
        if concept in VITALS or (pids is not None and pid not in pids) or pid not in people: continue
        if p['concept'] and concept != p['concept']: continue
        tier = lab_tier(concept, v, f, p['abnormal_basis'])
        if tier is None: continue
        when = sd.day(av) or sd.day(d)  # the clock starts when the result was available to clinicians
        if not when or not start <= when <= end: continue
        kind = 'critical_result' if tier == 'critical' else 'abnormal_result'
        if kind not in p['item_types']: continue
        out.append({'item_id': rid, 'patient_id': pid, 'item_type': kind, 'tier': tier, 'concept': concept,
                    'item': f"{concept.replace('_', ' ')} {v:g} {unit or ''}".strip(), 'item_date': when, 'result_date': sd.day(d) or when})
    if out and 'link' in sd.tables(db):  # owner = ordering clinician; review targets = the result, its order and the order's notifications
        pay = sd.payloads(db, {i['item_id'] for i in out}, ('laboratory_order_record_id', 'ordering_clinician_id'))
        orders = {i['item_id']: (pay.get(i['item_id'], (None, None, {}))[2] or {}).get('laboratory_order_record_id') for i in out}
        opay = sd.payloads(db, {o for o in orders.values() if o}, ('ordering_clinician_id',))
        notes = defaultdict(set)
        for tgt, stream, field, rid in sd.links_to(db, {o for o in orders.values() if o}, ('laboratory_result_notifications',)):
            notes[tgt].add(rid)
        for i in out:
            o = orders[i['item_id']]
            i['owner_id'] = ((opay.get(o) or (None, None, {}))[2] or {}).get('ordering_clinician_id') if o else None
            i['targets'] = {i['item_id'], *(notes.get(o, ()) if o else ())}
    return out


def screen_items(db, pids, people, p, start, end):
    out = []
    for stream in SCREEN_RESULTS:
        for rid, pid, d, payload in db.execute('SELECT record_id, patient_id, coalesce(event_date, available_date), payload FROM record WHERE stream = ?', (stream,)):
            if (pids is not None and pid not in pids) or pid not in people: continue
            r = json.loads(payload)
            when = sd.day(r.get('performed_date')) or sd.day(d)
            if not when or not start <= when <= end: continue
            birads = r.get('birads_category')
            test = ' '.join(str(r.get(k) or '') for k in ('test', 'method', 'screening_kind')).lower()
            if birads is not None and str(birads).strip() in ('0', '4', '5'):
                kind, label, window = 'breast', f'mammogram BI-RADS {birads}', 30
            elif re.search(r'fit|fecal|occult|stool', test) and str(r.get('result') or '').lower() in ('positive', 'abnormal', 'detected'):
                kind, label, window = 'colorectal', 'positive FIT', 180
            else:
                continue
            out.append({'item_id': rid, 'patient_id': pid, 'item_type': 'positive_screen', 'tier': 'abnormal', 'screen': kind, 'item': label,
                        'item_date': when, 'window': window, 'owner_id': None, 'order_id': r.get('order_record_id'), 'targets': {rid},
                        'copy_key': (pid, 'screen', kind), 'received_copy': stream.startswith('received_')})
    return out


def recommendation_items(db, pids, people, p, start, end, cap):
    out = []
    for stream in REPORTS:
        if stream not in cap['streams']: continue
        for rid, pid, d, payload in db.execute('SELECT record_id, patient_id, coalesce(event_date, available_date), payload FROM record WHERE stream = ?', (stream,)):
            if (pids is not None and pid not in pids) or pid not in people: continue
            r = json.loads(payload)
            rec = r.get('recommendation') or ''
            text = str(r.get('text') or '')
            if not rec:
                m = re.search(r'(recommend\w*[^.]*\.|follow-up (?:imaging|ct|mri|ultrasound)[^.]*\.)', text, re.I)
                rec = m.group(1) if m else ''
            when = sd.day(d)
            if not rec or ROUTINE_REC.search(rec) or not when or not start <= when <= end: continue
            act = action_of(rec)
            days = interval_days(rec)
            out.append({'item_id': rid, 'patient_id': pid, 'item_type': 'recommendation', 'tier': 'incidental', 'action': act,
                        'item': f'{stream.replace("_", " ")}: {rec[:120]}', 'item_date': when, 'window': grace(days), 'source': 'report field',
                        'owner_id': None, 'order_id': r.get('order_record_id'), 'targets': {rid}})
    if cap['note_fact']:
        cols = sd.columns(db, 'note_fact')
        q = 'qualifier' if 'qualifier' in cols else 'NULL'
        for rid, pid, v, qual, d, ev in db.execute(f"SELECT record_id, patient_id, value, {q}, event_date, evidence_text FROM note_fact "
                                                   f"WHERE fact_type = 'follow_up' AND coalesce(negated, 0) = 0"):
            if (pids is not None and pid not in pids) or pid not in people: continue
            when = sd.day(d)
            if not when or not start <= when <= end: continue
            try:
                days = int(float(v))
            except (TypeError, ValueError):
                days = interval_days(ev)
            qual = (qual or '').lower()
            act = 'lab' if qual in ACTIONABLE or re.search(r'cbc|bmp|lab', qual) else 'specialist' if qual and qual not in ('visit', 'trial hold') else \
                'review' if qual == 'trial hold' else 'visit'
            out.append({'item_id': f'{rid}#{qual or "follow_up"}', 'patient_id': pid, 'item_type': 'recommendation', 'tier': 'incidental', 'action': act,
                        'qualifier': qual, 'item': f'note follow-up instruction: {(ev or qual)[:100]}', 'item_date': when, 'window': grace(days),
                        'source': 'note text (note_fact follow_up)', 'owner_id': None, 'targets': {rid}})
    return out


def order_items(db, pids, people, p, start, end, cap):
    out = []
    for stream, (window, _) in ORDERS.items():
        if stream not in cap['streams']: continue
        for rid, pid, d, payload in db.execute('SELECT record_id, patient_id, event_date, payload FROM record WHERE stream = ?', (stream,)):
            if (pids is not None and pid not in pids) or pid not in people: continue
            when = sd.day(d)
            if not when or not start <= when <= end: continue
            r = json.loads(payload)
            what = r.get('requested_tests_local') or r.get('test_requested') or r.get('test') or r.get('anatomic_site') or r.get('procedure') or ''
            what = ', '.join(what) if isinstance(what, list) else str(what)
            out.append({'item_id': rid, 'patient_id': pid, 'item_type': 'test_order', 'tier': 'routine', 'order_stream': stream,
                        'item': f"{stream.replace('_', ' ').replace('orders', 'order')}: {what}"[:140], 'item_date': when, 'window': window,
                        'owner_id': r.get('ordering_clinician_id'), 'targets': {rid}})
    return out


def referral_items(db, pids, people, p, start, end, cap):
    out = []
    if 'referrals' not in cap['streams']: return out
    for rid, pid, d, payload in db.execute("SELECT record_id, patient_id, event_date, payload FROM record WHERE stream = 'referrals'"):
        if (pids is not None and pid not in pids) or pid not in people: continue
        when = sd.day(d)
        if not when or not start <= when <= end: continue
        r = json.loads(payload)
        if str(r.get('status') or 'requested').lower() in ('cancelled', 'canceled', 'completed', 'entered_in_error'): continue
        what = r.get('referral_type') or r.get('specialty') or r.get('requested_specialty') or r.get('requested_service') or 'referral'
        words = ' '.join(str(r.get(k) or '') for k in ('referral_type', 'specialty', 'requested_specialty', 'reason')).lower()
        spec = next((target for rx, target in REFERRAL_SPECIALTY if re.search(rx, words)), None)
        out.append({'item_id': rid, 'patient_id': pid, 'item_type': 'referral', 'tier': 'routine', 'item': f'referral: {str(what).replace("_", " ")}'[:140],
                    'item_date': when, 'owner_id': r.get('requesting_clinician_id') or r.get('ordering_clinician_id'), 'specialty_rx': spec,
                    'orgs': {x for x in (r.get('receiving_organization_id'), r.get('destination_organization_id')) if x},
                    'clinicians': {x for x in (r.get('receiving_clinician_id'),) if x}, 'targets': {rid}})
    return out


def report_items(db, pids, people, p, start, end, cap):
    out = []
    for stream in REPORTS:
        if stream not in cap['streams']: continue
        for rid, pid, d, av, payload in db.execute('SELECT record_id, patient_id, event_date, available_date, payload FROM record WHERE stream = ?', (stream,)):
            if (pids is not None and pid not in pids) or pid not in people: continue
            when = sd.day(av) or sd.day(d)
            if not when or not start <= when <= end: continue
            r = json.loads(payload)
            imp = str(r.get('impression') or '')
            text = ' '.join(str(r.get(k) or '') for k in ('impression', 'findings', 'text'))
            m = r.get('measurements') if isinstance(r.get('measurements'), dict) else {}
            ef = m.get('LVEF_percent') if isinstance(m.get('LVEF_percent'), (int, float)) else None
            affirmed = NEGATED.sub(' ', text.replace('_', ' '))  # "no suspicious mass" is not a finding
            birads = str(r.get('birads_category') if r.get('birads_category') is not None else '').strip()
            abnormal = (ef is not None and ef <= 40) or birads in ('0', '3', '4', '5') or str(r.get('test_quality') or '') == 'limited' or \
                (not NORMAL.search(imp.replace('_', ' ')) and bool(ABNORMAL_TEXT.search(affirmed)))
            if not abnormal: continue
            label = (imp.replace('_', ' ') or (f'LVEF {ef}%' if ef is not None else '') or text[:80]).strip()
            out.append({'item_id': rid, 'patient_id': pid, 'item_type': 'report_review', 'tier': 'abnormal',
                        'item': f"{stream.replace('_', ' ')}: {label}"[:140], 'item_date': when, 'owner_id': None,
                        'copy_key': (pid, 'report', label[:60]), 'received_copy': stream.startswith('received_'),
                        'order_id': r.get('order_record_id'), 'targets': {rid, *([r['order_record_id']] if r.get('order_record_id') else [])}})
    return out


def dedupe(items, days=14):
    """One loop per result: a received copy of a report or screen result (received_* streams, same patient and finding, dated
    within two weeks) merges into the original; the copy's record still counts as a target, so evidence citing either closes it."""
    keep, by_key = [], defaultdict(list)
    for it in sorted(items, key=lambda i: (bool(i.get('received_copy')), i['item_date'])):
        k = it.get('copy_key')
        twin = next((o for o in by_key.get(k, ()) if abs(sd.days_between(o['item_date'], it['item_date'])) <= days), None) \
            if k is not None and it.get('received_copy') else None  # only copies merge: two own studies of one finding are two loops
        if twin is not None:
            twin['targets'] |= it['targets']; continue
        if k is not None: by_key[k].append(it)
        keep.append(it)
    return keep


# ---- closure ---------------------------------------------------------------------------------------------------------------
def _first(cands):
    cands = [c for c in cands if c]
    return min(cands) if cands else None


def close(items, data, links, p, as_of):
    """Attach closure evidence (earliest within the item's window; later evidence makes it closed_late) to every item."""
    specific_only = p['evidence'] == 'specific'
    for it in items:
        pid, lo = it['patient_id'], it['item_date']
        t = it['item_type']
        explicit = p['windows'][t] != WINDOWS[t]  # a window the user set overrides the per-item default (stated interval, order type)
        win = p['windows'][t] if explicit or not it.get('window') else it['window']
        it['window_days'] = win
        it['due_date'] = sd.shift(lo, win)
        hi = as_of  # look for evidence up to the snapshot; on-time vs late is decided against the due date
        ev = []  # (date, type, detail)
        linked = [x for tg in it.get('targets', ()) for x in links.get(tg, ())]
        acknowledged = t in ('critical_result', 'abnormal_result', 'report_review')  # these close on acknowledgement; screens and
        if t in ('critical_result', 'abnormal_result', 'report_review', 'positive_screen', 'recommendation'):  # recommendations need the action itself
            for stream, field, rid, d, pay in linked:
                if stream in LAB_SIDE or not d or d < lo or d > hi: continue
                if REVIEW_FIELD.search(field) or stream in ('telephone_messages', 'diagnostic_result_reviews', 'outside_followup_contacts',
                                                           'procedure_result_communications', 'laboratory_followup_contacts'):
                    if acknowledged: ev.append((d, 'structured_review', f'{stream}.{field}'))
                    else: it.setdefault('activity', f"{stream}: {pay.get('status') or pay.get('disposition') or 'reviewed'}")
                    if DECLINED.search(' '.join(str(pay.get(k) or '') for k in ('status', 'disposition'))): it['declined'] = True
        if t in ('critical_result', 'abnormal_result'):
            nxt = sd.first_in(data.labs.get((pid, it['concept']), []), sd.shift(lo, 1), hi)
            if nxt: ev.append((nxt, 'repeat_test', f"repeat {it['concept']}"))
            m = sd.first_in(data.meds.get(pid, []), lo, hi)
            if m: ev.append((m, 'medication_change', 'medication order or intent change'))
        if t == 'positive_screen':
            got = _diagnostic_followup(data, pid, it['screen'], lo, hi)
            if got: ev.append((got[0], 'expected_action', got[1]))
        if t == 'recommendation':
            got = _action_done(data, pid, it, lo, hi)
            if got: ev.append((got[0], 'expected_action', got[1]))
        if t == 'test_order':
            done_streams = ORDERS[it['order_stream']][1]
            for stream, field, rid, d, pay in linked:
                if not d: continue
                if stream in done_streams: ev.append((max(d, lo), 'expected_action', f'{stream} linked to the order'))
                elif DECLINED.search(' '.join(str(pay.get(k) or '') for k in ('status', 'disposition'))) and d >= lo: it['declined'] = True
                elif d >= lo: it.setdefault('activity', f"{stream}: {pay.get('status') or pay.get('disposition') or 'contact'}")
        if t == 'referral':
            for stream, field, rid, d, pay in sorted(linked, key=lambda x: x[3] or ''):
                if not d or d < lo: continue
                status = ' '.join(str(pay.get(k) or '') for k in ('status', 'disposition'))
                if any(pay.get(k) is True for k in COMPLETED_FLAGS) or (stream == 'referral_status_updates' and pay.get('received_report_record_id')):
                    ev.append((d, 'expected_action', f'{stream}: {status or "completed"}'.strip()))
                elif DECLINED.search(status):
                    it['declined'] = True; it['activity'] = f'{stream}: {status}'
                elif status:
                    it['activity'] = f'{stream}: {status}'
            got = data.specialist_visit(pid, lo, hi, it.get('specialty_rx'), it.get('orgs', ()), it.get('clinicians', ()))
            if got and (it.get('specialty_rx') or it.get('orgs') or it.get('clinicians')): ev.append((got[0], 'expected_action', got[1]))
        if acknowledged and not specific_only:
            v = sd.first_in(data.visit_dates.get(pid, []), lo, hi)
            if v: ev.append((v, 'visit', 'visit'))
            c = sd.first_in(data.call_dates.get(pid, []), lo, hi)
            if c: ev.append((c, 'telephone_contact', 'telephone contact'))
        if acknowledged:
            words = ANALYTE_WORDS.get(it.get('concept'))
            if t == 'report_review':  # the test or its finding by name, never the generic word "report"
                found = ABNORMAL_TEXT.findall(it['item'].lower())
                words = r'echocardiog|ejection fraction|ultrasound|x-?ray|radiograph|spirometr|colonoscop|endoscop|patholog|mammogra|\bct\b|\bmri\b' + \
                        ''.join('|' + re.escape(f) for f in set(found) if len(f) > 4)
            if words and data.cap['notes']:
                n = next((d for d, txt in data.notes(pid) if lo <= d <= hi and re.search(words, txt)), None)
                if n: ev.append((n, 'note_acknowledgement', 'note/telephone text names the test'))
            f = sd.first_in(data.followup_facts.get(pid, []), lo, hi)
            if f and not specific_only: ev.append((f, 'note_acknowledgement', 'note follow-up instruction (note_fact)'))
        ev.sort()
        if ev:
            it['closed_date'], it['closed_by'], it['closure_detail'] = ev[0]
            it['days_to_close'] = sd.days_between(lo, ev[0][0])
        it['status'] = classify(it, as_of)
        it['age_days'] = sd.days_between(lo, as_of)
        it['days_overdue'] = max(0, sd.days_between(it['due_date'], as_of)) if it['status'] == 'open_overdue' else 0
    return items


def _diagnostic_followup(data, pid, kind, lo, hi):
    db = data.db
    if kind == 'breast':
        streams = [s for s in ('breast_diagnostic_sessions', 'breast_diagnostic_reports', 'received_breast_diagnostic_reports', 'imaging_studies') if s in data.cap['streams']]
        for s in streams:
            for d, payload in db.execute('SELECT event_date, payload FROM record WHERE stream = ? AND patient_id = ? ORDER BY event_date', (s, pid)):
                d = sd.day(d)
                if d and lo <= d <= hi and (s != 'imaging_studies' or 'breast' in payload.lower()): return d, s.replace('_', ' ')
        return None
    found = []
    for s in ('endoscopy_reports', 'received_procedure_reports', 'procedures'):
        if s not in data.cap['streams']: continue
        for d, payload in db.execute('SELECT event_date, payload FROM record WHERE stream = ? AND patient_id = ?', (s, pid)):
            d = sd.day(d)
            if d and lo <= d <= hi and 'colonoscop' in payload.lower(): found.append((d, f'colonoscopy ({s.replace("_", " ")})'))
    if 'claim_line' in data.cap['tables'] and 'cpt' in sd.columns(db, 'claim_line'):
        for d, code in db.execute('SELECT service_date, coalesce(cpt, hcpcs) FROM claim_line WHERE patient_id = ?', (pid,)):
            d = sd.day(d)
            if d and lo <= d <= hi and code and COLONOSCOPY_CPT.match(code): found.append((d, f'colonoscopy claim {code}'))
    return min(found) if found else None


def _action_done(data, pid, it, lo, hi):
    act = it['action']
    if act == 'colonoscopy': return _diagnostic_followup(data, pid, 'colorectal', lo, hi)
    if act == 'imaging':
        for s in ('imaging_studies', 'imaging_reports', 'diagnostic_test_sessions', 'diagnostic_test_reports', 'breast_diagnostic_sessions', 'imaging_orders'):
            if s not in data.cap['streams']: continue
            for (d,) in data.db.execute('SELECT event_date FROM record WHERE stream = ? AND patient_id = ?', (s, pid)):
                d = sd.day(d)
                if d and lo < d <= hi: return d, s.replace('_', ' ')
        return None
    if act == 'lab':
        concept = it.get('qualifier') if it.get('qualifier') in ACTIONABLE else None
        keys = [(pid, concept)] if concept else [k for k in data.labs if k[0] == pid]
        if it.get('qualifier') == 'cbc': keys = [(pid, c) for c in ('hemoglobin', 'wbc', 'platelets')]
        got = _first(sd.first_in(data.labs.get(k, []), sd.shift(lo, 1), hi) for k in keys)
        return (got, 'lab result') if got else None
    if act == 'specialist':
        rx = next((target for r, target in REFERRAL_SPECIALTY if re.search(r, (it.get('qualifier') or '') + ' ' + it['item'].lower())), None)
        return data.specialist_visit(pid, sd.shift(lo, 1), hi, rx)
    if act == 'review':
        got = _first([sd.first_in(data.visit_dates.get(pid, []), lo, hi), sd.first_in(data.call_dates.get(pid, []), lo, hi)])
        return (got, 'visit or call') if got else None
    got = sd.first_in(data.visit_dates.get(pid, []), sd.shift(lo, 1), hi)
    return (got, 'visit') if got else None


# ---- summaries -------------------------------------------------------------------------------------------------------------
def _group_key(it, p):
    return {'practice': it['practice'], 'owner': it['owner'], 'item_type': it['item_type'], 'none': 'all'}[p['group_by']]


def rates(items, key):
    groups = defaultdict(list)
    for it in items: groups[key(it)].append(it)
    out = []
    for g, rows in groups.items():
        c = Counter(r['status'] for r in rows)
        due = len(rows) - c['pending']
        closed = [r['days_to_close'] for r in rows if r.get('days_to_close') is not None]
        lo, hi = sd.wilson(c['closed_on_time'], due)
        out.append({'group': g, 'items': len(rows), 'due': due, 'closed_on_time': c['closed_on_time'], 'closed_late': c['closed_late'],
                    'declined': c['declined'], 'open_overdue': c['open_overdue'], 'pending': c['pending'],
                    'close_rate': round(c['closed_on_time'] / due, 4) if due else None, 'close_rate_ci95': [lo, hi] if due else None,
                    'critical_open': sum(r['status'] == 'open_overdue' and r['tier'] == 'critical' for r in rows),
                    'median_days_to_close': sd.median(closed)})
    out.sort(key=lambda r: (-(r['open_overdue']), r['close_rate'] if r['close_rate'] is not None else 2, str(r['group'])))
    return out


def km_by_type(items, as_of):
    from .survival import kaplan_meier, median_time
    out = {}
    for t in ITEM_TYPES:
        rows = [i for i in items if i['item_type'] == t and not i.get('declined')]
        if len(rows) < 2: continue
        times = [i['days_to_close'] if i.get('days_to_close') is not None else i['age_days'] for i in rows]
        events = [1 if i.get('days_to_close') is not None else 0 for i in rows]
        curve = kaplan_meier([max(0, x) for x in times], events)
        horizon = max(60, 3 * max(i['window_days'] for i in rows))
        pts = [[0, 0.0]] + [[r['time'], round(1 - r['survival'], 4)] for r in curve if r['events'] and r['time'] <= horizon]
        out[t] = {'n': len(rows), 'median_days': median_time(curve), 'window_days': sd.median([i['window_days'] for i in rows]),
                  'closed_by_window': round(sum(e and x <= i['window_days'] for e, x, i in zip(events, times, rows)) / len(rows), 4), 'points': pts[:200]}
    return out


def analyse(items, p, as_of, people, cap):
    for it in items:
        pat = people.get(it['patient_id']) or {}
        it['owner'] = _owner(it.get('owner_id'), pat)
        it['practice'] = pat.get('practice') or 'unattributed'
    if p['owner']: items = [i for i in items if str(i['owner']).lower() == p['owner'].lower()]
    if p['practice']: items = [i for i in items if str(i['practice']).lower() == p['practice'].lower()]
    status = Counter(i['status'] for i in items)
    open_rows = sorted((i for i in items if i['status'] == 'open_overdue'), key=lambda i: (TIER_RANK[i['tier']], -i['days_overdue'], i['patient_id']))
    keep = ('patient_id', 'item_type', 'tier', 'item', 'item_date', 'due_date', 'age_days', 'days_overdue', 'owner', 'practice', 'expected_action',
            'activity', 'item_id')
    table = [{k: ({**i, 'expected_action': EXPECTED[i['item_type']]}).get(k) for k in keep} for i in open_rows]
    by_type = []
    for t in p['item_types']:
        rows = [i for i in items if i['item_type'] == t]
        if not rows: continue
        r = rates(rows, lambda i: t)[0]
        by_type.append({**r, 'group': t, 'default_window_days': p['windows'][t], 'expected_action': EXPECTED[t]})
    evidence = Counter(i['closed_by'] for i in items if i.get('closed_by'))
    return {'status': 'answered' if items else 'empty', 'items_total': len(items), 'status_counts': dict(status),
            'by_type': by_type, 'by_tier': {t: dict(Counter(i['status'] for i in items if i['tier'] == t)) for t in TIERS if any(i['tier'] == t for i in items)},
            'by_group': rates(items, lambda i: _group_key(i, p)), 'group_by': p['group_by'],
            'closure_evidence': [{'evidence': k, 'items': v, 'kind': 'text (note-derived)' if k == 'note_acknowledgement' else
                                  'contact (not specific to the item)' if k in ('visit', 'telephone_contact') else 'structured'}
                                 for k, v in evidence.most_common()],
            'open_loops': table[:p['top']], 'open_loops_total': len(table), 'time_to_closure': km_by_type(items, as_of)}


# ---- entry points ----------------------------------------------------------------------------------------------------------
def run(db, patient_ids, params, as_of=None):
    """The whole engine on an open read-only connection (tests and the real-stack smoke call this directly)."""
    p = validate(params)
    as_of = sd.as_of_of(db, as_of)
    cap = capability(db)
    start, end = _period(p, as_of)
    pids = set(patient_ids) if patient_ids is not None else None
    people = sd.patients(db, pids)
    items = []
    if {'critical_result', 'abnormal_result'} & set(p['item_types']) and cap['measurement']:
        items += lab_items(db, pids, people, p, start, end)
    if 'positive_screen' in p['item_types']: items += screen_items(db, pids, people, p, start, end)
    if 'recommendation' in p['item_types']: items += recommendation_items(db, pids, people, p, start, end, cap)
    if 'test_order' in p['item_types']: items += order_items(db, pids, people, p, start, end, cap)
    if 'referral' in p['item_types']: items += referral_items(db, pids, people, p, start, end, cap)
    if 'report_review' in p['item_types']: items += report_items(db, pids, people, p, start, end, cap)
    items = dedupe(items)
    data = Data(db, {i['patient_id'] for i in items}, cap)
    links = {}
    if cap['link']:
        for kind in ('review', 'test_order', 'referral'):
            mine = [i for i in items if (i['item_type'] if i['item_type'] in ('test_order', 'referral') else 'review') == kind]
            targets, streams = {t for i in mine for t in i.get('targets', ())}, citing_streams(cap['streams'], kind)
            later = ['labs'] if kind == 'test_order' and 'labs' in streams and 'laboratory_result_notifications' in streams else []
            found = _links_dated(db, targets, [s for s in streams if s not in later])
            if later:  # per-analyte lab rows only for orders without a result notification (one notification per order is far cheaper)
                done = {s for _, d in ORDERS.values() for s in d}
                for tgt, rows in _links_dated(db, {t for t in targets if not any(r[0] in done for r in found.get(t, ()))}, later).items():
                    found.setdefault(tgt, []).extend(rows)
            for tgt, rows in found.items():
                links.setdefault(tgt, []).extend(rows)
    close(items, data, links, p, as_of)
    report = analyse(items, p, as_of, people, cap)
    report.update(schema=VERSION, parameters=p, period={'start': start, 'end': end}, as_of=as_of,
                  capability={k: v for k, v in cap.items() if k not in ('tables', 'streams', 'data_gaps')}, data_gaps=cap['data_gaps'],
                  limitations=LIMITATIONS, assumptions=_assumptions(p))
    report['method'] = method(report)
    return report


def execute(path, patient_ids, params, as_of=None):
    db = sd.connect(path)
    try:
        return run(db, patient_ids, params, as_of)
    finally:
        db.close()


def _assumptions(p):
    w = p['windows']
    out = [f"Windows (days): critical {w['critical_result']}, abnormal {w['abnormal_result']}, positive screen 30 (mammogram) / 180 (FIT), "
           f"recommendation = stated interval + 25% (min 7) else {w['recommendation']}, orders 30-90 by type, referral {w['referral']}, report review {w['report_review']}.",
           'Lab limits: critical ' + ', '.join(f'{k} {lo if lo is not None else ""}/{hi if hi is not None else ""}' for k, (lo, hi) in CRITICAL.items()) +
           ('; abnormal = received H/L flag.' if p['abnormal_basis'] == 'reference_range' else '; abnormal = past an action limit (not every out-of-range flag).'),
           'The clock starts when the result or report was available (available date), else its event date.']
    if p['evidence'] == 'specific': out.append('Specific evidence only: visits and telephone contacts that do not cite the item do not close it.')
    return out


LIMITATIONS = ['Closure is inferred from received records; a follow-up done outside the received feeds looks open.',
               'Note acknowledgement is text matching (test name in a note or telephone message, or a note follow-up instruction): '
               'candidate evidence, not a signed acknowledgement.',
               'A visit or telephone contact in the window closes a result loop under evidence=any even if the result was not discussed.',
               'Owners are ordering/requesting clinicians when the order is linked, else the attributed PCP.',
               'Synthetic data.']


def method(report):
    p = report['parameters']
    return (f"Loop closure over items starting {report['period']['start']}..{report['period']['end']} (snapshot {report['as_of']}): "
            + '; '.join(f"{t.replace('_', ' ')} -> {EXPECTED[t]}" for t in p['item_types']) + '. '
            'Each item gets the earliest closure evidence after its start (structured review = a record that cites the result, its order or '
            'report via the link table; expected action; repeat test; medication order/intent change; visit; telephone contact; note text '
            'naming the test or a note-fact follow-up instruction). Closed on time = evidence by the due date; closed late = after; open overdue '
            '= none by the snapshot and past due; pending = not yet due. Close rate = closed on time / due, Wilson 95% interval. Time to '
            'closure: Kaplan-Meier (survival.kaplan_meier) censored at the item\'s age at the snapshot.')


def summarize(report, limit=None):
    if report.get('status') != 'answered':
        return 'No result, order, referral or recommendation loops in scope for ' + f"{report['period']['start']}..{report['period']['end']}.", []
    p = report['parameters']; c = report['status_counts']
    due = report['items_total'] - c.get('pending', 0)
    txt = (f"{report['items_total']} loops opened {report['period']['start']}..{report['period']['end']}: {c.get('open_overdue', 0)} open past due, "
           f"{c.get('closed_on_time', 0)} closed on time, {c.get('closed_late', 0)} closed late, {c.get('declined', 0)} declined, {c.get('pending', 0)} not yet due"
           + (f" (close rate {c.get('closed_on_time', 0) / due:.0%} of {due} due)" if due else '') + '. ')
    txt += 'By type: ' + '; '.join(f"{r['group'].replace('_', ' ')} {r['open_overdue']} open of {r['items']}"
                                   + (f", {r['close_rate']:.0%} on time" if r['close_rate'] is not None else '') for r in report['by_type']) + '.'
    crit = (report.get('by_tier') or {}).get('critical', {}).get('open_overdue', 0)
    if crit: txt += f' {crit} critical results have no acknowledgement or action evidence.'
    worst = [r for r in report['by_group'] if r['open_overdue']][:3]
    if worst and p['group_by'] != 'none':
        txt += f" Most open by {p['group_by'].replace('_', ' ')}: " + ', '.join(f"{r['group']} {r['open_overdue']}" + (f" ({r['close_rate']:.0%} on time)" if r['close_rate'] is not None else '') for r in worst) + '.'
    ev = report['closure_evidence']
    if ev: txt += ' Closure evidence: ' + ', '.join(f"{e['evidence'].replace('_', ' ')} {e['items']}" for e in ev[:6]) + '.'
    return txt, report['open_loops'][:limit or p['top']]


def follow_up():
    return ['Which abnormal lab results have no follow-up after 30 days?', 'Which referrals were never completed?',
            'Which critical lab results were not acknowledged?', 'What is our loop closure rate by practice?']
