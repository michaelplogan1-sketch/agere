"""Named standard default definitions: answer when a question doesn't state its definition, and name the one used.

Brief 1.3 (Mike, 10-20): no refusal for a missing definition when a published standard exists. Every result names its
definition (`definition`, `definitions`, limitation line) so the reader sees exactly what was counted.
  pqi_admissions   AHRQ Prevention Quality Indicators (PQI 90 overall composite): acute admissions whose principal
                   ICD-10-CM diagnosis falls in a PQI component family. The code families and age bounds are read from the
                   governed measure file config/agere/measures/pqi_90_overall_composite.json (AHRQ QI v2025), never retyped.
  ed_avoidability  NYU/Billings ED algorithm categories: each ED visit's principal diagnosis gets probabilities of
                   non-emergent (NE), emergent primary-care treatable (EPCT), emergent ED-needed preventable (ED_PA) and
                   not preventable (ED_NPA); injury, mental health, alcohol and substance use are special categories and
                   unmatched codes are unclassified (as in the published algorithm). Avoidable = NE + EPCT + ED_PA.
  avoidable_spend  PQI admission allowed dollars plus ED allowed dollars weighted by their avoidable probability.
  measure_gap      the governed measure's own denominator minus numerator (patient-unit measures in the measure library),
                   so gap lists come from the governed definition, never a re-implementation.
  pdc_adherence    PQA proportion of days covered >= 0.8 (measure-library PDC criterion: >= 2 fills on distinct dates,
                   >= 91-day treatment period, overlapping supply shifted forward); a library PDC measure is used when it
                   covers exactly the requested classes, else the same criterion applied to the requested classes.

NYU/Billings PROVENANCE: the published NYU ED algorithm (Billings, Parikh & Mijanovich, Commonwealth Fund 2000; NYU
Wagner ED algorithm documentation; Johnston et al., Health Serv Res 2017 ICD-10 update) assigns per-code probabilities
from an expert-panel review of ED records. That code-level file is not embedded here. NYU_ED below is an Agere-built
lookup by ICD-10-CM family constructed from the algorithm's public category definitions and its published worked
descriptions (e.g. upper respiratory infections and dental conditions largely non-emergent / primary-care treatable;
asthma, COPD and heart-failure exacerbations emergent but preventable with timely ambulatory care; MI, stroke,
appendicitis, sepsis emergent and not preventable). The probabilities are Agere's approximation, labelled as such on
every answer; replace NYU_ED with the licensed code-level file when one is received.
"""
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import re
import sqlite3

VERSION = 'default-definitions/1'
LIKELY_AVOIDABLE = 0.5  # a visit counts as "likely avoidable" for patient lists when NE + EPCT + ED_PA >= 0.5
PDC_THRESHOLD = 0.8
GROUP_BY = ('none', 'practice', 'pcp', 'payer')
SELECT = ('gap', 'numerator', 'denominator')
PQI_MEASURE = 'pqi_90_overall_composite'
PDC_TEMPLATE = 'pdc_statins'
PROVENANCE = ('NYU/Billings ED avoidability: Agere-built ICD-10-CM family lookup approximating the published NYU ED '
              'algorithm categories (Billings et al. 2000; Johnston et al. 2017 ICD-10 update); probabilities are Agere '
              'approximations, not the licensed code-level file.')
