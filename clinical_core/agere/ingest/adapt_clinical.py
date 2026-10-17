"""EHR clinical adapters: landing tables (Epic, athena, eCW, FHIR, HL7) -> Agere canonical clinical records.

    records(db, resolve) -> iterator of canonical record dicts      (contract: ingest/canonical.py)
    last_report()        -> counts of the last run: records per stream, dropped by reason, unmapped values, missing tables

Streams produced (field names as docs/agere/canonical_streams.json and the semantic layer read them):
    encounters (with diagnoses list), conditions (EHR problem lists, diagnosis_basis 'ehr_problem_list' as in v2.8),
    labs, vitals (wide columns), mood_questionnaires (PHQ-2/PHQ-9 totals found in flowsheets / result tables),
    medication_orders, medication_lists, appointments (one record per delivered version), notes, immunizations,
    allergies, adt_messages. Problem-list entries are emitted once, on `conditions` (they also carry problem_code), so
    the semantic layer's condition table, which reads conditions and problem_lists alike, does not count them twice.
    No procedure table is delivered by any EHR feed (athena CLINICALSERVICE*/Epic hsp_* are empty), so none are emitted.

Source of truth (docs/agere/NATIVE_FEED_INVENTORY.md section 3):
    * vendor extract (Epic / athena / eCW) wins for its practice; fhir_bulk of a vendor practice (and DEEPCHART) is
      dropped as a duplicate (its labs still feed the ORU duplicate check);
    * FHIR is authoritative for the FHIR-native practices; ehr/fhir and fhir_bulk copies collapse to one record;
    * HL7 ORU results are added only when MRN + LOINC (else value-only) + collection minute + value is not already
      present from the EHR (reference-lab results); corrected results (OBX-11 C, Epic result_status 4, athena C,
      eCW Corrected) supersede the earlier version (supersedes_record_id + replaces_result_record_id);
    * every source key keeps one record per distinct delivered version: identical re-sends / monthly copies / _RESEND
      rows collapse into the first copy (lineage kept), a _v2 file replaces its original in the same drop folder,
      a changed version supersedes the previous one (stage() then keeps only the current one). Appointments keep every
      version WITHOUT supersedes_record_id (the semantic layer's VERSIONED_STREAMS logic reads the history);
    * source retirements (eCW deleteFlag=1, athena deleteddatetime, Epic allergy status Deleted) remove the entity;
      an eCW appointment slot re-sent as CHK with deleteFlag=1 is the slot that became a visit (status fulfilled) and is
      never counted as an encounter;
    * EHR migrations (athena/eCW -> EPIC-CA): the same person + practice + day (encounters) or + LOINC + minute + value
      (labs) from a second vendor is dropped (needs the identity resolver to join the two charts).

Person keys tried in order (most specific first), built with ingest/identity.py's helpers so they match its map:
native key (epic_key / athena_key / ecw_key / fhir_key), then mrn_key (practice from the MRN, else the issuing context:
Epic identity type name, FHIR folder, ORU authority), then hl7_key (assigning authority + PID-3.1).
organization_id is a WH... id (WHN-PRACTICE-CA-5, WHN-HOSPITAL-AK-0; EPIC-DEEPCHART departments 'SYNTHETIC CARE
PRACTICE n' -> WH-PRACTICE-<n-1>, 'SYNTHETIC DISTRICT n HOSPITAL' -> WH-HOSPITAL-<n-1>, as in v2.8).
Dates: event_date = clinical date, available_date = file drop date (date in the file name, else last day of the drop
month). record_id = 'AGR-' + sha256(stream | source business key | version content)[:24]. stdlib only.
"""
from __future__ import annotations

import base64
import calendar
from collections import Counter, defaultdict
import hashlib
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .identity import athena_key, ecw_key, epic_key, fhir_key, hl7_key, mrn_key

ENVELOPE = ('record_id', 'patient_id', 'source_table', 'source_system', 'organization_id', 'event_date', 'recorded_date',
            'available_date', 'encounter_id', 'source_patient_id', 'supersedes_record_id', 'event_date_precision',
            'event_time', 'recorded_time', 'signed_time', 'identity_link_basis')
VOLATILE = {'record_id', 'supersedes_record_id', 'replaces_result_record_id', 'available_date', 'available_time', '_lineage',
            'patient_id', 'identity_link_basis'}
PROBLEM_LIST_STREAM = 'conditions'
FHIR_NATIVE_PRACTICES = {'WHN-PRACTICE-CA-5', 'WHN-PRACTICE-LA-0', 'WHN-PRACTICE-NY-5', 'WHN-PRACTICE-VA-0'}
VENDOR_PRIORITY = ('epic', 'athena', 'ecw', 'fhir', 'hl7')

_REPORT: dict = {}


def last_report():
    return json.loads(json.dumps(_REPORT, default=str))


# ------------------------------------------------------------------------------------------------ small helpers
_JUNK = re.compile(r'(?:Ren(?:é|Ã©|�)e)+$')


def _c(v):
    """Clean a landed text value: cut at NUL, strip the appended cp1252 'Renee' fragments of malformed rows."""
    if v is None:
        return None
    v = str(v).split('\x00')[0]
    v = _JUNK.sub('', v).strip()
    return v or None


def _num(v):
    v = _c(v)
    if v is None:
        return None
    try:
        f = float(v.replace(',', ''))
    except ValueError:
        return None
    if f != f or f in (float('inf'), float('-inf')):
        return None
    return int(f) if re.fullmatch(r'[+-]?\d+', v) else f


def _value(v):
    """Numeric when the text is a number, else the text (e.g. '>60')."""
    n = _num(v)
    return n if n is not None else _c(v)


def _vkey(v):
    n = _num(v) if not isinstance(v, (int, float)) else v
    if n is not None:
        return ('n', round(float(n), 4))
    return ('t', str(v or '').strip().lower())


def _iso(v):
    """Vendor date/time text -> ISO ('YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS[offset]'); None when not a date."""
    v = _c(v)
    if not v:
        return None
    m = re.match(r'^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2})(:\d{2})?(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$', v)
    if m:
        d, hm, s, tz = m.groups()
        if not _valid_day(d):
            return None
        if not hm:
            return d
        return f'{d}T{hm}{s or ":00"}{tz or ""}'
    m = re.match(r'^(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2})?)?', v)  # HL7 TS
    if m:
        y, mo, d, hh, mi, ss = m.groups()
        day = f'{y}-{mo}-{d}'
        if not _valid_day(day):
            return None
        return f'{day}T{hh}:{mi}:{ss or "00"}' if hh else day
    return None


def _valid_day(d):
    try:
        y, m, dd = (int(x) for x in d.split('-'))
        return 1 <= m <= 12 and 1 <= dd <= calendar.monthrange(y, m)[1]
    except (ValueError, TypeError):
        return False


def _day(v):
    s = _iso(v)
    return s[:10] if s else None


def _minute(v):
    """Wall-clock minute as sent (offset ignored): the key the ORU/EHR comparison uses."""
    s = _iso(v)
    return s[:16] if s and 'T' in s else None


def _prec(v):
    s = _iso(v)
    return None if not s else ('minute' if 'T' in s else 'day')


def _join_dt(d, t):
    d, t = _c(d), _c(t)
    if not d:
        return None
    if t and re.fullmatch(r'\d{2}:\d{2}(:\d{2})?', t):
        return _iso(f'{d} {t}')
    return _iso(d)


def _icd(code):
    code = _c(code)
    if not code:
        return None
    code = code.upper().replace(' ', '')
    if '.' not in code and len(code) > 3 and code[0].isalpha():
        code = code[:3] + '.' + code[3:]
    return code


def _snake(s):
    s = _c(s)
    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_') or None if s else None


def _h(*parts):
    return hashlib.sha256('|'.join(json.dumps(p, sort_keys=True, default=str) for p in parts).encode()).hexdigest()[:24]


def _variant(r):
    v = _c(r.get('file_variant'))
    if v:
        return v.lower()
    f = r.get('_file') or ''
    if re.search(r'_v2(?=\.[^./]+$)', f, re.I):
        return 'v2'
    if re.search(r'_resend(?=\.[^./]+$)', f, re.I):
        return 'resend'
    return None


def _file_group(f):
    f = f or ''
    d, _, b = f.rpartition('/')
    return d, re.sub(r'_(v2|resend)(?=\.[^.]+$)', '', b, flags=re.I).lower()


_RANK = {'resend': 0, None: 1, 'v2': 2}


def _order(r):
    return (r.get('_drop') or '', _RANK.get(r.get('_variant'), 1), r.get('_file') or '', r.get('_row') or 0)


def _avail(r):
    f = r.get('_file') or ''
    m = re.search(r'(20\d{2})(\d{2})(\d{2})(?!\d)', f.rsplit('/', 1)[-1])
    if m and _valid_day(f'{m[1]}-{m[2]}-{m[3]}'):
        return f'{m[1]}-{m[2]}-{m[3]}'
    d = r.get('_drop') or ''
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d):
        return d
    if re.fullmatch(r'\d{4}-\d{2}', d):
        y, mo = int(d[:4]), int(d[5:])
        return f'{d}-{calendar.monthrange(y, mo)[1]:02d}'
    return None


def _inst(r):
    return (r.get('_instance') or '').split('/')[-1]


def _lin(table, r):
    return {'table': table, 'file': r.get('_file'), 'row': r.get('_row')}


def _org_from_name(name):
    """Department / identity-type / MRN text -> WH... organization id."""
    s = _c(name) or ''
    m = re.search(r'\bWHN[ -]PRACTICE[ -]([A-Z]{2})[ -](\d+)\b', s)
    if m:
        return f'WHN-PRACTICE-{m[1]}-{m[2]}'
    m = re.search(r'\bWHN ([A-Z]{2}) PRIMARY CARE (\d+)\b', s)
    if m:
        return f'WHN-PRACTICE-{m[1]}-{m[2]}'
    m = re.search(r'\bSYNTHETIC CARE PRACTICE (\d+)\b', s, re.I)
    if m:
        return f'WH-PRACTICE-{int(m[1]) - 1}'
    m = re.search(r'\bSYNTHETIC DISTRICT (\d+) HOSPITAL', s, re.I)
    if m:
        return f'WH-HOSPITAL-{int(m[1]) - 1}'
    m = re.match(r'^I?CE-([A-Z]{2})-(\d+)-', s)
    if m:
        return f'WHN-PRACTICE-{m[1]}-{m[2]}'
    return None


def _org_from_authority(a):
    m = re.fullmatch(r'(PRAC|HOSP)([A-Z]{2})(\d+)', _c(a) or '')
    if not m:
        return None
    return f'WHN-{"PRACTICE" if m[1] == "PRAC" else "HOSPITAL"}-{m[2]}-{m[3]}'


def _flag(value, rr):
    """H / L / N from a numeric value and a 'low-high', '<high' or '>low' reference range."""
    if not isinstance(value, (int, float)) or not rr:
        return None
    rr = str(rr).replace(' ', '')
    m = re.fullmatch(r'([\d.]+)-([\d.]+)', rr)
    try:
        if m:
            lo, hi = float(m[1]), float(m[2])
            return 'L' if value < lo else 'H' if value > hi else 'N'
        m = re.fullmatch(r'<=?([\d.]+)', rr)
        if m:
            return 'H' if value > float(m[1]) else 'N'
        m = re.fullmatch(r'>=?([\d.]+)', rr)
        if m:
            return 'L' if value < float(m[1]) else 'N'
    except ValueError:
        return None
    return None


def _hl7_flag(f):
    f = (_c(f) or '').split(',')[0].upper()
    return {'H': 'H', 'HH': 'H', 'L': 'L', 'LL': 'L', 'N': 'N', 'A': 'A', 'AA': 'A'}.get(f)


STATE_ZONE = {'AK': 'America/Anchorage', 'AL': 'America/Chicago', 'AR': 'America/Chicago', 'AZ': 'America/Phoenix',
              'CA': 'America/Los_Angeles', 'CO': 'America/Denver', 'CT': 'America/New_York', 'DE': 'America/New_York',
              'FL': 'America/New_York', 'GA': 'America/New_York', 'HI': 'Pacific/Honolulu', 'IA': 'America/Chicago',
              'ID': 'America/Boise', 'IL': 'America/Chicago', 'IN': 'America/Indiana/Indianapolis', 'KS': 'America/Chicago',
              'KY': 'America/New_York', 'LA': 'America/Chicago', 'MA': 'America/New_York', 'MD': 'America/New_York',
              'ME': 'America/New_York', 'MI': 'America/Detroit', 'MN': 'America/Chicago', 'MO': 'America/Chicago',
              'MS': 'America/Chicago', 'MT': 'America/Denver', 'NC': 'America/New_York', 'ND': 'America/Chicago',
              'NE': 'America/Chicago', 'NH': 'America/New_York', 'NJ': 'America/New_York', 'NM': 'America/Denver',
              'NV': 'America/Los_Angeles', 'NY': 'America/New_York', 'OH': 'America/New_York', 'OK': 'America/Chicago',
              'OR': 'America/Los_Angeles', 'PA': 'America/New_York', 'RI': 'America/New_York', 'SC': 'America/New_York',
              'SD': 'America/Chicago', 'TN': 'America/Chicago', 'TX': 'America/Chicago', 'UT': 'America/Denver',
              'VA': 'America/New_York', 'VT': 'America/New_York', 'WA': 'America/Los_Angeles', 'WI': 'America/Chicago',
              'WV': 'America/New_York', 'WY': 'America/Denver', 'DC': 'America/New_York'}
_UTC_TS = re.compile(r'^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?$')


def _utc_to_local(v, zone):
    """athena 'YYYY-MM-DD HH:MM:SS.000' (UTC) -> local ISO with offset. Midnight values are dates and stay dates."""
    m = _UTC_TS.match(v) if isinstance(v, str) else None
    if not m or not zone:
        return v
    d, hh, mi, ss = m.groups()
    if (hh, mi, ss or '00') == ('00', '00', '00'):
        return d
    try:
        t = datetime(*map(int, d.split('-')), int(hh), int(mi), int(ss or 0), tzinfo=timezone.utc).astimezone(ZoneInfo(zone))
    except (ValueError, KeyError):
        return v
    return t.isoformat()


# Default units by LOINC, used only when a feed sends a lab value without a unit (eCW labdatadetail).
LOINC_UNITS = {'2951-2': 'mmol/L', '2823-3': 'mmol/L', '2075-0': 'mmol/L', '2028-9': 'mmol/L', '2160-0': 'mg/dL',
               '3094-0': 'mg/dL', '2345-7': 'mg/dL', '1558-6': 'mg/dL', '718-7': 'g/dL', '6690-2': '10*3/uL',
               '777-3': '10*3/uL', '4544-3': '%', '4548-4': '%', '2085-9': 'mg/dL', '13457-7': 'mg/dL',
               '18262-6': 'mg/dL', '2089-1': 'mg/dL', '2093-3': 'mg/dL', '2571-8': 'mg/dL', '33914-3': 'mL/min/1.73m2',
               '62238-1': 'mL/min/1.73m2', '98979-8': 'mL/min/1.73m2', '48642-3': 'mL/min/1.73m2',
               '9318-7': 'mg/g', '14959-1': 'mg/g', '3016-3': 'mIU/L', '2276-4': 'ng/mL', '33762-6': 'pg/mL',
               '17861-6': 'mg/dL', '1751-7': 'g/dL', '1742-6': 'U/L', '1920-8': 'U/L', '6768-6': 'U/L', '1975-2': 'mg/dL'}