DEFINITIONS = {
    'pqi_admissions': {'name': 'AHRQ PQI 90 ambulatory-care-sensitive admissions',
                       'basis': 'Acute inpatient admissions with a principal ICD-10-CM diagnosis in an AHRQ PQI 90 component '
                                'family (AHRQ QI v2025 code families from the governed measure file; age bounds per component).'},
    'ed_avoidability': {'name': 'NYU/Billings ED avoidability',
                        'basis': 'Each ED visit weighted by NE + EPCT + ED-needed-preventable probability of its principal '
                                 'diagnosis; injury, mental health, alcohol, substance use and unclassified visits are reported '
                                 'separately and not counted as avoidable. ' + PROVENANCE},
    'avoidable_spend': {'name': 'Avoidable spend (AHRQ PQI admissions + NYU/Billings avoidable ED)',
                        'basis': 'Allowed dollars of PQI 90 admissions plus ED-visit allowed dollars weighted by the NYU/Billings '
                                 'avoidable probability. Claims are matched to a visit by patient and service date within the '
                                 'visit dates. ' + PROVENANCE},
    'measure_gap': {'name': 'Measure gap = governed denominator minus numerator',
                    'basis': "Patients in the governed measure's denominator (after its exclusions) who are not in its numerator."},
    'pdc_adherence': {'name': f'Adherence = PQA proportion of days covered >= {PDC_THRESHOLD}',
                      'basis': 'PDC from the first fill in the period to period end, >= 2 fills on distinct dates and a '
                               '>= 91-day treatment period; overlapping supply shifted forward (measure-library PDC criterion).'},
}
ALIASES = {'avoidable_spend': r'avoidable (?:spend|cost|dollars)|preventable (?:spend|cost)',
           'pqi_admissions': r'avoidable admissions?|preventable (?:admissions?|hospitali[sz]ations?)|ambulatory[- ]care[- ]sensitive|\bpqi\b|\bacsc\b',
           'ed_avoidability': r'avoidable ed|avoidable emergency|non[- ]?emergent ed|preventable ed|ed avoidab',
           'measure_gap': r'\bgaps?\b|open gaps|not met|missing (?:screening|measure)',
           'pdc_adherence': r'adheren|\bpdc\b|proportion of days covered'}

# ICD-10-CM family -> (NE, EPCT, ED_PA, ED_NPA); see PROVENANCE. Longest prefix wins.
NYU_ED = {
    'J00': (.80, .20, 0, 0), 'J02': (.75, .25, 0, 0), 'J01': (.70, .30, 0, 0), 'J03': (.70, .30, 0, 0), 'J06': (.70, .30, 0, 0),
    'J20': (.55, .40, .05, 0), 'J30': (.90, .10, 0, 0), 'H10': (.80, .20, 0, 0), 'H60': (.70, .30, 0, 0), 'H65': (.60, .40, 0, 0),
    'H66': (.60, .40, 0, 0), 'H92': (.70, .30, 0, 0), 'K04': (.70, .30, 0, 0), 'K05': (.80, .20, 0, 0), 'K08': (.75, .25, 0, 0),
    'L23': (.70, .30, 0, 0), 'L30': (.75, .25, 0, 0), 'L50': (.50, .40, .10, 0), 'B35': (.90, .10, 0, 0), 'B37': (.80, .20, 0, 0),
    'N39.0': (.40, .50, .10, 0), 'N30': (.40, .50, .10, 0), 'N76': (.60, .40, 0, 0), 'M54': (.60, .35, .05, 0), 'M25.5': (.55, .40, 0, .05),
    'M79': (.60, .35, 0, .05), 'R51': (.45, .35, 0, .20), 'G43': (.40, .40, .10, .10), 'G44': (.45, .40, .05, .10), 'R05': (.60, .40, 0, 0),
    'R50': (.40, .45, 0, .15), 'R11': (.30, .50, 0, .20), 'A09': (.45, .45, .10, 0), 'K52': (.45, .45, .10, 0), 'K59.0': (.70, .30, 0, 0),
    'R21': (.70, .30, 0, 0), 'Z76': (1.0, 0, 0, 0), 'Z00': (1.0, 0, 0, 0), 'Z02': (1.0, 0, 0, 0), 'Z09': (.80, .20, 0, 0),
    'Z48': (.70, .30, 0, 0), 'I10': (.30, .50, .15, .05), 'E11.65': (.10, .40, .40, .10), 'E11.9': (.30, .50, .20, 0),
    'E16.2': (.05, .35, .40, .20), 'J45': (.05, .30, .60, .05), 'J44': (.05, .25, .60, .10), 'I50': (0, .15, .60, .25),
    'E10.1': (0, .05, .65, .30), 'E11.1': (0, .05, .65, .30), 'L03': (.20, .55, .25, 0), 'L02': (.25, .55, .20, 0),
    'J18': (.05, .35, .30, .30), 'N10': (.05, .45, .30, .20), 'E86': (.10, .40, .30, .20), 'R10': (.15, .35, 0, .50),
    'R07': (.05, .25, 0, .70), 'R06.0': (.05, .30, .10, .55), 'R55': (.05, .25, 0, .70), 'R42': (.20, .40, 0, .40),
    'N20': (0, .30, 0, .70), 'I48': (0, .20, .20, .60), 'K35': (0, 0, 0, 1.0), 'K56': (0, 0, 0, 1.0), 'K92': (0, .05, .05, .90),
    'I21': (0, 0, 0, 1.0), 'I63': (0, 0, 0, 1.0), 'I61': (0, 0, 0, 1.0), 'I26': (0, 0, .05, .95), 'A41': (0, 0, .10, .90),
    'R65': (0, 0, .10, .90), 'N17': (0, .05, .25, .70), 'J96': (0, 0, .30, .70), 'O': (0, .30, 0, .70),
}
NYU_SPECIAL = [('alcohol', r'^F10'), ('substance', r'^F1[1-9]|^T4[0-3]'), ('mental_health', r'^F[0-9]'),
               ('injury', r'^[ST]|^V|^W|^X|^Y')]