# Same, by analyte name, when the feed sent neither a unit nor a LOINC (eCW practices without labloinccodes rows).
NAME_UNITS = ((r'egfr|glomerular', 'mL/min/1.73m2'), (r'microalb|albumin/creat', 'mg/g'), (r'a1c', '%'),
              (r'nt-?pro', 'pg/mL'), (r'creatinine', 'mg/dL'), (r'sodium', 'mmol/L'), (r'potassium', 'mmol/L'),
              (r'glucose', 'mg/dL'), (r'ldl|hdl|cholesterol|triglycer', 'mg/dL'), (r'hemoglobin|^hgb', 'g/dL'),
              (r'white blood|^wbc', '10*3/uL'), (r'platelet', '10*3/uL'), (r'tsh', 'mIU/L'), (r'^alt\b|^ast\b', 'U/L'),
              (r'urea nitrogen|^bun', 'mg/dL'))


def _name_unit(name):
    s = (name or '').lower()
    return next((u for pat, u in NAME_UNITS if re.search(pat, s)), None)


EPIC_ROUTE = {'6': 'inhalation', '9': 'intravenous', '11': 'intramuscular', '12': 'nasal', '15': 'oral', '16': 'ophthalmic',
              '18': 'subcutaneous', '19': 'sublingual', '20': 'topical', '24': 'transdermal', '26': 'rectal', '28': 'vaginal'}
EPIC_ENC_TYPE = {'101': 'office', '76': 'virtual', '1214': 'orders_only'}
EPIC_APPT = {'1': 'booked', '2': 'fulfilled', '3': 'cancelled', '4': 'no_show', '6': 'arrived'}
EPIC_ORDER_STATUS = {'2': 'active', '5': 'completed', '4': 'cancelled', '9': 'discontinued'}
EPIC_PROBLEM = {'1': 'active', '2': 'inactive', '3': 'resolved'}
EPIC_NOTE_TYPE = {'1': 'progress_note', '5': 'telephone_encounter', '17': 'patient_instructions'}
EPIC_NOTE_STATUS = {'2': 'signed', '3': 'addendum'}
EPIC_IMM_STATUS = {'1': 'completed', '3': 'historical'}
ECW_APPT = {'PEN': 'booked', 'ARR': 'arrived', 'CANC': 'cancelled', 'N/S': 'no_show', 'CHK': 'fulfilled'}
ECW_VISIT = {'FU': 'office', 'PHY': 'wellness', 'WCC': 'pediatric_office', 'TEL': 'telephone', 'NV': 'nurse_visit'}
ECW_PURPOSE = {'FU': 'follow_up', 'PHY': 'physical', 'WCC': 'well_child', 'TEL': 'telephone_visit', 'NV': 'nurse_visit'}
FHIR_APPT = {'booked': 'booked', 'fulfilled': 'fulfilled', 'cancelled': 'cancelled', 'noshow': 'no_show', 'arrived': 'arrived',
             'checked-in': 'arrived', 'pending': 'booked', 'proposed': 'booked', 'entered-in-error': 'cancelled'}
FHIR_ENC_STATUS = {'in-progress': 'in_progress', 'finished': 'completed', 'arrived': 'in_progress', 'planned': 'scheduled',
                   'cancelled': 'cancelled', 'triaged': 'in_progress', 'onleave': 'in_progress'}
FHIR_ENC_ORDER = {'planned': 0, 'arrived': 1, 'triaged': 1, 'in-progress': 2, 'onleave': 2, 'finished': 3, 'cancelled': 3,
                  'entered-in-error': 4}
HL7_DISPOSITION = {'01': 'home', '02': 'short_term_hospital', '03': 'snf', '04': 'icf', '05': 'other_facility',
                   '06': 'home_health', '07': 'left_ama', '09': 'admitted_inpatient', '20': 'expired', '21': 'law_enforcement',
                   '43': 'federal_hospital', '50': 'hospice_home', '51': 'hospice_facility', '61': 'swing_bed', '62': 'irf',
                   '63': 'ltch', '64': 'nursing_facility', '65': 'psychiatric', '66': 'critical_access_hospital'}
LAB_STATUS = {'F': 'final', 'C': 'corrected', 'P': 'preliminary', 'X': 'cancelled', 'D': 'deleted', 'A': 'amended',
              'I': 'in_process', 'R': 'entered_not_verified'}
PHQ = {'55757-9': 'PHQ-2', '55758-7': 'PHQ-2', '44261-6': 'PHQ-9'}


def _phq(name, loinc=None):
    if loinc in PHQ:
        return PHQ[loinc]
    s = (name or '').upper().replace(' ', '').replace('-', '')
    if 'ITEM' in s:
        return None
    if 'PHQ9' in s:
        return 'PHQ-9'
    if 'PHQ2' in s:
        return 'PHQ-2'
    return None


# ------------------------------------------------------------------------------------------------ vitals conversion
VITAL_UNITS = {'blood_pressure_systolic': 'mmHg', 'blood_pressure_diastolic': 'mmHg', 'heart_rate': 'beats/min',
               'weight_kg': 'kg', 'height_cm': 'cm', 'bmi_kg_m2': 'kg/m2', 'temperature_C': 'Cel',
               'respiratory_rate': 'breaths/min', 'oxygen_saturation_percent': '%'}


def _vital(kind, value, unit=None):
    """(column, converted value) for one vital sign; kind is a normalized name, unit as sent."""
    v = _num(value) if not isinstance(value, (int, float)) else value
    if v is None:
        return None
    u = (_c(unit) or '').lower().replace('[', '').replace(']', '')
    if kind == 'weight':
        f = {'oz': 0.028349523125, 'lb': 0.45359237, 'lbs': 0.45359237, 'lb_av': 0.45359237, 'g': 0.001, 'kg': 1.0}.get(u, 1.0)
        return 'weight_kg', round(v * f, 2)
    if kind == 'height':
        f = {'in': 2.54, 'in_i': 2.54, 'inch': 2.54, 'm': 100.0, 'cm': 1.0}.get(u, 1.0)
        return 'height_cm', round(v * f, 1)
    if kind == 'temperature':
        return 'temperature_C', round((v - 32) * 5 / 9, 1) if u in ('degf', 'f', '°f') else round(v, 1)
    col = {'systolic': 'blood_pressure_systolic', 'diastolic': 'blood_pressure_diastolic', 'pulse': 'heart_rate',
           'bmi': 'bmi_kg_m2', 'resp': 'respiratory_rate', 'spo2': 'oxygen_saturation_percent'}.get(kind)
    return (col, round(v, 1) if kind == 'bmi' else v) if col else None


def _vital_kind(name):
    s = (name or '').lower()
    if 'systolic' in s:
        return 'systolic'
    if 'diastolic' in s:
        return 'diastolic'
    if 'blood pressure' in s or s in ('bp',):
        return 'bp'
    if 'pulse' in s or 'heart rate' in s:
        return 'pulse'
    if 'bmi' in s or 'body mass' in s:
        return 'bmi'
    if 'weight' in s:
        return 'weight'
    if 'height' in s:
        return 'height'
    if 'temp' in s:
        return 'temperature'
    if 'resp' in s:
        return 'resp'
    if 'spo2' in s or 'oxygen' in s or 'o2 sat' in s:
        return 'spo2'
    return None


FHIR_VITAL_LOINC = {'8480-6': 'systolic', '8462-4': 'diastolic', '8867-4': 'pulse', '29463-7': 'weight', '3141-9': 'weight',
                    '8302-2': 'height', '8306-3': 'height', '39156-5': 'bmi', '8310-5': 'temperature', '8331-1': 'temperature',
                    '9279-1': 'resp', '59408-5': 'spo2', '2708-6': 'spo2'}