def _norm(code): return re.sub(r'[^A-Z0-9]', '', str(code or '').upper())


def ed_category(code):
    """('weights', (ne, epct, ed_pa, ed_npa)) | (special category, None) | ('unclassified', None) for one ICD-10 code."""
    c = _norm(code)
    if not c: return 'unclassified', None
    for name, rx in NYU_SPECIAL:
        if re.match(rx, c): return name, None
    best = max((k for k in NYU_ED if c.startswith(_norm(k))), key=lambda k: len(_norm(k)), default=None)
    return ('weights', NYU_ED[best]) if best else ('unclassified', None)


def pqi_components():
    from . import measures
    return measures.library()[PQI_MEASURE]['numerator']['components']


def pqi_component(code, age=None):
    c = _norm(code)
    for comp in pqi_components():
        if any(c.startswith(_norm(p)) for p in comp['icd10_prefixes']) and (age is None or comp.get('age_min', 0) <= age <= comp.get('age_max', 200)):
            return comp
    return None


# ---- shared data access ---------------------------------------------------------------------------------------------
def _cols(db, table): return {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}


def _visits(db, concept, start, end, keep):
    """[{patient_id, encounter_id, day, end_day, principal}] for one visit concept in [start, end]."""
    vc = _cols(db, 'visit')
    if not vc: return []
    principal = 'principal_icd10' if 'principal_icd10' in vc else 'NULL'
    rows = [dict(zip(('patient_id', 'encounter_id', 'day', 'end_day', 'principal'), r)) for r in db.execute(
        f"SELECT patient_id, encounter_id, substr(event_date,1,10), substr(coalesce(end_date, event_date),1,10), {principal} "
        "FROM visit WHERE type_concept = ? AND substr(event_date,1,10) BETWEEN ? AND ?", (concept, start, end))]
    rows = [r for r in rows if keep is None or r['patient_id'] in keep]
    if any(not r['principal'] for r in rows) and _cols(db, 'encounter_diagnosis'):
        first = {}
        for enc, code in db.execute('SELECT encounter_id, icd10 FROM encounter_diagnosis WHERE icd10 IS NOT NULL ORDER BY rank'):
            first.setdefault(enc, code)
        for r in rows: r['principal'] = r['principal'] or first.get(r['encounter_id'])
    return rows


def _ed_visits(db, start, end, keep):
    """ED visits under the one governed definition (ed_definition.py: EHR + ADT + claims, one per patient-day); an EHR visit
    without a principal diagnosis takes its first-ranked encounter diagnosis, as _visits does."""
    from .ed_definition import events
    rows = events(db, start, end, keep)
    if any(not r['principal'] for r in rows) and _cols(db, 'encounter_diagnosis'):
        first = {}
        for enc, code in db.execute('SELECT encounter_id, icd10 FROM encounter_diagnosis WHERE icd10 IS NOT NULL ORDER BY rank'):
            first.setdefault(enc, code)
        for r in rows: r['principal'] = r['principal'] or first.get(r['encounter_id'])
    return rows


def _allowed(db, rows):
    """Allowed cents per visit: claims of the patient with a service date within the visit dates (None without claims)."""
    if not {'patient_id', 'service_date', 'allowed_cents'} <= _cols(db, 'claim'):
        for r in rows: r['allowed_cents'] = None
        return False
    claims = defaultdict(list)
    for pid, day, cents in db.execute('SELECT patient_id, substr(service_date,1,10), allowed_cents FROM claim WHERE allowed_cents IS NOT NULL'):
        claims[pid].append((day, cents))
    for r in rows: r['allowed_cents'] = sum(c for d, c in claims[r['patient_id']] if d and r['day'] <= d <= (r['end_day'] or r['day']))
    return True


def _ages(db):
    from .age import age_at
    if 'birth_date' not in _cols(db, 'patient'): return lambda pid, day: None
    born = dict(db.execute('SELECT patient_id, birth_date FROM patient'))
    return lambda pid, day: age_at(born.get(pid), day) if born.get(pid) else None


def _groups(db, group_by):
    if group_by == 'none': return lambda pid: 'all'
    col = {'practice': 'pcp_practice_id', 'pcp': 'pcp_clinician_id', 'payer': 'payer_id'}[group_by]
    cols = _cols(db, 'patient')
    if col not in cols and group_by == 'payer': col = 'coverage_type' if 'coverage_type' in cols else None
    labels = dict(db.execute(f'SELECT patient_id, {col} FROM patient')) if col in cols else {}
    return lambda pid: labels.get(pid) or 'Unknown'


# ---- definitions -----------------------------------------------------------------------------------------------------
def pqi_admissions(db, start, end, keep=None, group_by='none'):
    age, group = _ages(db), _groups(db, group_by)
    rows = []
    for v in _visits(db, 'inpatient_admission', start, end, keep):
        comp = pqi_component(v['principal'], age(v['patient_id'], v['day'])) if v['principal'] else None
        if comp: rows.append({**v, 'pqi': comp['id'], 'pqi_label': comp['label'], 'group': group(v['patient_id'])})
    priced = _allowed(db, rows)
    return {'events': rows, 'priced': priced, 'patient_ids': sorted({r['patient_id'] for r in rows}),
            'admissions': len(rows), 'allowed_cents': sum(r['allowed_cents'] or 0 for r in rows) if priced else None,
            'unclassified_admissions': None}


def ed_avoidability(db, start, end, keep=None, group_by='none'):
    group = _groups(db, group_by)
    rows, special = [], defaultdict(int)
    for v in _ed_visits(db, start, end, keep):
        kind, w = ed_category(v['principal'])
        if w is None: special[kind] += 1
        avoidable = round(w[0] + w[1] + w[2], 3) if w else 0.0
        rows.append({**v, 'nyu_category': kind, 'ne': w[0] if w else None, 'epct': w[1] if w else None,
                     'ed_preventable': w[2] if w else None, 'ed_not_preventable': w[3] if w else None,
                     'avoidable_probability': avoidable, 'group': group(v['patient_id'])})
    priced = _allowed(db, rows)
    likely = sorted({r['patient_id'] for r in rows if r['avoidable_probability'] >= LIKELY_AVOIDABLE})
    return {'events': rows, 'priced': priced, 'patient_ids': likely, 'visits': len(rows),
            'expected_avoidable_visits': round(sum(r['avoidable_probability'] for r in rows), 2),
            'avoidable_allowed_cents': round(sum(r['avoidable_probability'] * (r['allowed_cents'] or 0) for r in rows)) if priced else None,
            'special_categories': dict(special)}


def avoidable_spend(db, start, end, keep=None, group_by='none'):
    pqi, ed = pqi_admissions(db, start, end, keep, group_by), ed_avoidability(db, start, end, keep, group_by)
    table = defaultdict(lambda: {'pqi_admissions': 0, 'pqi_allowed_cents': 0, 'ed_visits': 0, 'expected_avoidable_ed_visits': 0.0,
                                 'avoidable_ed_allowed_cents': 0.0, 'patients': set()})
    for r in pqi['events']:
        t = table[r['group']]; t['pqi_admissions'] += 1; t['pqi_allowed_cents'] += r['allowed_cents'] or 0; t['patients'].add(r['patient_id'])
    for r in ed['events']:
        t = table[r['group']]; t['ed_visits'] += 1; t['expected_avoidable_ed_visits'] += r['avoidable_probability']
        t['avoidable_ed_allowed_cents'] += r['avoidable_probability'] * (r['allowed_cents'] or 0)
        if r['avoidable_probability'] >= LIKELY_AVOIDABLE: t['patients'].add(r['patient_id'])
    rows = []
    for g, t in table.items():
        total = t['pqi_allowed_cents'] + t['avoidable_ed_allowed_cents']
        rows.append({'group': g, 'avoidable_allowed_cents': round(total), 'pqi_admissions': t['pqi_admissions'],
                     'pqi_allowed_cents': t['pqi_allowed_cents'], 'ed_visits': t['ed_visits'],
                     'expected_avoidable_ed_visits': round(t['expected_avoidable_ed_visits'], 2),
                     'avoidable_ed_allowed_cents': round(t['avoidable_ed_allowed_cents']), 'patients': len(t['patients'])})
    rows.sort(key=lambda r: -r['avoidable_allowed_cents'])
    ids = sorted(set(pqi['patient_ids']) | set(ed['patient_ids']))
    priced = pqi['priced'] and ed['priced']
    return {'rows': rows, 'patient_ids': ids, 'priced': priced, 'pqi': {k: v for k, v in pqi.items() if k != 'events'},
            'ed': {k: v for k, v in ed.items() if k != 'events'},
            'allowed_cents': round(sum(r['avoidable_allowed_cents'] for r in rows)) if priced else None}