# ------------------------------------------------------------------------------------------------ the run
class _Run:
    def __init__(self, db, resolve):
        self.db, self._resolve = db, resolve
        self.tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.out = Counter()
        self.dropped = Counter()
        self.dropped_by_stream = defaultdict(Counter)
        self.unmapped = defaultdict(Counter)
        self.missing = set()
        self.unresolved = Counter()
        self._v2 = {}
        self._pcache = {}
        self.lab_presence = defaultdict(list)      # (org, mrn, loinc, day) -> EHR values (kept records + FHIR copies)
        self.lab_presence_any = defaultdict(list)  # (org, mrn, '*', day) -> values, for ORU results without LOINC
        self.migration = {}            # migration dedupe key -> vendor

    # ---- reading
    def has(self, t):
        if t in self.tables:
            return True
        self.missing.add(t)
        return False

    def _v2_groups(self, table):
        if table not in self._v2:
            s = set()
            for (f,) in self.db.execute(f'SELECT DISTINCT _file FROM "{table}"'):
                if f and re.search(r'_v2(?=\.[^./]+$)', f, re.I):
                    s.add(_file_group(f))
            self._v2[table] = s
        return self._v2[table]

    def rows(self, table, count=True, stream=None):
        if not self.has(table):
            return
        v2 = self._v2_groups(table)
        cur = self.db.execute(f'SELECT * FROM "{table}"')
        cols = [d[0] for d in cur.description]
        for t in cur:
            r = dict(zip(cols, t))
            var = _variant(r)
            if var is None and v2 and _file_group(r.get('_file')) in v2:
                if count:
                    self.drop('superseded_by_v2_file', stream or table)
                continue
            r['_variant'] = var
            if table.startswith('ehr_athena_'):
                zone = self._athena_zone(r.get('contextid') or _inst(r))
                for k, v in r.items():
                    if k[0] != '_' and isinstance(v, str) and len(v) >= 16:
                        r[k] = _utc_to_local(v, zone)
            yield r

    def _athena_zone(self, ctx):
        """athena datetimes are UTC (encounterdatetimeutc = createddatetime); local zone from the practice state."""
        if not hasattr(self, '_azones'):
            self._azones = {}
            if 'ehr_athena_department' in self.tables:
                for c, inst, name in self.db.execute('SELECT contextid, _instance, departmentname FROM ehr_athena_department'):
                    m = re.search(r'PRACTICE-([A-Z]{2})-', _c(name) or '')
                    if m and m[1] in STATE_ZONE:
                        self._azones.setdefault(_c(c) or (inst or '').split('/')[-1], STATE_ZONE[m[1]])
        return self._azones.get(_c(ctx))

    def lookup(self, table, key, extra_key=None):
        """{(instance, key value[, extra]) : best row}: clean rows beat malformed ones, later copies beat earlier."""
        best = {}
        for r in self.rows(table, count=False):
            k = _c(r.get(key))
            if not k:
                continue
            kk = (_inst(r), k) if not extra_key else (_inst(r), k, _c(r.get(extra_key)))
            o = (0 if r.get('flat_row_issue') else 1, _order(r))
            if kk not in best or o > best[kk][0]:
                best[kk] = (o, r)
        return {k: v[1] for k, v in best.items()}

    def drop(self, reason, stream, n=1):
        self.dropped[reason] += n
        self.dropped_by_stream[stream][reason] += n

    def unmap(self, what, value):
        self.unmapped[what][str(value)[:80]] += 1

    # ---- persons
    def person(self, keys):
        """First source key that resolves, most specific first. Returns (person_id or None, basis key)."""
        keys = [k for k in keys if k]
        for k in keys:
            if k not in self._pcache:
                try:
                    self._pcache[k] = self._resolve(k)
                except Exception:  # noqa: BLE001 - a resolver failure leaves the record unresolved, never fatal
                    self._pcache[k] = None
            if self._pcache[k]:
                return self._pcache[k], k
        return None, (keys[0] if keys else None)

    # ---- record assembly
    def base(self, stream, r, table, *, vendor, instance, org, keys, source_patient_id, event=None, recorded=None,
             encounter_id=None, signed=None):
        pid, basis = self.person(keys)
        ev = _iso(event)
        rec = {'patient_id': pid, 'source_table': stream, 'source_system': f'{vendor}:{instance}', 'organization_id': org,
               'event_date': ev[:10] if ev else None, 'event_time': ev if ev and 'T' in ev else None,
               'event_date_precision': _prec(event) or 'unknown',
               'recorded_date': _day(recorded), 'recorded_time': _iso(recorded) if _prec(recorded) == 'minute' else None,
               'signed_time': _iso(signed) if _prec(signed) == 'minute' else None,
               'available_date': _avail(r), 'encounter_id': encounter_id, 'source_patient_id': source_patient_id,
               'supersedes_record_id': None, 'identity_link_basis': basis, '_lineage': [_lin(table, r)]}
        return rec

    def versions(self, stream, cands, chain=True):
        """cands: {key: [(order, rec, resend)]} -> records, one per distinct version, chained by supersedes_record_id."""
        out = []
        for key, lst in cands.items():
            lst.sort(key=lambda x: x[0])
            vers = []  # [sig, rec]
            for _, rec, resend in lst:
                sig = json.dumps({k: v for k, v in rec.items() if k not in VOLATILE}, sort_keys=True, default=str)
                hit = next((v for v in vers if v[0] == sig), None) if resend else (vers[-1] if vers and vers[-1][0] == sig else None)
                if hit:
                    hit[1]['_lineage'].extend(rec['_lineage'])
                    feeds = {(x.get('file') or '').split('/')[0] for x in hit[1]['_lineage']}
                    reason = 'fhir_native_bulk_copy' if stream and len(feeds) > 1 and 'fhir_bulk' in feeds else \
                        ('resend_duplicate' if resend else 'repeated_identical_copy')
                    self.drop(reason, stream)
                    continue
                vers.append([sig, rec])
            prev = None
            for i, (sig, rec) in enumerate(vers):
                rec['record_id'] = 'AGR-' + _h(stream, key, sig, i)
                if prev is not None and chain:
                    rec['supersedes_record_id'] = prev['record_id']
                    if stream == 'labs':
                        rec['replaces_result_record_id'] = prev['record_id']
                    self.drop('superseded_version', stream)  # the earlier version is kept as a record; stage() retires it
                prev = rec
                out.append(rec)
        return out

    def finish(self, rec):
        for k in ENVELOPE:
            rec.setdefault(k, None)
        for k in [k for k, v in rec.items() if v is None and k not in ENVELOPE]:
            del rec[k]
        if not rec.get('patient_id'):
            self.unresolved[rec['source_table']] += 1
        self.out[rec['source_table']] += 1
        return rec

    def migration_dup(self, rec, key):
        """Second vendor's copy of the same practice event after an EHR migration."""
        if not rec.get('patient_id') or key is None:
            return False
        vendor = rec['source_system'].split(':')[0]
        k = (rec['source_table'], rec.get('organization_id'), rec['patient_id']) + tuple(key)
        seen = self.migration.get(k)
        if seen is None:
            self.migration[k] = vendor
            return False
        if seen != vendor:
            self.drop('ehr_migration_duplicate', rec['source_table'])
            return True
        return False

    def emit(self, recs, migration=None):
        for rec in recs:
            if migration and self.migration_dup(rec, migration(rec)):
                continue
            yield self.finish(rec)

    # ================================================================================================= Epic
    def epic(self):
        if not self.has('ehr_epic_patient') and not self.has('ehr_epic_pat_enc'):
            return
        L = {
            'edg': self.lookup('ehr_epic_clarity_edg', 'dx_id'),
            'dep': self.lookup('ehr_epic_clarity_dep', 'department_id'),
            'ser': self.lookup('ehr_epic_clarity_ser', 'prov_id'),
            'ser2': self.lookup('ehr_epic_clarity_ser_2', 'prov_id'),
            'idtype': self.lookup('ehr_epic_identity_id_type', 'id_type'),
            'comp': self.lookup('ehr_epic_clarity_component', 'component_id'),
            'lnc': self.lookup('ehr_epic_lnc_db_main', 'record_id'),
            'eap': self.lookup('ehr_epic_clarity_eap', 'proc_id'),
            'med': self.lookup('ehr_epic_clarity_medication', 'medication_id'),
            'imm': self.lookup('ehr_epic_clarity_immunzatn', 'immunzatn_id'),
            'elg': self.lookup('ehr_epic_cl_elg', 'allergen_id'),
            'flo': self.lookup('ehr_epic_ip_flo_gp_data', 'flo_meas_id'),
        }
        rx = {}
        for (inst, mid, line), r in self.lookup('ehr_epic_rxnorm_codes', 'medication_id', 'line').items():
            code = _c(r.get('rxnorm_code'))
            if code and ((inst, mid) not in rx or (line or '') < rx[(inst, mid)][0]):
                rx[(inst, mid)] = (line or '', code)
        L['rx'] = {k: v[1] for k, v in rx.items()}
        ids = defaultdict(dict)  # (inst, pat) -> {line: (mrn, type name)}
        for (inst, pat, line), r in self.lookup('ehr_epic_identity_id', 'pat_id', 'line').items():
            mrn = _c(r.get('identity_id'))
            t = _c(r.get('identity_type_id'))
            name = _c((L['idtype'].get((inst, t)) or {}).get('id_type_name'))
            if mrn:
                ids[(inst, pat)][line] = (mrn, name)
        L['ids'] = ids
        L['enc_dept'] = {}
        for (inst, csn), r in self.lookup('ehr_epic_pat_enc_2', 'pat_enc_csn_id').items():
            L['enc_dept'][(inst, csn)] = _c(r.get('department_id'))
        self.E = L
        yield from self._epic_encounters()
        yield from self._epic_problems()
        yield from self._epic_labs()
        yield from self._epic_vitals()
        yield from self._epic_meds()
        yield from self._epic_appointments()
        yield from self._epic_notes()
        yield from self._epic_allergies()
        yield from self._epic_immunizations()

    def _epic_pat(self, inst, pat):
        """(mrn, practice org, person keys) for an Epic patient; practice MRNs preferred over hospital MRNs."""
        lines = self.E['ids'].get((inst, pat)) or {}
        best = None
        for line, (mrn, tname) in sorted(lines.items(), key=lambda x: (int(x[0]) if (x[0] or '').isdigit() else 999)):
            org = _org_from_name(tname) or _org_from_name(mrn)
            score = 0 if org and 'PRACTICE' in org else 1
            if best is None or score < best[0]:
                best = (score, mrn, org)
        mrn, org = (best[1], best[2]) if best else (None, None)
        tname = next((t for m_, t in lines.values() if m_ == mrn), None)
        keys = [epic_key(inst, pat), mrn_key(mrn, tname or inst) if mrn else None]
        return mrn, org, keys

    def _epic_org(self, inst, csn=None, dept=None, pat_org=None):
        dept = dept or (self.E['enc_dept'].get((inst, csn)) if csn else None)
        if dept:
            org = _org_from_name((self.E['dep'].get((inst, dept)) or {}).get('department_name'))
            if org:
                return org
        return pat_org

    def _epic_clin(self, inst, prov):
        prov = _c(prov)
        if not prov or not prov.isdigit():  # column-shifted rows put dates / text here
            return None
        npi = _c((self.E['ser2'].get((inst, prov)) or {}).get('npi'))
        return npi if npi and re.fullmatch(r'\d{10}', npi) else f'epic:{inst}:{prov}'

    def _epic_base(self, stream, r, table, inst, pat, csn=None, dept=None, **kw):
        mrn, porg, keys = self._epic_pat(inst, pat)
        org = self._epic_org(inst, csn, dept, porg)
        rec = self.base(stream, r, table, vendor='epic', instance=inst, org=org, keys=keys,
                        source_patient_id=mrn or pat, encounter_id=f'epic:{inst}:{csn}' if csn else None, **kw)
        return rec, mrn, org

    def _epic_encounters(self):
        dx = defaultdict(dict)
        for r in self.rows('ehr_epic_pat_enc_dx', stream='encounters'):
            inst, csn, line = _inst(r), _c(r.get('pat_enc_csn_id')), _c(r.get('line'))
            if not (csn and csn.isdigit() and line and line.isdigit()):
                self.drop('malformed_row', 'encounters')
                continue
            cur = dx[(inst, csn)].get(int(line))
            if cur is None or _order(r) >= cur[0]:
                dx[(inst, csn)][int(line)] = (_order(r), _c(r.get('dx_id')))
        enc2 = defaultdict(list)
        for r in self.rows('ehr_epic_pat_enc_2', stream='encounters'):
            csn = _c(r.get('pat_enc_csn_id'))
            if csn and csn.isdigit():
                enc2[(_inst(r), csn)].append(r)
        cands = defaultdict(list)
        for r in self.rows('ehr_epic_pat_enc', stream='encounters'):
            inst, csn, pat = _inst(r), _c(r.get('pat_enc_csn_id')), _c(r.get('pat_id'))
            if not (csn and csn.isdigit() and pat and re.fullmatch(r'Z\d+', pat)):
                self.drop('malformed_row', 'encounters')
                continue
            cands[(inst, csn)].append(r)
        out = defaultdict(list)
        for (inst, csn), encs in cands.items():
            e = max(encs, key=_order)
            diags = []
            for line, (_, dx_id) in sorted(dx.get((inst, csn), {}).items()):
                g = self.E['edg'].get((inst, dx_id)) or {}
                code = _icd(g.get('icd10_code') or g.get('ref_bill_code'))
                if code:
                    diags.append({'rank': len(diags) + 1, 'icd10': code, 'type': 'primary' if not diags else 'secondary',
                                  'description': _c(g.get('dx_name'))})
                elif dx_id:
                    self.unmap('epic.dx_id_without_icd10', dx_id)
            etype = EPIC_ENC_TYPE.get(_c(e.get('enc_type_c')) or '')
            if not etype:
                self.unmap('epic.enc_type_c', e.get('enc_type_c'))
            statuses = sorted(enc2.get((inst, csn), []), key=_order) or [None]
            for s in statuses:
                src = s or e
                appt = _c((s or {}).get('appt_status_c'))
                closed = _c((s or {}).get('enc_closed_yn'))
                status = 'completed' if closed == 'Y' or appt == '2' else 'in_progress' if appt == '6' else (
                    'completed' if s is None else (EPIC_APPT.get(appt) or 'in_progress'))
                rec, mrn, org = self._epic_base('encounters', src, 'ehr_epic_pat_enc' if s is None else 'ehr_epic_pat_enc_2',
                                                inst, _c(e.get('pat_id')), csn, dept=_c((s or {}).get('department_id')),
                                                event=e.get('contact_date'), recorded=(s or {}).get('update_date') or e.get('contact_date'))
                rec['_lineage'].insert(0, _lin('ehr_epic_pat_enc', e))
                dep = self.E['dep'].get((inst, _c((s or {}).get('department_id')) or self.E['enc_dept'].get((inst, csn))))
                rec.update({'type': etype or _snake(e.get('enc_type_c')), 'status': status,
                            'start_time': _iso(_c((s or {}).get('hosp_admsn_time'))) or _day(e.get('contact_date')),
                            'end_time': _iso(_c((s or {}).get('hosp_dischrg_time'))) or (_day((s or {}).get('enc_close_date')) if closed == 'Y' else None),
                            'attending_clinician_id': self._epic_clin(inst, e.get('visit_prov_id')),
                            'department': _c((dep or {}).get('department_name')), 'specialty': _c((dep or {}).get('specialty')),
                            'place_of_service': '02' if etype == 'virtual' else '11' if etype == 'office' else None,
                            'diagnoses': diags})
                out[(inst, csn)].append((_order(src), rec, src.get('_variant') == 'resend'))
        yield from self.emit(self.versions('encounters', out), migration=lambda r: (r['event_date'], r.get('type') != 'virtual'))

    def _epic_problems(self):
        out = defaultdict(list)
        for r in self.rows('ehr_epic_problem_list', stream=PROBLEM_LIST_STREAM):
            inst, pid, pat = _inst(r), _c(r.get('problem_list_id')), _c(r.get('pat_id'))
            if not (pid and pid.isdigit() and pat and re.fullmatch(r'Z\d+', pat)):
                self.drop('malformed_row', PROBLEM_LIST_STREAM)
                continue
            g = self.E['edg'].get((inst, _c(r.get('dx_id')))) or {}
            code = _icd(g.get('icd10_code') or g.get('ref_bill_code'))
            noted = _c(r.get('noted_date')) or _c(r.get('date_of_entry'))
            rec, mrn, org = self._epic_base(PROBLEM_LIST_STREAM, r, 'ehr_epic_problem_list', inst, pat, event=noted,
                                            recorded=r.get('date_of_entry'))
            status = EPIC_PROBLEM.get(_c(r.get('problem_status_c')) or '')
            rec.update(_condition_fields(_c(g.get('dx_name')), code, None, status, _day(r.get('noted_date')),
                                         _day(r.get('resolved_date')), None))
            out[(inst, pid)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions(PROBLEM_LIST_STREAM, out))

    def _epic_labs(self):
        procs = self.lookup('ehr_epic_order_proc', 'order_proc_id')
        proc2 = self.lookup('ehr_epic_order_proc_2', 'order_proc_id')
        out = defaultdict(list)
        for r in self.rows('ehr_epic_order_results', stream='labs'):
            inst, op, line = _inst(r), _c(r.get('order_proc_id')), _c(r.get('line'))
            pat = _c(r.get('pat_id')) or _c((procs.get((inst, op)) or {}).get('pat_id'))
            if not (op and op.isdigit() and line and line.isdigit() and pat and re.fullmatch(r'Z\d+', pat)):
                self.drop('malformed_row', 'labs')
                continue
            p = procs.get((inst, op)) or {}
            comp = self.E['comp'].get((inst, _c(r.get('component_id')))) or {}
            loinc = _c((self.E['lnc'].get((inst, _c(comp.get('default_lnc_id')))) or {}).get('lnc_code'))
            spec = _iso((proc2.get((inst, op)) or {}).get('specimn_taken_time'))
            num = _c(r.get('ord_num_value'))
            value = _num(num) if num and num != '9999999' else None
            if value is None:
                value = _value(r.get('ord_value'))
            lo, hi = _c(r.get('reference_low')), _c(r.get('reference_high'))
            rr = f'{lo}-{hi}' if lo and hi else (f'>{lo}' if lo else f'<{hi}' if hi else None)
            inr = _c(r.get('result_in_range_yn'))
            flag = 'N' if inr == 'Y' else (_flag(value, rr) or 'A') if inr == 'N' else _flag(value, rr)
            st = _c(r.get('result_status_c'))
            status = {'3': 'final', '4': 'corrected'}.get(st or '')
            if not status:
                self.unmap('epic.result_status_c', st)
            csn = _c(r.get('pat_enc_csn_id')) or _c(p.get('pat_enc_csn_id'))
            rec, mrn, org = self._epic_base('labs', r, 'ehr_epic_order_results', inst, pat, csn if csn and csn.isdigit() else None,
                                            event=spec or r.get('result_time'), recorded=r.get('result_time'))
            name = _c(comp.get('name')) or _c(comp.get('base_name'))
            if not name:
                self.unmap('epic.component_without_name', r.get('component_id'))
            rec.update(_lab_fields(name, loinc, _c(comp.get('base_name')), value, _c(r.get('reference_unit')), rr, flag,
                                   status or 'final', spec, _iso(r.get('result_time')), f'epic:{inst}:{op}',
                                   _c((self.E['eap'].get((inst, _c(p.get('proc_id')))) or {}).get('proc_name'))))
            out[(inst, op, line)].append((_order(r), rec, r['_variant'] == 'resend'))
            self._presence(org, mrn, rec)
        yield from self.emit(self.versions('labs', out), migration=_lab_migration_key)

    def _epic_vitals(self):
        recs = self.lookup('ehr_epic_ip_flwsht_rec', 'fsd_id')
        meas = defaultdict(dict)
        for r in self.rows('ehr_epic_ip_flwsht_meas', stream='vitals'):
            inst, fsd, line = _inst(r), _c(r.get('fsd_id')), _c(r.get('line'))
            if not (fsd and fsd.isdigit() and line and line.isdigit()):
                self.drop('malformed_row', 'vitals')
                continue
            cur = meas[(inst, fsd)].get(line)
            if cur is None or _order(r) >= _order(cur):
                if cur is not None:
                    self.drop('resend_duplicate' if r['_variant'] == 'resend' or cur['_variant'] == 'resend' else 'repeated_identical_copy', 'vitals')
                meas[(inst, fsd)][line] = r
            else:
                self.drop('repeated_identical_copy', 'vitals')
        vit, phq = defaultdict(list), defaultdict(list)
        for (inst, fsd), lines in meas.items():
            head = recs.get((inst, fsd))
            if not head or not re.fullmatch(r'Z\d+', _c(head.get('pat_id')) or ''):
                self.drop('vital_without_flowsheet_header', 'vitals', len(lines))
                continue
            pat, csn = _c(head.get('pat_id')), _c(head.get('pat_enc_csn_id'))
            values, units, times, lin = {}, {}, [], []
            for line, m in sorted(lines.items(), key=lambda x: int(x[0])):
                flo = self.E['flo'].get((inst, _c(m.get('flo_meas_id')))) or {}
                fname, funit = _c(flo.get('flo_meas_name')), _c(flo.get('units'))
                t = _iso(m.get('recorded_time'))
                instrument = _phq(fname)
                if instrument:
                    rec, mrn, org = self._epic_base('mood_questionnaires', m, 'ehr_epic_ip_flwsht_meas', inst, pat,
                                                    csn if csn and csn.isdigit() else None, event=t or head.get('record_date'))
                    rec.update({'instrument': instrument, 'total': _num(m.get('meas_value')), 'result_status': 'final'})
                    phq[(inst, fsd, line)].append((_order(m), rec, m['_variant'] == 'resend'))
                    continue
                if fname and 'PHQ' in fname.upper():
                    continue  # PHQ item answers are not totals
                kind = _vital_kind(fname)
                if kind == 'bp':
                    mm = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', _c(m.get('meas_value')) or '')
                    if mm:
                        values['blood_pressure_systolic'], values['blood_pressure_diastolic'] = int(mm[1]), int(mm[2])
                    else:
                        self.unmap('epic.blood_pressure_value', m.get('meas_value'))
                else:
                    cv = _vital(kind, m.get('meas_value'), funit) if kind else None
                    if cv:
                        values[cv[0]] = cv[1]
                    else:
                        self.unmap('epic.flowsheet_measure', fname or m.get('flo_meas_id'))
                if t:
                    times.append(t)
                lin.append(_lin('ehr_epic_ip_flwsht_meas', m))
            if not values:
                continue
            first = min(lines.values(), key=_order)
            rec, mrn, org = self._epic_base('vitals', head, 'ehr_epic_ip_flwsht_rec', inst, pat, csn if csn and csn.isdigit() else None,
                                            event=min(times) if times else head.get('record_date'))
            rec['available_date'] = _avail(first)
            rec['_lineage'] += lin
            rec.update(values)
            rec['units'] = {k: VITAL_UNITS[k] for k in values}
            vit[(inst, fsd)].append((_order(first), rec, False))
        yield from self.emit(self.versions('vitals', vit))
        yield from self.emit(self.versions('mood_questionnaires', phq))

    def _epic_meds(self):
        m2 = self.lookup('ehr_epic_order_med_2', 'order_med_id')
        info = self.lookup('ehr_epic_order_medinfo', 'order_med_id')
        sig = self.lookup('ehr_epic_order_med_sig', 'order_id')
        orders, lists = defaultdict(list), defaultdict(list)
        for r in self.rows('ehr_epic_order_med', stream='medication_orders'):
            inst, oid, pat = _inst(r), _c(r.get('order_med_id')), _c(r.get('pat_id'))
            if not (oid and oid.isdigit() and pat and re.fullmatch(r'Z\d+', pat)):
                self.drop('malformed_row', 'medication_orders')
                continue
            d2 = m2.get((inst, oid)) or {}
            mid = _c((info.get((inst, oid)) or {}).get('medication_id'))
            med = self.E['med'].get((inst, mid)) or {}
            name = _c(d2.get('description')) or _c(med.get('name'))
            rxcui = self.E['rx'].get((inst, mid))
            route = EPIC_ROUTE.get(_c(d2.get('med_route_c')) or _c(med.get('route_c')) or '')
            status = EPIC_ORDER_STATUS.get(_c(r.get('order_status_c')) or '')
            if not status:
                self.unmap('epic.order_status_c', r.get('order_status_c'))
            cls = _c(d2.get('order_class_c'))
            csn = _c(r.get('pat_enc_csn_id'))
            historical = cls == '3'
            stream = 'medication_lists' if historical else 'medication_orders'
            rec, mrn, org = self._epic_base(stream, r, 'ehr_epic_order_med', inst, pat, csn if csn and csn.isdigit() else None,
                                            event=r.get('order_inst') or d2.get('ordering_date'), recorded=r.get('order_inst'))
            sigtext = _c((sig.get((inst, oid)) or {}).get('sig_text'))
            if historical:
                rec.update({'name': name, 'rxcui': rxcui, 'dose': _c(med.get('strength')), 'route': route, 'sig': sigtext,
                            'frequency': _frequency(sigtext),
                            'list_status': 'active' if status in (None, 'active', 'completed') else 'discontinued',
                            'source': 'historical', 'medication_list_id': f'epic:{inst}:{oid}'})
                lists[(inst, oid)].append((_order(r), rec, r['_variant'] == 'resend'))
            else:
                rec.update({'medication_name': name, 'rxcui': rxcui, 'prescription_id': f'epic:{inst}:{oid}', 'status': status,
                            'order_status': status, 'order_class': 'normal' if cls == '1' else _snake(cls), 'sig': sigtext,
                            'directions_text': sigtext, 'route': route, 'frequency': _frequency(sigtext),
                            'strength': _c(med.get('strength')), 'quantity': _num(r.get('quantity')),
                            'days_supply': _num(d2.get('disp_days_supply')), 'refills_authorized': _num(d2.get('refills')),
                            'start_time': _iso(r.get('order_inst')) or _day(d2.get('start_date')), 'end_time': _day(d2.get('end_date')),
                            'ordering_clinician_id': self._epic_clin(inst, r.get('authrzing_prov_id')),
                            'pharmacy': _c(d2.get('pharmacy_name')), 'pending': _c(r.get('is_pending_ord_yn')) == 'Y'})
                orders[(inst, oid)].append((_order(r), rec, r['_variant'] == 'resend'))
            if not name:
                self.unmap('epic.order_med_without_name', oid)
        yield from self.emit(self.versions('medication_orders', orders))
        yield from self.emit(self.versions('medication_lists', lists))

    def _epic_appointments(self):
        out = defaultdict(list)
        for r in self.rows('ehr_epic_f_sched_appt', stream='appointments'):
            inst, csn, pat = _inst(r), _c(r.get('pat_enc_csn_id')), _c(r.get('pat_id'))
            if not (csn and csn.isdigit() and pat and re.fullmatch(r'Z\d+', pat)):
                self.drop('malformed_row', 'appointments')
                continue
            out[(inst, csn)].append(r)
        cands = defaultdict(list)
        for (inst, csn), rows in out.items():
            prev_date = None
            for r in sorted(rows, key=_order):
                st = _c(r.get('appt_status_c'))
                status = EPIC_APPT.get(st or '')
                if not status:
                    self.unmap('epic.appt_status_c', st)
                sched = _day(r.get('contact_date')) or _day(r.get('appt_dttm'))
                moved = prev_date and sched and sched != prev_date
                if moved and status in ('booked', 'cancelled'):
                    status = 'rescheduled'
                rec, mrn, org = self._epic_base('appointments', r, 'ehr_epic_f_sched_appt', inst, pat=_c(r.get('pat_id')),
                                                dept=_c(r.get('department_id')), event=r.get('appt_made_dttm') if status == 'booked' else None,
                                                recorded=r.get('appt_made_dttm'))
                rec['encounter_id'] = None
                if not rec['event_date']:
                    rec['event_date'], rec['event_date_precision'] = rec['available_date'], 'day'
                purpose = _snake(r.get('prc_name'))
                rec.update({'appointment_id': f'epic:{inst}:{csn}', 'scheduled_date': sched, 'scheduled_time': _iso(r.get('appt_dttm')),
                            'status': status or _snake(st), 'purpose': purpose, 'visit_type': purpose,
                            'clinician_id': self._epic_clin(inst, r.get('prov_id')), 'slot_minutes': _num(r.get('appt_length')),
                            'booked_at': _iso(r.get('appt_made_dttm')), 'modality': 'video' if purpose and 'video' in purpose else None,
                            'previous_scheduled_date': prev_date if moved else None})
                if sched:
                    prev_date = sched
                cands[(inst, csn)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions('appointments', cands, chain=False))

    def _epic_notes(self):
        text = defaultdict(dict)
        for r in self.rows('ehr_epic_hno_note_text', stream='notes'):
            inst, nid, line = _inst(r), _c(r.get('note_id')), _c(r.get('line'))
            if not (nid and nid.isdigit() and line and line.isdigit()):
                self.drop('malformed_row', 'notes')
                continue
            cur = text[(inst, nid)].get(int(line))
            if cur is None or _order(r) >= cur[0]:
                text[(inst, nid)][int(line)] = (_order(r), r.get('note_text') or '')
        out = defaultdict(list)
        used = set()
        for r in self.rows('ehr_epic_hno_info', stream='notes'):
            inst, nid, pat = _inst(r), _c(r.get('note_id')), _c(r.get('pat_id'))
            if not (nid and nid.isdigit() and pat and re.fullmatch(r'Z\d+', pat)):
                self.drop('malformed_row', 'notes')
                continue
            lines = text.get((inst, nid))
            used.add((inst, nid))
            body = '\n'.join(v[1] for _, v in sorted(lines.items())) if lines else None
            csn = _c(r.get('pat_enc_csn_id'))
            created = r.get('create_instant_dttm')
            rec, mrn, org = self._epic_base('notes', r, 'ehr_epic_hno_info', inst, pat, csn if csn and csn.isdigit() else None,
                                            event=created, recorded=created, signed=created)
            nt = _c(r.get('ip_note_type_c'))
            ns = _c(r.get('note_status_c'))
            if nt not in EPIC_NOTE_TYPE:
                self.unmap('epic.ip_note_type_c', nt)
            rec.update({'note_id': f'epic:{inst}:{nid}', 'note_type': EPIC_NOTE_TYPE.get(nt or '') or _snake(nt),
                        'note_status': EPIC_NOTE_STATUS.get(ns or '') or _snake(ns),
                        'author_id': self._epic_clin(inst, r.get('current_author_id')), 'signed_date': _day(created),
                        'authored_time': _iso(created), 'text': body, 'text_lines': len(lines) if lines else 0})
            if body is None:
                self.unmap('epic.note_without_text', nid)
            out[(inst, nid)].append((_order(r), rec, r['_variant'] == 'resend'))
        orphan = sum(len(v) for k, v in text.items() if k not in used)
        if orphan:
            self.drop('note_text_without_header', 'notes', orphan)
        yield from self.emit(self.versions('notes', out))

    def _epic_allergies(self):
        out = defaultdict(list)
        for r in self.rows('ehr_epic_allergy', stream='allergies'):
            inst, aid, pat = _inst(r), _c(r.get('allergy_id')), _c(r.get('pat_id'))
            if not (aid and aid.isdigit() and pat and re.fullmatch(r'Z\d+', pat)):
                self.drop('malformed_row', 'allergies')
                continue
            out[(inst, aid)].append(r)
        cands = defaultdict(list)
        for (inst, aid), rows in out.items():
            rows.sort(key=_order)
            if _c(rows[-1].get('alrgy_status_c')) == '2':
                self.drop('retired_by_source', 'allergies', len(rows))
                continue
            for r in rows:
                g = self.E['elg'].get((inst, _c(r.get('allergen_id')))) or {}
                name = _c(g.get('allergen_name'))
                nka = bool(name and re.search(r'NO KNOWN', name, re.I))
                rx = _c(g.get('rxnorm_code'))
                rec, mrn, org = self._epic_base('allergies', r, 'ehr_epic_allergy', inst, _c(r.get('pat_id')), event=r.get('date_noted'),
                                                recorded=r.get('date_noted'))
                reaction = _c(r.get('reaction'))
                sub = name.lower() if name and not nka else None
                rec.update({'substance': sub, 'coded': bool(rx), 'code': rx, 'code_system': 'RxNorm' if rx else None,
                            'reaction': reaction, 'status': 'active', 'no_known_allergies': nka,
                            'display': 'NKDA' if nka else (f'{name} — {reaction}' if reaction else name), 'verification': 'not_supplied'})
                cands[(inst, aid)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions('allergies', cands))

    def _epic_immunizations(self):
        out = defaultdict(list)
        for r in self.rows('ehr_epic_immune', stream='immunizations'):
            inst, iid, pat = _inst(r), _c(r.get('immune_id')), _c(r.get('pat_id'))
            if not (iid and iid.isdigit() and pat and re.fullmatch(r'Z\d+', pat)):
                self.drop('malformed_row', 'immunizations')
                continue
            v = self.E['imm'].get((inst, _c(r.get('immunzatn_id')))) or {}
            rec, mrn, org = self._epic_base('immunizations', r, 'ehr_epic_immune', inst, pat, event=r.get('immune_date'))
            st = _c(r.get('imm_status_c'))
            if st not in EPIC_IMM_STATUS:
                self.unmap('epic.imm_status_c', st)
            rec.update({'vaccine': (_c(v.get('name')) or '').lower() or None, 'cvx_code': _c(v.get('cvx_code')), 'cpt': _c(v.get('cpt_code')),
                        'administration_status': EPIC_IMM_STATUS.get(st or '') or _snake(st),
                        'route': EPIC_ROUTE.get(_c(r.get('route_c')) or ''), 'dose': _num(r.get('dose')), 'dose_unit': _c(r.get('dose_unit'))})
            out[(inst, iid)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions('immunizations', out))

    # ================================================================================================= athena
    def athena(self):
        if not self.has('ehr_athena_patient'):
            return
        pats = {}
        for (ctx, pid), r in self.lookup('ehr_athena_patient', 'patientid').items():
            if pid.isdigit():
                pats[(ctx, pid)] = _c(r.get('enterpriseid'))
        by_mrn = {(ctx, mrn): pid for (ctx, pid), mrn in pats.items() if mrn}
        charts = {}
        for (ctx, cid), r in self.lookup('ehr_athena_chart', 'chartid').items():
            mrn = _c(r.get('enterpriseid'))
            if mrn and not re.match(r'^I?CE-', mrn) and re.match(r'^I?CE-', _c(r.get('newchartid')) or ''):
                mrn = _c(r.get('newchartid'))  # the 13 CHART rows with enterpriseid/newchartid swapped
            charts[(ctx, cid)] = mrn
        deps = {k: _org_from_name(v.get('departmentname')) for k, v in self.lookup('ehr_athena_department', 'departmentid').items()}
        ctx_org = {}
        for (ctx, _), org in deps.items():
            if org:
                ctx_org.setdefault(ctx, org)
        prov = {k: _c(v.get('providernpinumber')) for k, v in self.lookup('ehr_athena_provider', 'providerid').items()}
        cprov = {k: _c(v.get('npi')) for k, v in self.lookup('ehr_athena_clinicalprovider', 'clinicalproviderid').items()}
        self.A = {'pats': pats, 'by_mrn': by_mrn, 'charts': charts, 'deps': deps, 'ctx_org': ctx_org, 'prov': prov, 'cprov': cprov}
        yield from self._athena_encounters()
        yield from self._athena_problems()
        yield from self._athena_labs()
        yield from self._athena_vitals()
        yield from self._athena_meds()
        yield from self._athena_documents()

    def _athena_person(self, ctx, pid=None, chart=None):
        mrn = self.A['pats'].get((ctx, pid)) if pid else None
        if chart and not pid:
            mrn = self.A['charts'].get((ctx, chart))
            pid = self.A['by_mrn'].get((ctx, mrn)) if mrn else None
        org = _org_from_name(mrn) or self.A['ctx_org'].get(ctx)
        keys = [athena_key(ctx, pid) if pid else None, mrn_key(mrn, org) if mrn else None]
        return pid, mrn, org, keys

    def _athena_base(self, stream, r, table, ctx, pid=None, chart=None, dept=None, enc=None, **kw):
        pid, mrn, porg, keys = self._athena_person(ctx, pid, chart)
        org = self.A['deps'].get((ctx, _c(dept))) if dept else None
        org = org or porg
        rec = self.base(stream, r, table, vendor='athena', instance=ctx, org=org, keys=keys, source_patient_id=mrn or pid,
                        encounter_id=f'athena:{ctx}:{enc}' if enc else None, **kw)
        return rec, mrn, org

    def _athena_clin(self, ctx, provider=None, clinical_provider=None):
        npi = self.A['prov'].get((ctx, _c(provider))) if provider else self.A['cprov'].get((ctx, _c(clinical_provider)))
        if npi and re.fullmatch(r'\d{10}', npi):
            return npi
        p = _c(provider) or _c(clinical_provider)
        return f'athena:{ctx}:{p}' if p and p.isdigit() else None

    @staticmethod
    def _deleted(r):
        return bool(_c(r.get('deleteddatetime')) or (_c(r.get('isdeleted')) or '').lower() == 'true' or _c(r.get('deactivatedby')))

    def _keyed(self, rows, key, stream, valid=r'\d+'):
        """Group rows by (context, key), dropping entities whose latest version is a source deletion."""
        g = defaultdict(list)
        for r in rows:
            k = _c(r.get(key))
            if not k or not re.fullmatch(valid, k):
                self.drop('malformed_row', stream)
                continue
            g[(_inst(r), k)].append(r)
        out = {}
        for k, lst in g.items():
            lst.sort(key=_order)
            if self._deleted(lst[-1]):
                self.drop('retired_by_source', stream, len(lst))
                continue
            out[k] = lst
        return out

    def _athena_encounters(self):
        icd = {k: _icd(v.get('unstrippeddiagnosiscode') or v.get('diagnosiscode')) for k, v in self.lookup('ehr_athena_icdcodeall', 'icdcodeid').items()}
        icd_desc = {k: _c(v.get('diagnosiscodedescription')) for k, v in self.lookup('ehr_athena_icdcodeall', 'icdcodeid').items()}
        dx10 = defaultdict(list)
        for k, rows in self._keyed(self.rows('ehr_athena_clinicalencounterdxicd10', stream='encounters'), 'clinicalencounterdxicd10id', 'encounters').items():
            r = rows[-1]
            dx10[(k[0], _c(r.get('clinicalencounterdxid')))].append((_num(r.get('ordering')) or 0, _c(r.get('icdcodeid'))))
        dx = defaultdict(list)
        for k, rows in self._keyed(self.rows('ehr_athena_clinicalencounterdiagnosis', stream='encounters'), 'clinicalencounterdxid', 'encounters').items():
            r = rows[-1]
            dx[(k[0], _c(r.get('clinicalencounterid')))].append((_num(r.get('ordering')) or 0, k[1], _c(r.get('snomedcode'))))
        cands = defaultdict(list)
        for (ctx, eid), rows in self._keyed(self.rows('ehr_athena_clinicalencounter', stream='encounters'), 'clinicalencounterid', 'encounters').items():
            diags = []
            for _, dxid, snomed in sorted(dx.get((ctx, eid), [])):
                for _, icid in sorted(dx10.get((ctx, dxid), [])):
                    code = icd.get((ctx, icid))
                    if code and code not in [d['icd10'] for d in diags]:
                        diags.append({'rank': len(diags) + 1, 'icd10': code, 'type': 'primary' if not diags else 'secondary',
                                      'snomed': snomed, 'description': icd_desc.get((ctx, icid))})
            for r in rows:
                etype = {'VISIT': 'office', 'ORDERSONLY': 'orders_only'}.get(_c(r.get('clinicalencountertype')) or '')
                if not etype:
                    self.unmap('athena.clinicalencountertype', r.get('clinicalencountertype'))
                st = _c(r.get('encounterstatus'))
                start = _iso(r.get('createddatetime')) if _day(r.get('createddatetime')) == _day(r.get('encounterdate')) else None
                rec, mrn, org = self._athena_base('encounters', r, 'ehr_athena_clinicalencounter', ctx, pid=_c(r.get('patientid')),
                                                  dept=r.get('departmentid'), enc=eid, event=start or r.get('encounterdate'),
                                                  recorded=r.get('lastmodified') or r.get('createddatetime'))
                rec.update({'type': etype or _snake(r.get('clinicalencountertype')),
                            'status': {'CLOSED': 'completed', 'OPEN': 'in_progress'}.get(st or '') or _snake(st),
                            'start_time': start or _day(r.get('encounterdate')),
                            'end_time': _iso(r.get('closeddatetime')) if st == 'CLOSED' else None,
                            'attending_clinician_id': self._athena_clin(ctx, provider=r.get('providerid')),
                            'appointment_id': f'athena:{ctx}:{_c(r.get("appointmentid"))}' if _c(r.get('appointmentid')) else None,
                            'place_of_service': '11' if etype == 'office' else None, 'diagnoses': diags})
                cands[(ctx, eid)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions('encounters', cands), migration=lambda r: (r['event_date'], r.get('type') != 'virtual'))

    def _athena_problems(self):
        cands = defaultdict(list)
        covered = set()
        for (ctx, ppid), rows in self._keyed(self.rows('ehr_athena_patientproblem', stream=PROBLEM_LIST_STREAM), 'patientproblemid', PROBLEM_LIST_STREAM).items():
            for r in rows:
                pid = _c(r.get('patientid'))
                if not (pid and pid.isdigit()) or r.get('flat_row_issue'):
                    self.drop('malformed_row', PROBLEM_LIST_STREAM)
                    continue
                code, snomed = _icd(r.get('diagnosiscode')), _c(r.get('snomedcode'))
                covered.add((ctx, _c(r.get('chartid')), snomed))
                rec, mrn, org = self._athena_base(PROBLEM_LIST_STREAM, r, 'ehr_athena_patientproblem', ctx, pid=pid,
                                                  event=r.get('onsetdate') or r.get('createddatetime'), recorded=r.get('createddatetime'))
                st = (_c(r.get('status')) or '').lower() or None
                rec.update(_condition_fields(_c(r.get('problemlistname')), code, snomed, st, _day(r.get('onsetdate')),
                                             _day(r.get('deactivateddatetime')) if st == 'resolved' else None, None))
                rec['source'] = (_c(r.get('source')) or '').lower() or None
                cands[(ctx, ppid)].append((_order(r), rec, r['_variant'] == 'resend'))
        for (ctx, spid), rows in self._keyed(self.rows('ehr_athena_patientsnomedproblem', stream=PROBLEM_LIST_STREAM), 'patientsnomedproblemid', PROBLEM_LIST_STREAM).items():
            r = rows[-1]
            if (ctx, _c(r.get('chartid')), _c(r.get('snomedcode'))) in covered:
                self.drop('athena_snomed_problem_duplicate', PROBLEM_LIST_STREAM, len(rows))
                continue
            for r in rows:
                rec, mrn, org = self._athena_base(PROBLEM_LIST_STREAM, r, 'ehr_athena_patientsnomedproblem', ctx, chart=_c(r.get('chartid')),
                                                  event=r.get('startdatedatetime') or r.get('entereddatetime'), recorded=r.get('entereddatetime'))
                st = (_c(r.get('status')) or '').lower() or None
                rec.update(_condition_fields(_c(r.get('problemnote')), None, _c(r.get('snomedcode')), st,
                                             _day(r.get('startdatedatetime')), _day(r.get('enddatedatetime')), None))
                cands[(ctx, 'snomed', spid)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions(PROBLEM_LIST_STREAM, cands))

    def _athena_labs(self):
        results = {}
        for k, rows in self._keyed(self.rows('ehr_athena_clinicalresult', count=False), 'clinicalresultid', 'labs').items():
            results[k] = rows[-1]
        docs = self.lookup('ehr_athena_document', 'documentid')
        loinc_ids = {k: _c(v.get('loinccode')) for k, v in self.lookup('ehr_athena_loinc', 'loincid').items()}
        labs, phq = defaultdict(list), defaultdict(list)
        for (ctx, oid), rows in self._keyed(self.rows('ehr_athena_clinicalresultobservation', stream='labs'), 'clinicalobservationid', 'labs').items():
            for r in rows:
                res = results.get((ctx, _c(r.get('clinicalresultid'))))
                doc = docs.get((ctx, _c((res or {}).get('documentid')))) or {}
                pid = _c(doc.get('patientid'))
                if not pid:
                    self.drop('lab_without_patient_link', 'labs')
                    continue
                loinc = _c(r.get('observationidentifierloinc')) or loinc_ids.get((ctx, _c(r.get('loincid'))))
                name = _c(r.get('observationidentifiertext')) or _c(r.get('templateanalytename'))
                spec = _iso(doc.get('specimencollecteddatetime')) or _iso((res or {}).get('observationdatetime')) or _iso(r.get('observationtimestampdatetime'))
                instrument = _phq(name or r.get('observationidentifier'), loinc)
                if instrument:
                    rec, mrn, org = self._athena_base('mood_questionnaires', r, 'ehr_athena_clinicalresultobservation', ctx, pid=pid,
                                                      dept=doc.get('departmentid'), event=spec)
                    rec.update({'instrument': instrument, 'total': _num(r.get('result')), 'interpretation': _c(r.get('observationnote')),
                                'result_status': LAB_STATUS.get(_c(r.get('resultstatus')) or 'F', 'final')})
                    phq[(ctx, oid)].append((_order(r), rec, r['_variant'] == 'resend'))
                    continue
                value = _value(r.get('result'))
                rr = _c(r.get('referencerange'))
                st = _c(r.get('resultstatus'))
                flag = _hl7_flag(r.get('abnormaltestnature') or r.get('plcobservationabnormalflagid')) or _flag(value, rr)
                rec, mrn, org = self._athena_base('labs', r, 'ehr_athena_clinicalresultobservation', ctx, pid=pid, dept=doc.get('departmentid'),
                                                  enc=_c(doc.get('clinicalencounterid')), event=spec,
                                                  recorded=(res or {}).get('resultsreporteddatetime') or r.get('createddatetime'))
                rec.update(_lab_fields(name, loinc, _c(r.get('observationidentifier')), value, _c(r.get('observationunits')), rr, flag,
                                       LAB_STATUS.get(st or 'F') or _snake(st), spec, _iso((res or {}).get('resultsreporteddatetime')),
                                       _c((res or {}).get('externalaccessionidentifier')) or f'athena:{ctx}:{_c(r.get("clinicalresultid"))}',
                                       _c(doc.get('clinicalordertype'))))
                labs[(ctx, oid)].append((_order(r), rec, r['_variant'] == 'resend'))
                self._presence(org, mrn, rec)
        yield from self.emit(self.versions('labs', labs), migration=_lab_migration_key)
        yield from self.emit(self.versions('mood_questionnaires', phq))

    def _athena_vitals(self):
        encs = {}
        for (ctx, eid), r in self.lookup('ehr_athena_clinicalencounter', 'clinicalencounterid').items():
            encs[(ctx, eid)] = r
        groups = defaultdict(dict)
        for (ctx, did), rows in self._keyed(self.rows('ehr_athena_vitalsign', stream='vitals'), 'encounterdataid', 'vitals').items():
            r = rows[-1]
            if len(rows) > 1:
                self.drop('repeated_identical_copy', 'vitals', len(rows) - 1)
            groups[(ctx, _c(r.get('clinicalencounterid')))][did] = r
        cands = defaultdict(list)
        keymap = {'VITALS.BLOODPRESSURE.SYSTOLIC': 'systolic', 'VITALS.BLOODPRESSURE.DIASTOLIC': 'diastolic',
                  'VITALS.PULSE.RATE': 'pulse', 'VITALS.WEIGHT': 'weight', 'VITALS.HEIGHT': 'height', 'VITALS.BMI': 'bmi',
                  'VITALS.TEMPERATURE': 'temperature', 'VITALS.RESPIRATIONRATE': 'resp', 'VITALS.O2SATURATION': 'spo2'}
        for (ctx, eid), items in groups.items():
            e = encs.get((ctx, eid))
            if not e or not _c(e.get('patientid')):
                self.drop('vital_without_encounter_link', 'vitals', len(items))
                continue
            values, times, lin = {}, [], []
            for did, r in items.items():
                kind = keymap.get(_c(r.get('key')) or '')
                cv = _vital(kind, r.get('value'), r.get('dbunit')) if kind else None
                if not cv and kind:
                    cv = _vital(kind, r.get('displayvalue'), r.get('displayunit'))
                if not cv:
                    self.unmap('athena.vitalsign.key', r.get('key'))
                    continue
                values[cv[0]] = cv[1]
                if _iso(r.get('createddatetime')):
                    times.append(_iso(r.get('createddatetime')))
                lin.append(_lin('ehr_athena_vitalsign', r))
            if not values:
                continue
            first = min(items.values(), key=_order)
            rec, mrn, org = self._athena_base('vitals', first, 'ehr_athena_vitalsign', ctx, pid=_c(e.get('patientid')),
                                              dept=e.get('departmentid'), enc=eid, event=min(times) if times else e.get('encounterdate'))
            rec['_lineage'] = lin
            rec.update(values)
            rec['units'] = {k: VITAL_UNITS[k] for k in values}
            cands[(ctx, eid)].append((_order(first), rec, False))
        yield from self.emit(self.versions('vitals', cands))

    def _athena_meds(self):
        docs = self.lookup('ehr_athena_document', 'documentid')
        meds = self.lookup('ehr_athena_medication', 'medicationid')
        orders = defaultdict(list)
        for (ctx, cpid), rows in self._keyed(self.rows('ehr_athena_clinicalprescription', stream='medication_orders'), 'clinicalprescriptionid', 'medication_orders').items():
            for r in rows:
                doc = docs.get((ctx, _c(r.get('documentid')))) or {}
                pid = _c(doc.get('patientid'))
                if not pid:
                    self.drop('prescription_without_patient_link', 'medication_orders')
                    continue
                written = r.get('writtendatedatetime') or doc.get('orderdatetime')
                rec, mrn, org = self._athena_base('medication_orders', r, 'ehr_athena_clinicalprescription', ctx, pid=pid,
                                                  dept=doc.get('departmentid'), enc=_c(doc.get('clinicalencounterid')), event=written, recorded=written)
                sigtext = _c(doc.get('ordertext'))
                st = _c(doc.get('status'))
                rec.update({'medication_name': _c(r.get('labelname')) or _c(doc.get('clinicalordertype')), 'ndc': _c(r.get('ndc')),
                            'prescription_id': f'athena:{ctx}:{cpid}', 'status': 'completed' if st == 'CLOSED' else 'active',
                            'order_status': (st or '').lower() or None, 'order_class': 'e-prescribe', 'sig': sigtext,
                            'directions_text': sigtext, 'route': (_c(r.get('dosageroute')) or '').lower() or None,
                            'frequency': _frequency(sigtext), 'strength': _c(r.get('dosagestrength')),
                            'quantity': _num(r.get('dosagequantity')), 'days_supply': _num(r.get('dayssupply')),
                            'start_time': _iso(written), 'last_fill_date': _day(r.get('lastfilldatedatetime')),
                            'ordering_clinician_id': self._athena_clin(ctx, clinical_provider=doc.get('clinicalproviderid'))})
                orders[(ctx, cpid)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions('medication_orders', orders))
        lists = defaultdict(list)
        for (ctx, pmid), rows in self._keyed(self.rows('ehr_athena_patientmedication', stream='medication_lists'), 'patientmedicationid', 'medication_lists').items():
            for r in rows:
                m = meds.get((ctx, _c(r.get('medicationid')))) or {}
                doc = docs.get((ctx, _c(r.get('documentid')))) or {}
                pid = _c(doc.get('patientid'))
                rec, mrn, org = self._athena_base('medication_lists', r, 'ehr_athena_patientmedication', ctx, pid=pid,
                                                  chart=None if pid else _c(r.get('chartid')), dept=doc.get('departmentid'),
                                                  event=r.get('filldate') or doc.get('orderdatetime') or doc.get('createddatetime'))
                if not rec['event_date']:
                    rec['event_date'], rec['event_date_precision'] = rec['available_date'], 'unknown'
                mt = _c(r.get('medicationtype'))
                rec.update({'name': _c(m.get('medicationname')) or _c(doc.get('clinicalordertype')), 'rxcui': _c(m.get('rxnorm')),
                            'ndc': _c(m.get('ndc')), 'list_status': 'active', 'route': (_c(r.get('dosageroute')) or '').lower() or None,
                            'dose': _c(r.get('dosagestrength')), 'source': {'CLINICALPRESCRIPTION': 'prescribed', 'ENTERED': 'patient_reported'}.get(mt or '') or _snake(mt),
                            'medication_list_id': f'athena:{ctx}:{pmid}'})
                if not rec.get('name'):
                    self.unmap('athena.patientmedication_without_name', pmid)
                lists[(ctx, pmid)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions('medication_lists', lists))

    def _athena_documents(self):
        notes, imms = defaultdict(list), defaultdict(list)
        for (ctx, did), rows in self._keyed(self.rows('ehr_athena_document', stream='notes'), 'documentid', 'notes').items():
            for r in rows:
                cls = _c(r.get('documentclass'))
                pid = _c(r.get('patientid'))
                if cls in ('ENCOUNTERDOCUMENT', 'CLINICALDOCUMENT') and _c(r.get('documenttextdata')):
                    signed = r.get('approveddatetime') or r.get('createddatetime')
                    rec, mrn, org = self._athena_base('notes', r, 'ehr_athena_document', ctx, pid=pid, dept=r.get('departmentid'),
                                                      enc=_c(r.get('clinicalencounterid')), event=r.get('createddatetime'),
                                                      recorded=r.get('createddatetime'), signed=signed)
                    text = r.get('documenttextdata')
                    m = re.search(r'Electronically signed by ([^,\n]+), ([A-Z]{1,4}) on', text or '')
                    rec.update({'note_id': f'athena:{ctx}:{did}', 'note_type': _snake(r.get('documentsubclass')) or _snake(cls),
                                'note_status': 'signed' if _c(r.get('status')) == 'CLOSED' else (_c(r.get('status')) or '').lower() or None,
                                'author_id': self._athena_clin(ctx, clinical_provider=r.get('clinicalproviderid')) if _c(r.get('clinicalproviderid')) else None,
                                'author_name': m[1] if m else None, 'author_credential': m[2] if m else None,
                                'signed_date': _day(signed), 'authored_time': _iso(r.get('createddatetime')), 'text': text})
                    notes[(ctx, did)].append((_order(r), rec, r['_variant'] == 'resend'))
                elif _c(r.get('cvx')) and (_c(r.get('clinicalordertypegroup')) == 'VACCINE' or cls == 'ORDER'):
                    when = r.get('observationdatetime') or r.get('orderdatetime')
                    rec, mrn, org = self._athena_base('immunizations', r, 'ehr_athena_document', ctx, pid=pid, dept=r.get('departmentid'),
                                                      enc=_c(r.get('clinicalencounterid')), event=when, recorded=r.get('createddatetime'))
                    hist = _c(r.get('historicalyn')) == 'Y'
                    rec.update({'vaccine': (_c(r.get('clinicalordertype')) or '').lower() or None, 'cvx_code': _c(r.get('cvx')),
                                'administration_status': 'historical' if hist else 'completed',
                                'route': (_c(r.get('vaccineroute')) or '').lower() or None})
                    imms[(ctx, did)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions('notes', notes))
        yield from self.emit(self.versions('immunizations', imms))

    # ================================================================================================= eCW
    def ecw(self):
        if not self.has('ehr_ecw_patients'):
            return
        pats = {k: _c(v.get('hl7id')) for k, v in self.lookup('ehr_ecw_patients', 'pid').items()}
        docs = {k: _c(v.get('npi')) for k, v in self.lookup('ehr_ecw_doctors', 'doctorid').items()}
        items = self.lookup('ehr_ecw_items', 'itemid')
        icd_items = {k: _icd(v.get('value')) for k, v in self.lookup('ehr_ecw_itemdetail', 'itemid').items() if _c(v.get('propid')) == '13'}
        icd_desc = {k: _c(v.get('shortdesc')) for k, v in self.lookup('ehr_ecw_icd', 'code').items()}
        loinc = {k: _c(v.get('code')) for k, v in self.lookup('ehr_ecw_labloinccodes', 'itemid').items()}
        enc_rows = defaultdict(list)
        for r in self.rows('ehr_ecw_enc', stream='encounters'):
            eid = _c(r.get('encounterid'))
            if not (eid and eid.isdigit() and (_c(r.get('patientid')) or '').isdigit() and _iso(r.get('date'))):
                self.drop('malformed_row', 'encounters')
                continue
            enc_rows[(_inst(r), eid)].append(r)
        enc_pat = {k: _c(max(v, key=_order).get('patientid')) for k, v in enc_rows.items()}
        self.W = {'pats': pats, 'docs': docs, 'items': items, 'icd': icd_items, 'icd_desc': icd_desc, 'loinc': loinc,
                  'enc_pat': enc_pat, 'enc_rows': enc_rows}
        yield from self._ecw_encounters()
        yield from self._ecw_problems()
        yield from self._ecw_labs()
        yield from self._ecw_vitals()
        yield from self._ecw_meds()
        yield from self._ecw_immunizations()

    def _ecw_base(self, stream, r, table, practice, pid, enc=None, **kw):
        mrn = self.W['pats'].get((practice, pid))
        keys = [ecw_key(practice, pid), mrn_key(mrn, practice) if mrn else None]
        rec = self.base(stream, r, table, vendor='ecw', instance=practice, org=practice, keys=keys, source_patient_id=mrn or pid,
                        encounter_id=f'ecw:{practice}:{enc}' if enc else None, **kw)
        return rec, mrn, practice

    def _ecw_clin(self, practice, doc):
        doc = _c(doc)
        npi = self.W['docs'].get((practice, doc)) if doc else None
        return npi if npi and re.fullmatch(r'\d{10}', npi) else (f'ecw:{practice}:{doc}' if doc and doc.isdigit() and doc != '0' else None)

    def _ecw_encounters(self):
        dx = defaultdict(list)
        for r in self.rows('ehr_ecw_diagnosis', stream='encounters'):
            inst, eid = _inst(r), _c(r.get('encounterid'))
            code = self.W['icd'].get((inst, _c(r.get('itemid'))))
            if not code:
                self.unmap('ecw.diagnosis_item_without_icd', r.get('itemid'))
                continue
            dx[(inst, eid)].append((0 if _c(r.get('primaryasmt')) == '1' else 1, _num(r.get('displayindex')) or 0, code))
        encs, appts = defaultdict(list), defaultdict(list)
        for (practice, eid), rows in self.W['enc_rows'].items():
            rows.sort(key=_order)
            slot = any(_c(r.get('status')) in ('PEN', 'ARR', 'CANC', 'N/S') or _c(r.get('deleteflag')) == '1' for r in rows)
            vt = _c(rows[-1].get('visittype'))
            if slot:
                for r in rows:
                    st = _c(r.get('status'))
                    status = ECW_APPT.get(st or '')
                    if not status:
                        self.unmap('ecw.enc.status', st)
                    rec, mrn, org = self._ecw_base('appointments', r, 'ehr_ecw_enc', practice, _c(r.get('patientid')), event=None)
                    rec['event_date'], rec['event_date_precision'] = rec['available_date'], 'day'
                    rec.update({'appointment_id': f'ecw:{practice}:{eid}', 'scheduled_date': _day(r.get('date')),
                                'scheduled_time': _iso(r.get('starttime')), 'scheduled_end_time': _iso(r.get('endtime')),
                                'status': status or _snake(st), 'purpose': ECW_PURPOSE.get(_c(r.get('visittype')) or ''),
                                'visit_type': ECW_VISIT.get(_c(r.get('visittype')) or ''), 'clinician_id': self._ecw_clin(practice, r.get('doctorid')),
                                'reason': _c(r.get('reason')), 'modality': 'telephone' if _c(r.get('visittype')) == 'TEL' else None})
                    appts[(practice, eid)].append((_order(r), rec, r['_variant'] == 'resend'))
                continue
            if vt == 'LAB':
                self.drop('ecw_lab_collection_not_a_visit', 'encounters', len(rows))
                continue
            diags = [{'rank': i + 1, 'icd10': c, 'type': 'primary' if i == 0 else 'secondary', 'description': self.W['icd_desc'].get((practice, c))}
                     for i, (_, _, c) in enumerate(sorted(set(dx.get((practice, eid), []))))]
            for r in rows:
                st = _c(r.get('status'))
                etype = ECW_VISIT.get(_c(r.get('visittype')) or '')
                if not etype:
                    self.unmap('ecw.enc.visittype', r.get('visittype'))
                rec, mrn, org = self._ecw_base('encounters', r, 'ehr_ecw_enc', practice, _c(r.get('patientid')), enc=eid,
                                               event=r.get('starttime') or r.get('date'), recorded=r.get('date'))
                rec.update({'type': etype or _snake(r.get('visittype')), 'status': 'completed' if st == 'CHK' else 'in_progress',
                            'start_time': _iso(r.get('starttime')) or _day(r.get('date')), 'end_time': _iso(r.get('endtime')),
                            'attending_clinician_id': self._ecw_clin(practice, r.get('doctorid')), 'chief_complaint': _c(r.get('reason')),
                            'place_of_service': '11' if etype in ('office', 'wellness', 'pediatric_office', 'nurse_visit') else None,
                            'diagnoses': diags})
                encs[(practice, eid)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions('encounters', encs), migration=lambda r: (r['event_date'], r.get('type') not in ('telephone', 'virtual')))
        yield from self.emit(self.versions('appointments', appts, chain=False))

    def _ecw_problems(self):
        g = defaultdict(list)
        for r in self.rows('ehr_ecw_problemlist', stream=PROBLEM_LIST_STREAM):
            pid, asmt = _c(r.get('patientid')), _c(r.get('asmtid'))
            if not (pid and pid.isdigit() and asmt and asmt.isdigit()):
                self.drop('malformed_row', PROBLEM_LIST_STREAM)
                continue
            g[(_inst(r), pid, asmt)].append(r)
        cands = defaultdict(list)
        for (practice, pid, asmt), rows in g.items():
            rows.sort(key=_order)
            if _c(rows[-1].get('deleteflag')) == '1':
                self.drop('retired_by_source', PROBLEM_LIST_STREAM, len(rows))
                continue
            code = self.W['icd'].get((practice, asmt))
            desc = self.W['icd_desc'].get((practice, code)) or _c((self.W['items'].get((practice, asmt)) or {}).get('itemname'))
            for r in rows:
                status = 'resolved' if _c(r.get('resolved')) == '1' else 'inactive' if _c(r.get('inactiveflag')) == '1' else 'active'
                rec, mrn, org = self._ecw_base(PROBLEM_LIST_STREAM, r, 'ehr_ecw_problemlist', practice, pid,
                                               event=r.get('onsetdate') or r.get('addeddate'), recorded=r.get('addeddate'))
                if _c(r.get('encounterid')):
                    rec['encounter_id'] = f'ecw:{practice}:{_c(r.get("encounterid"))}'
                rec.update(_condition_fields(desc if desc != code else None, code, None, status, _day(r.get('onsetdate')),
                                             _day(r.get('resolvedon')), None))
                cands[(practice, pid, asmt)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions(PROBLEM_LIST_STREAM, cands))

    def _ecw_labs(self):
        detail = defaultdict(list)
        for r in self.rows('ehr_ecw_labdatadetail', stream='labs'):
            detail[(_inst(r), _c(r.get('reportid')))].append(r)
        hl7d = defaultdict(list)
        for r in self.rows('ehr_ecw_hl7labdatadetail', stream='labs'):
            hl7d[(_inst(r), _c(r.get('reportid')))].append(r)
        heads = defaultdict(list)
        for r in self.rows('ehr_ecw_labdata', stream='labs'):
            rid = _c(r.get('reportid'))
            if not (rid and rid.isdigit()):
                self.drop('malformed_row', 'labs')
                continue
            heads[(_inst(r), rid)].append(r)
        # eCW sends a correction as a new report (status Corrected) for the same encounter + order item + collection time
        groups = defaultdict(list)
        for (practice, rid), rows in heads.items():
            rows.sort(key=_order)
            h = rows[-1]
            if _c(h.get('deleteflag')) == '1' or _c(h.get('cancelled')) == '1':
                self.drop('retired_by_source', 'labs', len(rows))
                continue
            groups[(practice, _c(h.get('encounterid')), _c(h.get('itemid')), _join_dt(h.get('colldate'), h.get('colltime')))].append((rid, rows))
        cands = defaultdict(list)
        for (practice, eid, item, coll), reports in groups.items():
            reports.sort(key=lambda x: (0 if _c(x[1][-1].get('status')) == 'Final' else 1, _order(x[1][-1])))
            for rid, rows in reports:
                pid = self.W['enc_pat'].get((practice, eid))
                if not pid:
                    self.drop('lab_without_patient_link', 'labs', len(rows))
                    continue
                h = rows[-1]
                status = {'Final': 'final', 'Corrected': 'corrected'}.get(_c(h.get('status')) or '')
                if not status:
                    self.unmap('ecw.labdata.status', h.get('status'))
                resulted = _join_dt(h.get('resultdate'), h.get('resultime'))
                panel = _c((self.W['items'].get((practice, item)) or {}).get('itemname'))
                comps = {}
                for d in sorted(detail.get((practice, rid), []), key=_order):
                    comps[_c(d.get('itemid')) or _c(d.get('propid'))] = ('detail', d)
                for d in sorted(hl7d.get((practice, rid), []), key=_order):
                    comps[_c(d.get('hl7itemid')) or _c(d.get('name'))] = ('hl7', d)
                if not comps:
                    self.drop('lab_report_without_results', 'labs')
                for comp_id, (kind, d) in comps.items():
                    lo = self.W['loinc'].get((practice, comp_id))
                    lo = re.sub(r'[^0-9-]', '', lo) if lo else None
                    if kind == 'hl7':
                        name, unit, rr = _c(d.get('name')), _c(d.get('units')), _c(d.get('range'))
                    else:
                        name = _c((self.W['items'].get((practice, comp_id)) or {}).get('itemname'))
                        unit, rr = None, None
                    inferred = None
                    if not unit and lo in LOINC_UNITS:
                        unit, inferred = LOINC_UNITS[lo], 'loinc_default'
                    elif not unit and _name_unit(name):
                        unit, inferred = _name_unit(name), 'name_default'
                    value = _value(d.get('value'))
                    rec, mrn, org = self._ecw_base('labs', d, f'ehr_ecw_{"hl7labdatadetail" if kind == "hl7" else "labdatadetail"}',
                                                   practice, pid, enc=eid, event=coll or resulted, recorded=resulted)
                    rec['_lineage'].insert(0, _lin('ehr_ecw_labdata', h))
                    rec.update(_lab_fields(name, lo, comp_id, value, unit, rr, _flag(value, rr), status or 'final', coll, resulted,
                                           f'ecw:{practice}:{rid}', panel))
                    if inferred:
                        rec['unit_source'] = inferred
                    cands[(practice, eid, item, coll, comp_id)].append(((h.get('_drop') or '', 0 if status == 'final' else 1) + _order(d), rec,
                                                                        d['_variant'] == 'resend'))
                    self._presence(org, mrn, rec)
        yield from self.emit(self.versions('labs', cands), migration=_lab_migration_key)

    def _ecw_vitals(self):
        vt = {k: (_c(v.get('type')), _c(v.get('displayuom'))) for k, v in self.lookup('ehr_ecw_vitaltypes', 'itemid').items()}
        groups = defaultdict(dict)
        for r in self.rows('ehr_ecw_vitals', stream='vitals'):
            eid, vid = _c(r.get('encounterid')), _c(r.get('vitalid'))
            if not (eid and eid.isdigit() and vid):
                self.drop('malformed_row', 'vitals')
                continue
            cur = groups[(_inst(r), eid)].get(vid)
            if cur is not None:
                self.drop('resend_duplicate' if r['_variant'] == 'resend' else 'repeated_identical_copy', 'vitals')
            if cur is None or _order(r) >= _order(cur):
                groups[(_inst(r), eid)][vid] = r
        cands = defaultdict(list)
        for (practice, eid), items in groups.items():
            pid = self.W['enc_pat'].get((practice, eid))
            if not pid:
                self.drop('vital_without_encounter_link', 'vitals', len(items))
                continue
            values, times, lin = {}, [], []
            for vid, r in items.items():
                name, uom = vt.get((practice, vid), (None, None))
                kind = _vital_kind(name)
                cv = _vital(kind, r.get('value'), uom) if kind and kind != 'bp' else None
                if not cv:
                    self.unmap('ecw.vitals.vitalid', f'{vid}:{name}')
                    continue
                values[cv[0]] = cv[1]
                if _iso(r.get('updatedtime')):
                    times.append(_iso(r.get('updatedtime')))
                lin.append(_lin('ehr_ecw_vitals', r))
            if not values:
                continue
            first = min(items.values(), key=_order)
            rec, mrn, org = self._ecw_base('vitals', first, 'ehr_ecw_vitals', practice, pid, enc=eid, event=min(times) if times else None)
            rec['_lineage'] = lin
            rec.update(values)
            rec['units'] = {k: VITAL_UNITS[k] for k in values}
            cands[(practice, eid)].append((_order(first), rec, False))
        yield from self.emit(self.versions('vitals', cands))

    def _ecw_meds(self):
        cands = defaultdict(list)
        for r in self.rows('ehr_ecw_rxhub_scriptlog', stream='medication_orders'):
            sid, pid = _c(r.get('id')), _c(r.get('patientid'))
            if not (sid and sid.isdigit() and pid and pid.isdigit()):
                self.drop('malformed_row', 'medication_orders')
                continue
            practice = _inst(r)
            enc = _c(r.get('encounterid'))
            rec, mrn, org = self._ecw_base('medication_orders', r, 'ehr_ecw_rxhub_scriptlog', practice, pid, enc=enc if enc and enc.isdigit() else None,
                                           event=r.get('scriptdate'), recorded=r.get('scriptdate'))
            sigtext = _c(r.get('dosage'))
            rec.update({'medication_name': _c(r.get('drugname')), 'ndc': _c(r.get('ndccode')), 'prescription_id': f'ecw:{practice}:{sid}',
                        'status': 'completed', 'order_status': 'sent', 'order_class': 'e-prescribe', 'sig': sigtext, 'directions_text': sigtext,
                        'route': (_c(r.get('route')) or '').lower() or None, 'frequency': _frequency(sigtext), 'quantity': _num(r.get('quantity')),
                        'days_supply': _num(r.get('duration')), 'start_time': _day(r.get('scriptdate')),
                        'ordering_clinician_id': self._ecw_clin(practice, r.get('doctorid'))})
            cands[(practice, sid)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions('medication_orders', cands))

    def _ecw_immunizations(self):
        g = defaultdict(list)
        for r in self.rows('ehr_ecw_immunizations', stream='immunizations'):
            iid, pid = _c(r.get('immunizationid')), _c(r.get('patientid'))
            if not (iid and iid.isdigit() and pid and pid.isdigit()):
                self.drop('malformed_row', 'immunizations')
                continue
            g[(_inst(r), iid)].append(r)
        cands = defaultdict(list)
        for (practice, iid), rows in g.items():
            rows.sort(key=_order)
            if _c(rows[-1].get('deleteflag')) == '1':
                self.drop('retired_by_source', 'immunizations', len(rows))
                continue
            for r in rows:
                enc = _c(r.get('encounterid'))
                rec, mrn, org = self._ecw_base('immunizations', r, 'ehr_ecw_immunizations', practice, _c(r.get('patientid')),
                                               enc=enc if enc and enc.isdigit() else None, event=r.get('givendate'))
                rec.update({'vaccine': (_c(r.get('vaccinename')) or '').lower() or None, 'cvx_code': _c(r.get('cvx_code')),
                            'administration_status': 'completed' if _c(r.get('immstatus')) in (None, '1') else _snake(r.get('immstatus')),
                            'lot_number': _c(r.get('lotnumber')), 'dose_number': _num(r.get('dosenumber')), 'dose': _num(r.get('dose')),
                            'dose_unit': 'mL' if _num(r.get('dose')) is not None else None, 'route': (_c(r.get('route')) or '').lower() or None,
                            'administered_by_clinician_id': self._ecw_clin(practice, r.get('givenby'))})
                cands[(practice, iid)].append((_order(r), rec, r['_variant'] == 'resend'))
        yield from self.emit(self.versions('immunizations', cands))

    # ================================================================================================= FHIR
    def fhir(self):
        if not any(t.startswith('fhir_') for t in self.tables):
            self.missing.add('fhir_*')
            return
        pats = {}
        for r in self.rows('fhir_patient', count=False):
            res = _res(r)
            folder = _inst(r)
            mrn = next((i.get('value') for i in res.get('identifier') or [] if isinstance(i, dict)
                        and any(c.get('code') == 'MR' for c in ((i.get('type') or {}).get('coding') or []))), None)
            k = (folder, res.get('id'))
            o = (str((res.get('meta') or {}).get('lastUpdated') or ''),) + _order(r)
            if mrn and (k not in pats or o >= pats[k][0]):
                pats[k] = (o, mrn)
        self.F = {k: v[1] for k, v in pats.items()}
        for rt, fn in (('encounter', self._fhir_encounter), ('condition', self._fhir_condition), ('observation', self._fhir_observation),
                       ('medicationrequest', self._fhir_medreq), ('documentreference', self._fhir_docref), ('appointment', self._fhir_appt),
                       ('allergyintolerance', self._fhir_allergy), ('immunization', self._fhir_imm)):
            table = f'fhir_{rt}'
            if table not in self.tables:
                self.missing.add(table)
                continue
            stream_of = {'encounter': 'encounters', 'condition': PROBLEM_LIST_STREAM, 'observation': 'labs', 'medicationrequest': 'medication_orders',
                         'documentreference': 'notes', 'appointment': 'appointments', 'allergyintolerance': 'allergies', 'immunization': 'immunizations'}[rt]
            by_stream, vitals = defaultdict(lambda: defaultdict(list)), defaultdict(list)
            for r in self.rows(table, stream=stream_of):
                folder = _inst(r)
                res = _res(r)
                if not res:
                    self.drop('malformed_row', stream_of)
                    continue
                native = folder in FHIR_NATIVE_PRACTICES
                if not native:
                    if rt == 'observation':
                        self._fhir_presence(folder, res)
                    self.drop('fhir_copy_of_vendor_practice' if folder != 'DEEPCHART' else 'fhir_copy_of_epic_deepchart', stream_of)
                    continue
                pid = _ref(res.get('subject') or res.get('patient') or next(
                    (p.get('actor') for p in res.get('participant') or [] if str((p.get('actor') or {}).get('reference', '')).startswith('Patient/')), None))
                mrn = self.F.get((folder, pid))
                keys = [fhir_key(folder, pid) if pid else None, mrn_key(mrn, folder) if mrn else None]
                out = fn(r, res, folder, pid, mrn, keys)
                if not out:
                    continue
                stream, rec, extra = out
                o = (str((res.get('meta') or {}).get('lastUpdated') or ''), FHIR_ENC_ORDER.get(res.get('status'), 2) if rt == 'encounter' else 0) + _order(r)
                if stream == 'vitals':
                    vitals[(folder, pid, extra)].append((o, res.get('id'), rec, r))
                    continue
                by_stream[stream][(folder, rt, res.get('id'))].append((o, rec, True))  # copies are compared to every version
            for stream, cands in by_stream.items():
                yield from self.emit(self.versions(stream, cands, chain=stream != 'appointments'),
                                     migration=_lab_migration_key if stream == 'labs' else None)
            if vitals:
                yield from self._fhir_vitals(vitals)

    def _fhir_base(self, stream, r, res, folder, pid, mrn, keys, table, event=None, recorded=None, encounter=None, signed=None):
        return self.base(stream, r, table, vendor='fhir', instance=folder, org=folder, keys=keys, source_patient_id=mrn or pid,
                         encounter_id=f'fhir:{folder}:{encounter}' if encounter else None, event=event, recorded=recorded, signed=signed)

    def _fhir_encounter(self, r, res, folder, pid, mrn, keys):
        start = (res.get('period') or {}).get('start')
        rec = self._fhir_base('encounters', r, res, folder, pid, mrn, keys, 'fhir_encounter', event=start,
                              recorded=(res.get('meta') or {}).get('lastUpdated'), encounter=res.get('id'))
        cls = (res.get('class') or {}).get('code')
        etype = {'AMB': 'office', 'VR': 'virtual', 'EMER': 'emergency', 'IMP': 'inpatient', 'HH': 'home_health', 'OBSENC': 'observation'}.get(cls)
        if not etype:
            self.unmap('fhir.encounter.class', cls)
        diags = []
        for rc in res.get('reasonCode') or []:
            for cd in (rc or {}).get('coding') or []:
                if 'icd-10' in str(cd.get('system')) and cd.get('code') and cd['code'] not in [d['icd10'] for d in diags]:
                    diags.append({'rank': len(diags) + 1, 'icd10': _icd(cd['code']), 'type': 'primary' if not diags else 'secondary'})
        st = res.get('status')
        rec.update({'type': etype or _snake(cls), 'status': FHIR_ENC_STATUS.get(st) or _snake(st), 'start_time': _iso(start),
                    'end_time': _iso((res.get('period') or {}).get('end')),
                    'place_of_service': '02' if etype == 'virtual' else '11' if etype == 'office' else None, 'diagnoses': diags})
        return 'encounters', rec, None

    def _fhir_condition(self, r, res, folder, pid, mrn, keys):
        codes = ((res.get('code') or {}).get('coding') or [])
        icd = next((_icd(c.get('code')) for c in codes if 'icd-10' in str(c.get('system'))), None)
        snomed = next((c.get('code') for c in codes if 'snomed' in str(c.get('system'))), None)
        desc = (res.get('code') or {}).get('text') or next((c.get('display') for c in codes if c.get('display')), None)
        cat = ' '.join(c.get('code', '') for cc in res.get('category') or [] for c in (cc or {}).get('coding') or [])
        status = next((c.get('code') for c in ((res.get('clinicalStatus') or {}).get('coding') or [])), None)
        onset = res.get('onsetDateTime')
        rec = self._fhir_base(PROBLEM_LIST_STREAM, r, res, folder, pid, mrn, keys, 'fhir_condition', event=onset or res.get('recordedDate'),
                              recorded=res.get('recordedDate'), encounter=_ref(res.get('encounter')))
        rec.update(_condition_fields(desc, icd, snomed, status, _day(onset), _day(res.get('abatementDateTime')),
                                     None if 'problem-list' in cat else 'clinical_encounter'))
        return PROBLEM_LIST_STREAM, rec, None

    def _fhir_observation(self, r, res, folder, pid, mrn, keys):
        cats = {c.get('code') for cc in res.get('category') or [] for c in (cc or {}).get('coding') or []}
        codes = (res.get('code') or {}).get('coding') or []
        loinc = next((c.get('code') for c in codes if 'loinc' in str(c.get('system'))), None)
        eff = res.get('effectiveDateTime') or (res.get('effectivePeriod') or {}).get('start')
        enc = _ref(res.get('encounter'))
        if 'vital-signs' in cats:
            values = {}
            parts = [(loinc, res.get('valueQuantity'))] + [
                (next((c.get('code') for c in ((cp.get('code') or {}).get('coding') or []) if 'loinc' in str(c.get('system'))), None), cp.get('valueQuantity'))
                for cp in res.get('component') or []]
            for code, q in parts:
                kind = FHIR_VITAL_LOINC.get(code)
                if not kind or not isinstance(q, dict):
                    continue
                cv = _vital(kind, q.get('value'), q.get('unit') or q.get('code'))
                if cv:
                    values[cv[0]] = cv[1]
            if not values:
                if loinc != '85354-9':
                    self.unmap('fhir.vital_loinc', loinc)
                return None
            rec = self._fhir_base('vitals', r, res, folder, pid, mrn, keys, 'fhir_observation', event=eff, encounter=enc)
            rec.update(values)
            return 'vitals', rec, enc or _minute(eff)
        name = (res.get('code') or {}).get('text') or next((c.get('display') for c in codes if c.get('display')), None)
        instrument = _phq(name, loinc)
        q = res.get('valueQuantity') or {}
        value = q.get('value') if q else _value(res.get('valueString'))
        status = res.get('status')
        if instrument:
            rec = self._fhir_base('mood_questionnaires', r, res, folder, pid, mrn, keys, 'fhir_observation', event=eff, encounter=enc)
            rec.update({'instrument': instrument, 'total': value if isinstance(value, (int, float)) else None, 'result_status': status})
            return 'mood_questionnaires', rec, None
        rr = None
        for ref in res.get('referenceRange') or []:
            lo, hi = (ref.get('low') or {}).get('value'), (ref.get('high') or {}).get('value')
            rr = f'{lo}-{hi}' if lo is not None and hi is not None else (ref.get('text') or rr)
        interp = next((c.get('code') for cc in res.get('interpretation') or [] for c in (cc or {}).get('coding') or []), None)
        rec = self._fhir_base('labs', r, res, folder, pid, mrn, keys, 'fhir_observation', event=eff,
                              recorded=res.get('issued') or (res.get('meta') or {}).get('lastUpdated'), encounter=enc)
        rstatus = {'final': 'final', 'corrected': 'corrected', 'amended': 'corrected', 'cancelled': 'cancelled', 'preliminary': 'preliminary',
                   'entered-in-error': 'cancelled'}.get(status) or _snake(status)
        rec.update(_lab_fields(name, loinc, None, None if rstatus == 'cancelled' else value, q.get('unit'), rr,
                               _hl7_flag(interp) or _flag(value, rr), rstatus, _iso(eff), _iso(res.get('issued')), None, None))
        self._presence(folder, mrn, rec)
        return 'labs', rec, None

    def _fhir_vitals(self, groups):
        cands = defaultdict(list)
        for (folder, pid, grp), obs in groups.items():
            latest = {}
            for o, oid, rec, r in sorted(obs, key=lambda x: x[0]):
                latest[oid] = (o, rec, r)
            values, lin, first = {}, [], None
            for o, rec, r in sorted(latest.values(), key=lambda x: x[0]):
                values.update({k: v for k, v in rec.items() if k in VITAL_UNITS})
                lin += rec['_lineage']
                first = first or rec
            if len(obs) > len(latest):
                self.drop('fhir_native_bulk_copy', 'vitals', len(obs) - len(latest))
            rec = dict(first)
            rec.update(values)
            rec['_lineage'] = lin
            rec['units'] = {k: VITAL_UNITS[k] for k in values}
            cands[(folder, pid, grp)].append(((), rec, False))
        yield from self.emit(self.versions('vitals', cands))

    def _fhir_medreq(self, r, res, folder, pid, mrn, keys):
        mcc = res.get('medicationCodeableConcept') or {}
        rxcui = next((c.get('code') for c in mcc.get('coding') or [] if 'rxnorm' in str(c.get('system'))), None)
        name = mcc.get('text') or next((c.get('display') for c in mcc.get('coding') or [] if c.get('display')), None)
        sigtext = next((d.get('text') for d in res.get('dosageInstruction') or [] if isinstance(d, dict) and d.get('text')), None)
        listed = res.get('intent') in ('plan', 'proposal') or bool(res.get('reportedBoolean'))
        stream = 'medication_lists' if listed else 'medication_orders'
        rec = self._fhir_base(stream, r, res, folder, pid, mrn, keys, 'fhir_medicationrequest', event=res.get('authoredOn'),
                              recorded=res.get('authoredOn'), encounter=_ref(res.get('encounter')))
        st = res.get('status')
        if listed:
            rec.update({'name': name, 'rxcui': rxcui, 'list_status': 'active' if st in ('active', None) else ('discontinued' if st == 'stopped' else st),
                        'source': 'patient_reported' if res.get('reportedBoolean') else 'prescribed', 'sig': sigtext,
                        'frequency': _frequency(sigtext or name), 'medication_list_id': f'fhir:{folder}:{res.get("id")}'})
        else:
            rec.update({'medication_name': name, 'rxcui': rxcui, 'prescription_id': f'fhir:{folder}:{res.get("id")}', 'status': st,
                        'order_status': st, 'order_class': res.get('intent'), 'sig': sigtext, 'directions_text': sigtext,
                        'frequency': _frequency(sigtext), 'start_time': _iso(res.get('authoredOn'))})
        return stream, rec, None

    def _fhir_docref(self, r, res, folder, pid, mrn, keys):
        text = None
        for c in res.get('content') or []:
            att = (c or {}).get('attachment') or {}
            if att.get('data'):
                try:
                    raw = base64.b64decode(att['data'] + '=' * (-len(att['data']) % 4))
                    text = raw.decode('utf-8-sig')
                except (ValueError, UnicodeDecodeError):
                    text = None
                    self.unmap('fhir.documentreference.undecodable', res.get('id'))
                break
        tcodes = (res.get('type') or {}).get('coding') or []
        disp = next((c.get('display') for c in tcodes if c.get('display')), None)
        loinc = next((c.get('code') for c in tcodes if 'loinc' in str(c.get('system'))), None)
        enc = next((_ref(e) for e in ((res.get('context') or {}).get('encounter') or [])), None)
        rec = self._fhir_base('notes', r, res, folder, pid, mrn, keys, 'fhir_documentreference', event=res.get('date'), recorded=res.get('date'),
                              encounter=enc, signed=res.get('date'))
        rec.update({'note_id': f'fhir:{folder}:{res.get("id")}', 'note_type': _snake(disp) or loinc, 'loinc_document_code': loinc,
                    'note_status': 'signed' if res.get('status') == 'current' else res.get('status'), 'signed_date': _day(res.get('date')),
                    'authored_time': _iso(res.get('date')), 'text': text})
        return 'notes', rec, None

    def _fhir_appt(self, r, res, folder, pid, mrn, keys):
        st = res.get('status')
        status = FHIR_APPT.get(st)
        if not status:
            self.unmap('fhir.appointment.status', st)
        rec = self._fhir_base('appointments', r, res, folder, pid, mrn, keys, 'fhir_appointment',
                              event=res.get('created') if st == 'booked' else (res.get('meta') or {}).get('lastUpdated'),
                              recorded=res.get('created'))
        rec.update({'appointment_id': f'fhir:{folder}:{res.get("id")}', 'scheduled_date': _day(res.get('start')),
                    'scheduled_time': _iso(res.get('start')), 'scheduled_end_time': _iso(res.get('end')), 'status': status or _snake(st),
                    'slot_minutes': res.get('minutesDuration'), 'booked_at': _iso(res.get('created'))})
        return 'appointments', rec, None

    def _fhir_allergy(self, r, res, folder, pid, mrn, keys):
        code = res.get('code') or {}
        rx = next((c.get('code') for c in code.get('coding') or [] if 'rxnorm' in str(c.get('system'))), None)
        sub = code.get('text') or next((c.get('display') for c in code.get('coding') or [] if c.get('display')), None)
        st = next((c.get('code') for c in ((res.get('clinicalStatus') or {}).get('coding') or [])), None)
        reaction = next((m.get('text') or next((c.get('display') for c in m.get('coding') or []), None)
                         for rx_ in res.get('reaction') or [] for m in (rx_ or {}).get('manifestation') or []), None)
        nka = bool(sub and re.search(r'no known', sub, re.I))
        rec = self._fhir_base('allergies', r, res, folder, pid, mrn, keys, 'fhir_allergyintolerance', event=res.get('recordedDate'),
                              recorded=res.get('recordedDate'))
        rec.update({'substance': None if nka else sub, 'coded': bool(rx), 'code': rx, 'code_system': 'RxNorm' if rx else None,
                    'reaction': reaction, 'status': st, 'no_known_allergies': nka,
                    'display': 'NKDA' if nka else (f'{sub} — {reaction}' if reaction else sub), 'verification': 'not_supplied'})
        return 'allergies', rec, None

    def _fhir_imm(self, r, res, folder, pid, mrn, keys):
        vc = res.get('vaccineCode') or {}
        cvx = next((c.get('code') for c in vc.get('coding') or [] if 'cvx' in str(c.get('system'))), None)
        rec = self._fhir_base('immunizations', r, res, folder, pid, mrn, keys, 'fhir_immunization', event=res.get('occurrenceDateTime'),
                              recorded=res.get('recorded'), encounter=_ref(res.get('encounter')))
        rec.update({'vaccine': (vc.get('text') or '').lower() or None, 'cvx_code': cvx,
                    'administration_status': res.get('status') if res.get('primarySource', True) else 'historical'})
        return 'immunizations', rec, None

    def _fhir_presence(self, folder, res):
        cats = {c.get('code') for cc in res.get('category') or [] for c in (cc or {}).get('coding') or []}
        if 'laboratory' not in cats:
            return
        pid = _ref(res.get('subject'))
        mrn = self.F.get((folder, pid))
        q = res.get('valueQuantity') or {}
        loinc = next((c.get('code') for c in (res.get('code') or {}).get('coding') or [] if 'loinc' in str(c.get('system'))), None)
        rec = {'loinc': loinc, 'specimen_time': res.get('effectiveDateTime'), 'value': q.get('value') if q else res.get('valueString')}
        self._presence(_org_from_name(mrn) or folder, mrn, rec)

    # ================================================================================================= HL7
    def _hl7_messages(self, code):
        msgs = {}
        seen = {}
        for r in self.rows('hl7_message', count=False):
            if (_c(r.get('message_code')) or '') != code:
                continue
            cid = _c(r.get('message_control_id'))
            k = (r.get('_file'), r.get('message_key'))
            if cid and cid in seen:
                prev = msgs[seen[cid]]
                keep_new = r['_variant'] != 'resend' and prev['_variant'] == 'resend'
                self.drop('hl7_resend_duplicate', 'adt_messages' if code == 'ADT' else 'labs')
                if not keep_new:
                    continue
                del msgs[seen[cid]]
            if cid:
                seen[cid] = k
            msgs[k] = r
        return msgs

    def adt(self):
        if not self.has('hl7_message'):
            return
        msgs = self._hl7_messages('ADT')
        dg = defaultdict(list)
        for r in self.rows('hl7_diagnosis', count=False):
            dg[(r.get('_file'), r.get('message_key'))].append(r)
        visits = defaultdict(list)
        for k, m in msgs.items():
            visits[_c(m.get('visit_number')) or f'msg:{k}'].append((_iso(m.get('event_recorded_datetime') or m.get('message_datetime')) or '', k, m))
        cands = defaultdict(list)
        for visit, ms in visits.items():
            ms.sort(key=lambda x: (x[0], _order(x[2])))
            prior_class, admit = None, None
            for t, k, m in ms:
                trig = _c(m.get('trigger_event'))
                auth = _c(m.get('patient_id_assigning_authority')) or _c(m.get('sending_facility'))
                org = _org_from_authority(auth) or _org_from_authority(m.get('sending_facility'))
                pid = _c(m.get('patient_id'))
                rec = self.base('adt_messages', m, 'hl7_message', vendor='hl7', instance=_c(m.get('sending_facility')) or auth or 'unknown',
                                org=org, keys=[hl7_key(auth, pid)], source_patient_id=pid,
                                event=t or m.get('message_datetime'), recorded=m.get('message_datetime'),
                                encounter_id=_c(m.get('visit_number')))
                if trig in ('A01', 'A04', 'A06') and not admit:
                    admit = _iso(m.get('admit_datetime')) or t
                pclass = _c(m.get('patient_class'))
                dxs = sorted(dg.get(k, []), key=lambda d: _num(d.get('set_id')) or 0)
                adm = next((d for d in dxs if _c(d.get('diagnosis_type')) in ('W', 'A')), None)
                dis = next((d for d in dxs if _c(d.get('diagnosis_type')) == 'F'), None)
                dcode = _c(m.get('discharge_disposition'))
                rec['_lineage'] += [_lin('hl7_diagnosis', d) for d in dxs]
                rec.update({'message_type': _c(m.get('message_type')), 'trigger_event': trig, 'patient_class': pclass,
                            'visit_number': _c(m.get('visit_number')), 'assigned_location': _c(m.get('assigned_location')),
                            'admit_datetime': admit if trig != 'A03' or admit else None,
                            'discharge_datetime': (_iso(m.get('discharge_datetime')) or t) if trig == 'A03' else None,
                            'discharge_disposition': HL7_DISPOSITION.get(dcode or '', dcode) if dcode else None,
                            'discharge_disposition_code': dcode,
                            'admit_diagnosis': _dg1(adm) if trig in ('A01', 'A04', 'A06', 'A08') and adm else None,
                            'discharge_diagnosis': _dg1(dis) if dis else None,
                            'diagnoses': [_dg1(d) for d in dxs] or None,
                            'message_datetime': _c(m.get('message_datetime_raw')) or _c(m.get('message_datetime')),
                            'message_control_id': _c(m.get('message_control_id')),
                            'prior_patient_class': prior_class if prior_class and prior_class != pclass else None,
                            'hospital_service': _c(m.get('hospital_service')), 'admit_source': _c(m.get('admit_source'))})
                if dcode and dcode not in HL7_DISPOSITION:
                    self.unmap('hl7.discharge_disposition', dcode)
                prior_class = pclass or prior_class
                cands[(visit, _c(m.get('message_control_id')) or k)].append((_order(m), rec, False))
        yield from self.emit(self.versions('adt_messages', cands, chain=False))

    def oru(self):
        if not self.has('hl7_message') or not self.has('hl7_observation'):
            return
        msgs = self._hl7_messages('ORU')
        orders = {(r.get('_file'), r.get('message_key'), r.get('order_ordinal')): r for r in self.rows('hl7_order', count=False)}
        chains = defaultdict(list)
        for o in self.rows('hl7_observation', count=False):
            k = (o.get('_file'), o.get('message_key'))
            m = msgs.get(k)
            if m is None:
                continue  # resend duplicate message (counted on the message)
            ob = orders.get((o.get('_file'), o.get('message_key'), o.get('order_ordinal'))) or {}
            auth = _c(m.get('patient_id_assigning_authority'))
            org = _org_from_authority(auth) or _org_from_authority(m.get('receiving_facility'))
            mrn = _c(m.get('patient_id')) or _c(o.get('patient_id'))
            coll = _iso(o.get('obr_observation_datetime') or ob.get('observation_datetime') or o.get('observation_datetime'))
            loinc = _c(o.get('loinc_code'))
            code = loinc or _c(o.get('local_code')) or _c(o.get('observation_code'))
            st = _c(o.get('result_status')) or _c(o.get('obr_result_status'))
            value = _value(o.get('value')) if _c(o.get('value_type')) != 'ST' else (_c(o.get('value')) or _c(o.get('value_text')))
            if isinstance(value, str) and _c(o.get('value_type')) == 'NM':
                value = _value(value)
            rr = _c(o.get('reference_range'))
            resulted = _iso(ob.get('results_report_datetime') or m.get('message_datetime'))
            rec = self.base('labs', o, 'hl7_observation', vendor='hl7', instance=_c(m.get('sending_facility')) or 'REFLAB', org=org,
                            keys=[mrn_key(mrn, auth or org), hl7_key(auth, mrn)],
                            source_patient_id=mrn, event=coll or resulted, recorded=resulted)
            rec['_lineage'].insert(0, _lin('hl7_message', m))
            rec.update(_lab_fields(_c(o.get('observation_text')) or _c(o.get('local_text')), loinc, _c(o.get('local_code')), value,
                                   _c(o.get('units')), rr, _hl7_flag(o.get('abnormal_flags')) or _flag(value, rr),
                                   LAB_STATUS.get(st or 'F') or _snake(st), coll, resulted, _c(o.get('placer_order_number')), _c(ob.get('universal_service_text'))))
            rec['performing_organization'] = _c(m.get('sending_facility'))
            rec['message_control_id'] = _c(m.get('message_control_id'))
            chains[(org, mrn, _c(o.get('placer_order_number')), code)].append(
                ((resulted or '', 0 if st == 'F' else 1) + _order(o), rec, False, (org, mrn, loinc, _day(coll), value, _c(o.get('observation_text')) or _c(o.get('local_text')))))
        kept = defaultdict(list)
        for key, versions in chains.items():
            if any(self._present(*p) for *_, p in versions):
                self.drop('oru_duplicate_of_ehr_lab', 'labs', len(versions))
                continue
            kept[key] = [v[:3] for v in versions]
        for rec in self.emit(self.versions('labs', kept)):
            rec['reference_lab_result'] = True
            yield rec

    # ---- ORU presence index
    def _presence(self, org, mrn, rec):
        """Index an EHR lab for the ORU duplicate check: (org, mrn, loinc | '*', local collection day) -> values."""
        if not mrn:
            return
        day = _day(rec.get('specimen_time') or rec.get('collected_time'))
        if not day:
            return
        v = rec.get('value')
        for o in {org, _org_from_name(mrn)} - {None}:
            if rec.get('loinc'):
                self.lab_presence[(o, mrn, rec['loinc'], day)].append(v)
            self.lab_presence_any[(o, mrn, '*', day)].append((v, rec.get('loinc'), _name_tokens(rec.get('name'))))

    def _present(self, org, mrn, loinc, day, value, name=None):
        """Same result already in the EHR: same LOINC + day + value; else (EHR row without LOINC, or ORU without LOINC)
        same day + value + compatible LOINC/name."""
        if not (mrn and day):
            return False
        toks = _name_tokens(name)
        for o in {org, _org_from_name(mrn)} - {None}:
            if loinc and any(_same_value(value, v) for v in self.lab_presence.get((o, mrn, loinc, day), ())):
                return True
            for v, l, t in self.lab_presence_any.get((o, mrn, '*', day), ()):
                if not _same_value(value, v) or (loinc and l and l != loinc):
                    continue
                if (loinc and l == loinc) or (toks and t and (toks[0] == t[0] or set(toks) <= set(t) or set(t) <= set(toks))):
                    return True
                if not loinc and not toks:
                    return True
        return False

    # ---- driver
    def all(self):
        for fn in (self.epic, self.athena, self.ecw, self.fhir, self.adt, self.oru):
            yield from fn()

    def report(self):
        return {'records': dict(sorted(self.out.items())), 'total': sum(self.out.values()),
                'dropped': dict(self.dropped.most_common()),
                'dropped_by_stream': {k: dict(v.most_common()) for k, v in sorted(self.dropped_by_stream.items())},
                'unresolved': dict(self.unresolved), 'missing_tables': sorted(self.missing),
                'unmapped': {k: dict(v.most_common(15)) for k, v in sorted(self.unmapped.items())},
                'unmapped_totals': {k: sum(v.values()) for k, v in sorted(self.unmapped.items())},
                'lab_presence_keys': len(self.lab_presence)}


# ------------------------------------------------------------------------------------------------ shared field builders
def _condition_fields(desc, icd10, snomed, status, onset, abatement, basis):
    return {'description': desc or icd10, 'icd10': icd10, 'icd10_code_system': 'ICD-10-CM' if icd10 else None, 'snomed': snomed,
            'problem_code': icd10 or snomed, 'problem_code_system': 'ICD-10-CM' if icd10 else ('SNOMED-CT' if snomed else None),
            'clinical_status': status, 'onset_date': onset, 'onset': onset, 'abatement_date': abatement,
            'problem_type': 'problem_list' if basis is None else None, 'diagnosis_basis': basis or 'ehr_problem_list'}


def _lab_fields(name, loinc, local_code, value, unit, rr, flag, status, specimen_time, resulted_time, order_id, panel):
    st = _iso(specimen_time)
    return {'name': name, 'test': name, 'loinc': loinc, 'local_code': local_code, 'value': value, 'unit': unit,
            'reference_range': rr, 'abnormal_flag': flag, 'result_status': status, 'specimen_time': st, 'collected_time': st,
            'resulted_time': _iso(resulted_time), 'laboratory_order_id': order_id, 'panel': panel}


_NAME_ALIASES = {'k': 'potassium', 'na': 'sodium', 'glu': 'glucose', 'creat': 'creatinine', 'hgb': 'hemoglobin', 'hb': 'hemoglobin',
                 'a1c': 'hba1c', 'hgba1c': 'hba1c', 'ldlc': 'ldl', 'gfr': 'egfr', 'microalb': 'microalbumin', 'bun': 'urea'}