def _measure_rows(db, m, start, end, as_of, keep):
    from . import measures
    rep = measures.evaluate_db(db, m, start, end, as_of, output='patients', keep=keep, top=0)
    if rep.get('declined'): return None, rep.get('missing_inputs') or []
    return rep.get('patient_rows') or [], rep.get('warnings') or []


def membership(rows, lower_is_better=False):
    """Governed membership from patient rows: denominator (not excluded), numerator (met), gap (denominator - numerator)."""
    den = sorted({r['patient_id'] for r in rows if not r.get('excluded')})
    num = sorted({r['patient_id'] for r in rows if not r.get('excluded') and r.get('met')})
    gap = sorted(set(num) if lower_is_better else set(den) - set(num))
    return den, num, gap


def measure_gap(db, measure_id, start, end, as_of, keep=None):
    from . import measures
    lib = measures.library()
    if measure_id not in lib: raise ValueError('measure: one of ' + ', '.join(sorted(lib)))
    m = lib[measure_id]
    if m['unit'] != 'patient': raise ValueError(f'{measure_id} is not a patient-unit measure; it has no patient gap list')
    rows, notes = _measure_rows(db, m, start, end, as_of, keep)
    if rows is None: return {'declined': True, 'missing': notes, 'name': m['name']}
    den, num, gap = membership(rows, m['direction'] == 'lower_is_better')
    return {'declined': False, 'name': m['name'], 'spec_year': m.get('spec_year'), 'measure': measure_id, 'warnings': notes,
            'denominator_patient_ids': den, 'numerator_patient_ids': num, 'gap_patient_ids': gap,
            'rate': round(len(num) / len(den), 4) if den else None, 'rows': rows}


def pdc_measure(classes, threshold=PDC_THRESHOLD):
    """The library PDC measure covering exactly these classes, else the PQA template applied to them."""
    import copy
    from . import measures
    lib = measures.library()
    for mid, m in lib.items():
        crit = [c for c in m['numerator'].get('criteria', []) if c.get('type') == 'pdc']
        if len(crit) == 1 and set(crit[0]['classes']) == set(classes) and crit[0].get('threshold', PDC_THRESHOLD) == threshold:
            return m, True
    m = copy.deepcopy(lib[PDC_TEMPLATE])
    crit = next(c for c in m['numerator']['criteria'] if c['type'] == 'pdc')
    crit.update(classes=list(classes), threshold=threshold, label=f"PDC {', '.join(classes)}")
    crit.pop('exclude_names', None)
    m.update(id='pdc_' + '_'.join(classes), name=f"PDC >= {threshold} ({', '.join(classes)})", status='default_definition')
    return m, False


def pdc_adherence(db, classes, start, end, as_of, keep=None, threshold=PDC_THRESHOLD):
    from . import drug_classes
    unknown = [c for c in classes if c not in drug_classes.CLASSES]
    if not classes or unknown: raise ValueError('pdc_adherence needs received drug classes; unknown: ' + ', '.join(unknown))
    m, governed = pdc_measure(classes, threshold)
    rows, notes = _measure_rows(db, m, start, end, as_of, keep)
    if rows is None: return {'declined': True, 'missing': notes, 'name': m['name']}
    den, num, gap = membership(rows)
    return {'declined': False, 'name': m['name'], 'measure': m['id'], 'governed_measure': governed, 'warnings': notes,
            'denominator_patient_ids': den, 'numerator_patient_ids': num, 'gap_patient_ids': gap,
            'rate': round(len(num) / len(den), 4) if den else None,
            'rows': [{k: r.get(k) for k in ('patient_id', 'pdc', 'met', 'excluded', 'evidence')} for r in rows]}