def _name_tokens(name):
    toks = [t for t in re.split(r'[^a-z0-9]+', (name or '').lower()) if t and t not in ('serum', 'cholesterol', 'in', 'of', 'total', 'calc')]
    toks = [_NAME_ALIASES.get(t, t) for t in toks]
    if toks and toks[0] == 'hemoglobin' and 'a1c' in toks:
        toks = ['hba1c']
    return toks


def _decimals(v):
    s = repr(float(v)).rstrip('0').rstrip('.') if isinstance(v, float) else str(v)
    return len(s.split('.')[1]) if '.' in s else 0


def _same_value(a, b):
    """Equal values, numbers compared at the coarser of the two precisions (eCW 0.7 = ORU 0.69 = 0.70)."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        d = min(_decimals(a), _decimals(b))
        return abs(round(float(a), d) - round(float(b), d)) < 1e-9 or abs(a - b) < 10 ** -d / 2 + 1e-9
    return _vkey(a) == _vkey(b)


def _lab_migration_key(rec):
    m = _minute(rec.get('specimen_time'))
    return (rec.get('loinc') or rec.get('name'), m, _vkey(rec.get('value'))) if m else None


def _frequency(sig):
    s = (sig or '').lower()
    for pat, f in ((r'\b(twice daily|bid|2 times (a|per) day)\b', 'twice_daily'), (r'\b(three times daily|tid)\b', 'three_times_daily'),
                   (r'\b(four times daily|qid)\b', 'four_times_daily'), (r'\b(at bedtime|qhs|nightly)\b', 'at_bedtime'),
                   (r'\b(as needed|prn)\b', 'as_needed'), (r'\bweekly\b', 'weekly'), (r'\b(daily|once a day|qd)\b', 'daily')):
        if re.search(pat, s):
            return f
    return None


def _res(r):
    try:
        v = json.loads(r.get('_resource') or r.get('resource') or '{}')  # landing._ident strips the leading underscore
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def _ref(ref):
    if isinstance(ref, dict):
        ref = ref.get('reference')
    if not isinstance(ref, str) or not ref:
        return None
    return ref.rsplit('/', 1)[-1]


def _dg1(d):
    return {'code': _icd(d.get('diagnosis_code')), 'system': _c(d.get('coding_system')), 'type': _c(d.get('diagnosis_type')),
            'description': _c(d.get('diagnosis_text'))}


# ------------------------------------------------------------------------------------------------ entry point
def records(db, resolve):
    """Canonical clinical records from the landing database (read-only use of db). See module docstring."""
    global _REPORT
    run = _Run(db, resolve)
    _REPORT = {'status': 'running'}
    try:
        yield from run.all()
    finally:
        _REPORT = run.report()
        _REPORT['status'] = 'complete'