# ---- governed macros for staged analyses ---------------------------------------------------------------------------
def macro_sql(kind, catalog):
    """{{pqi_admission}}: (patient_id, encounter_id, event_date, principal_icd10, pqi) admissions;
    {{ed_avoidability}}: (patient_id, encounter_id, event_date, principal_icd10, nyu_category, avoidable_probability)."""
    def q(v): return "'" + str(v).replace("'", "''") + "'"
    if kind == 'pqi_admission' and 'principal_icd10' not in (catalog.tables.get('visit') or ()):
        from .request_plan import PlanError
        raise PlanError(f'{kind} needs visit.principal_icd10, which this warehouse does not carry')
    from .ed_definition import relation_sql
    code = "replace(upper(v.principal_icd10), '.', '')"
    if kind == 'pqi_admission':
        age = 'age_at(p.birth_date, substr(v.event_date,1,10))'
        cases = ' '.join(f"WHEN {code} LIKE {q(_norm(x) + '%')} AND (p.birth_date IS NULL OR {age} BETWEEN {int(c.get('age_min', 0))} "
                         f"AND {int(c.get('age_max', 200))}) THEN {q(c['id'])}" for c in pqi_components() for x in c['icd10_prefixes'])
        return (f"(SELECT * FROM (SELECT v.patient_id, v.encounter_id, substr(v.event_date,1,10) AS event_date, v.principal_icd10, "
                f"CASE {cases} END AS pqi FROM visit v LEFT JOIN patient p ON p.patient_id = v.patient_id "
                "WHERE v.type_concept = 'inpatient_admission') WHERE pqi IS NOT NULL)")
    globs = [('alcohol', 'F10*'), ('substance', 'F1[1-9]*'), ('substance', 'T4[0-3]*'), ('mental_health', 'F[0-9]*'),
             ('injury', '[STVWXY]*')]
    special = ' '.join(f"WHEN {code} GLOB {q(g)} THEN {q(n)}" for n, g in globs)
    zero = ' '.join(f"WHEN {code} GLOB {q(g)} THEN 0" for _, g in globs)
    weights = sorted(NYU_ED.items(), key=lambda kv: -len(_norm(kv[0])))
    prob = 'CASE ' + ' '.join(f"WHEN {code} LIKE {q(_norm(k) + '%')} THEN {round(w[0] + w[1] + w[2], 3)}" for k, w in weights) + ' END'
    return (f"(SELECT v.patient_id, v.encounter_id, substr(v.event_date,1,10) AS event_date, v.principal_icd10, "
            f"CASE WHEN v.principal_icd10 IS NULL THEN 'unclassified' {special} WHEN ({prob}) IS NOT NULL THEN 'weighted' "
            f"ELSE 'unclassified' END AS nyu_category, "
            f"CASE WHEN v.principal_icd10 IS NULL THEN 0 {zero} ELSE coalesce(({prob}), 0) END AS avoidable_probability "
            f"FROM {relation_sql(catalog.tables)} v WHERE v.classification = 'ed')")  # the one governed ED definition


# ---- registered engine ----------------------------------------------------------------------------------------------
def default_period(as_of):
    from .decision_context import rolling_year  # one definition of a year: the 12 months ending at as_of
    return rolling_year(as_of)


def validate(p):
    if not isinstance(p, dict) or set(p) - {'definition', 'period', 'measure', 'drug_classes', 'threshold', 'group_by', 'select'}:
        raise ValueError('default_definition parameters: definition, period, measure, drug_classes, threshold, group_by, select')
    d = p.get('definition')
    if d not in DEFINITIONS: raise ValueError(f'definition: one of {sorted(DEFINITIONS)}')
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or any(
            v and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(v)) for v in period.values()):
        raise ValueError('period: {start, end} as YYYY-MM-DD')
    if d == 'measure_gap' and not p.get('measure'): raise ValueError('measure_gap needs a measure id from the measure library')
    if d == 'pdc_adherence' and not p.get('drug_classes'): raise ValueError('pdc_adherence needs drug_classes')
    threshold = p.get('threshold', PDC_THRESHOLD)
    if type(threshold) not in (int, float) or not 0 < threshold <= 1: raise ValueError('threshold: 0 < t <= 1')
    if p.get('group_by', 'none') not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    if p.get('select', 'gap') not in SELECT: raise ValueError(f'select: {SELECT}')
    return {'definition': d, 'period': {'start': period.get('start', ''), 'end': period.get('end', '')},
            'measure': p.get('measure', ''), 'drug_classes': list(p.get('drug_classes') or []), 'threshold': threshold,
            'group_by': p.get('group_by', 'none'), 'select': p.get('select', 'gap')}


def run(db, patient_ids, p, as_of):
    s0, e0 = default_period(as_of)
    start, end = p['period']['start'] or s0, p['period']['end'] or e0
    keep = set(patient_ids) if patient_ids is not None else None
    d = p['definition']
    base = {'schema': VERSION, 'definition': d, 'definition_name': DEFINITIONS[d]['name'], 'definition_basis': DEFINITIONS[d]['basis'],
            'period': {'start': start, 'end': end}, 'as_of': str(as_of)[:10], 'parameters': p}
    if d in ('pqi_admissions', 'ed_avoidability'):
        rep = (pqi_admissions if d == 'pqi_admissions' else ed_avoidability)(db, start, end, keep, p['group_by'])
        return {**base, **{k: v for k, v in rep.items() if k != 'events'}, 'rows': rep['events']}
    if d == 'avoidable_spend':
        return {**base, **avoidable_spend(db, start, end, keep, p['group_by'])}
    rep = (measure_gap(db, p['measure'], start, end, as_of, keep) if d == 'measure_gap'
           else pdc_adherence(db, p['drug_classes'], start, end, as_of, keep, p['threshold']))
    if not rep['declined']:
        rep['patient_ids'] = rep[f"{p['select']}_patient_ids"]
    return {**base, **rep}


def execute(path, patient_ids, params, as_of):
    from . import semantic_layer
    p = validate(params)
    target = semantic_layer.ensure(path) if (Path(path)/'run.json').exists() else Path(path)/'semantic.sqlite3'  # as Catalog.load
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        return run(db, patient_ids, p, as_of)
    finally:
        db.close()


def _dollars(cents): return 'not priced (no claims received)' if cents is None else f'${cents / 100:,.0f}'


def headline(r):
    d = r['definition']
    if r.get('declined'): return f"{r['definition_name']}: not computable from the received data (" + '; '.join(r.get('missing') or []) + ')'
    if d == 'pqi_admissions':
        return f"{r['admissions']} PQI admissions among {len(r['patient_ids'])} patients, allowed {_dollars(r['allowed_cents'])}"
    if d == 'ed_avoidability':
        return (f"{r['expected_avoidable_visits']} of {r['visits']} ED visits expected avoidable; {len(r['patient_ids'])} patients "
                f"with a likely avoidable visit; avoidable-weighted allowed {_dollars(r['avoidable_allowed_cents'])}")
    if d == 'avoidable_spend':
        return (f"Avoidable spend {_dollars(r['allowed_cents'])}: {r['pqi']['admissions']} PQI admissions and "
                f"{r['ed']['expected_avoidable_visits']} expected avoidable ED visits ({len(r['patient_ids'])} patients)")
    rate = 'n/a' if r['rate'] is None else f"{r['rate']:.1%}"
    return (f"{r['name']}: {len(r['numerator_patient_ids'])} of {len(r['denominator_patient_ids'])} ({rate}); "
            f"{len(r['gap_patient_ids'])} patients in the gap")


def answer(report, plan, question):
    """Answer contract for analysis_graph.build_answer_core: executed, with the definition used named on it."""
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    used = f"Definition used ({report['definition_name']}): {report['definition_basis']}"
    if report.get('declined'):
        return {'status': 'unsupported', 'answer_type': 'unsupported', 'value': headline(report), 'missing': report.get('missing') or [],
                'patient_ids': [], 'limitations': [used]}
    ids = report.get('patient_ids') or []
    out = {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['rows'], 'count': len(ids),
           'patient_ids': ids, 'value': f"{headline(report)} ({report['period']['start']} to {report['period']['end']}).",
           'method': used, 'definitions': [{'term': report['definition_name'], 'basis': report['definition_basis']}],
           'limitations': [used, *report.get('warnings', [])], 'membership_role': report['parameters']['select']
           if report['definition'] in ('measure_gap', 'pdc_adherence') else 'contributing'}
    for k in ('numerator_patient_ids', 'denominator_patient_ids', 'rate'):
        if k in report: out[k] = report[k]
    return out


def detect(question):
    """Definitions a question's wording asks for without stating one (for planners and prompts)."""
    q = (question or '').lower()
    return [d for d, rx in ALIASES.items() if re.search(rx, q)]
