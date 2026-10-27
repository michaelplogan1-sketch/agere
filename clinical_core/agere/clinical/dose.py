"""Deterministic medication dose parsing for governed SQL.

Registered on every governed connection so any analysis can turn received dose
evidence into numbers: per-administration milligrams, administrations per day
and guideline target daily dose. Unparseable text returns NULL, never a guess.
"""
import re
import json
import math
from datetime import date

VERSION = 'medication-dose/4'
REFERENCE = {
    'id': 'aha-acc-hfsa-2022-table14', 'version': 1,
    'source': '2022 AHA/ACC/HFSA guideline, Table 14 (official slide set, slides 87–90)',
    'url': 'https://professional.heart.org/-/media/832EA0F4E73948848612F228F7FA2D35.pdf',
    'checked_at': '2026-10-20',
    'scope': 'Reference dose ranges for listed HFrEF drugs; not an individualized target or current guideline review.',
    'qualification': 'Source-table arithmetic checked; clinical indication, formulation, tolerance and patient-specific dosing require review.',
}

_FREQUENCY = [  # (pattern, administrations per day); first match wins
    (r'\b(?:qid|four times (?:a |per )?day|4 times daily|four_times_daily)\b', 4),
    (r'\b(?:tid|three times (?:a |per )?day|3 times daily|three_times_daily|q8h)\b', 3),
    (r'\b(?:bid|twice (?:a |per )?day|2 times daily|twice_daily|q12h|two times daily)\b', 2),
    (r'\b(?:qd|qam|qpm|qhs|daily|once (?:a |per )?day|once_daily|each_morning|every morning|at bedtime|nightly|q24h)\b', 1),
]

# Guideline target daily doses (2022 AHA/ACC/HFSA heart failure guideline, typical adult targets).
# ingredient pattern -> (target daily mg, target text). Sacubitril/valsartan uses the first (sacubitril) component.
TARGETS = [
    (r'sacubitril', 194.0, '97/103 mg twice daily'),
    (r'lisinopril', 20.0, '20-40 mg daily'),
    (r'enalapril', 20.0, '10-20 mg twice daily'),
    (r'ramipril', 10.0, '10 mg daily'),
    (r'captopril', 150.0, '50 mg three times daily'),
    (r'losartan', 50.0, '50-150 mg daily'),
    (r'(?<!sacubitril[/ -])valsartan', 320.0, '160 mg twice daily'),
    (r'candesartan', 32.0, '32 mg daily'),
    (r'carvedilol phosphate|carvedilol.*(?:extended|\ber\b|\bcr\b)', 80.0, '80 mg daily (extended-release phosphate)'),
    (r'carvedilol', 50.0, '25 mg twice daily (50 mg twice daily if >85 kg)'),
    (r'metoprolol(?=.*(?:succinate|\ber\b|\bxl\b|24 ?hr|extended))', 200.0, '200 mg daily'),
    (r'bisoprolol', 10.0, '10 mg daily'),
    (r'spironolactone', 25.0, '25-50 mg daily'),
    (r'eplerenone', 50.0, '50 mg daily'),
    (r'dapagliflozin', 10.0, '10 mg daily'),
    (r'empagliflozin', 10.0, '10 mg daily'),
]


def dose_mg(text):
    """First milligram strength in the text, e.g. 'carvedilol 12.5 mg tablet BID' -> 12.5, '49/51 mg' -> 49."""
    if not isinstance(text, str):
        return None
    t = text.lower()
    # A range, concentration, negative amount or PRN instruction is not one dose.
    if re.search(r'\d\s*(?:[-–]|to)\s*\.?\d|(?<!\w)-\s*\.?\d|\d,\d|mg\s*(?:/|per\s+|in\s+)\s*(?:\d+(?:\.\d+)?\s*)?(?:ml|l)\b|\b(?:prn|as needed|up to)\b', t):
        return None
    if re.search(r'\d\s*(?:mcg|µg|ug|g/|units?|meq|ml)\b', t) and not re.search(r'\d\s*mg\b', t):
        return None
    m = re.search(r'(?<![\d.])((?:\d+(?:\.\d+)?|\.\d+))(?:\s*/\s*\d+(?:\.\d+)?)?\s*mg\b', t)
    return _positive(m.group(1)) if m else None


def per_day(text):
    """Administrations per day from frequency or directions text; NULL when not stated."""
    if not isinstance(text, str):
        return _positive(text)
    t = ' ' + text.lower().replace('_', ' ') + ' '
    if re.search(r'\b(?:prn|as needed|every other|except|alternat|weekly)\b|\d\s*[-–]\s*\d', t):
        return None
    for pattern, n in _FREQUENCY:
        if re.search(pattern.replace('_', ' '), t):
            return float(n)
    return None


def target_mg(text):
    """Lower reference daily bound, not an individualized treatment target."""
    if not isinstance(text, str):
        return None
    t = text.lower()
    for pattern, mg, _ in TARGETS:
        if re.search(pattern, t):
            return mg
    return None


def target_max_mg(text):
    lower = target_mg(text)
    if lower is None: return None
    for pattern, upper in ((r'lisinopril',40.0), (r'enalapril',40.0), (r'losartan',150.0),
                           (r'carvedilol',100.0), (r'spironolactone',50.0)):
        if re.search(pattern, text.lower()):
            # Extended-release carvedilol has its own fixed reference.
            return lower if pattern == 'carvedilol' and lower == 80.0 else upper
    return lower


_KEYS = [(r'sacubitril', 'sacubitril_valsartan'), (r'metoprolol(?=.*(?:succinate|\ber\b|\bxl\b|24 ?hr|extended))', 'metoprolol_succinate'),
         (r'metoprolol.*tartrate', 'metoprolol_tartrate'), (r'metoprolol', 'metoprolol_unspecified'),
         (r'carvedilol phosphate|carvedilol.*(?:extended|\ber\b|\bcr\b)', 'carvedilol_er')]


def drug_key(text):
    """Stable ingredient key used to compare doses of the same drug over time."""
    if not isinstance(text, str) or not text.strip():
        return None
    t = text.lower()
    for pattern, key in _KEYS:
        if re.search(pattern, t):
            return key
    m = re.search(r'[a-z][a-z-]+', t)
    return m.group(0) if m else None


def _positive(value):
    if isinstance(value, bool): return None
    try: number = float(value)
    except (ValueError, TypeError): return None
    return number if math.isfinite(number) and number > 0 else None


def statement_evidence(payload, stream):
    """Keep administration arithmetic and received fields together; no unit-count default."""
    p = json.loads(payload)
    fields = {k: p[k] for k in ('dose', 'dose_unit', 'strength', 'strength_unit', 'name',
              'medication_name', 'product_description', 'medication_display', 'directions_text',
              'frequency', 'times_per_day') if k in p}
    evidence = {'received': fields, 'per_admin_mg': None, 'basis': 'interpretation_required'}
    if stream == 'medication_orders':
        unit = str(p.get('dose_unit') or '').strip().lower().rstrip('s')
        amount = _positive(p.get('dose'))
        if unit == 'mg':
            evidence.update(per_admin_mg=amount, basis='explicit_mg' if amount else 'invalid_quantity')
            return json.dumps(evidence)
        strength = _positive(p.get('strength'))
        strength_unit = str(p.get('strength_unit') or '').strip().lower().rstrip('s')
        if unit in ('tablet', 'capsule') and strength_unit in ('mg/'+unit, 'mg') and amount and strength:
            evidence.update(per_admin_mg=_positive(amount * strength), basis='strength_times_unit_count',
                            strength_mg_per_unit=strength, unit_count=amount, unit=unit)
        return json.dumps(evidence)
    texts = [p.get(k) for k in ('dose', 'directions_text') if isinstance(p.get(k), str) and p[k].strip()]
    text = '; '.join(texts)
    # Do not turn a conditional, variable or conflicting regimen into a point dose.
    if re.search(r'\b(?:prn|as needed|up to|alternat\w*|every other|except|taper\w*|then|weekly|or|ml)\b|'
                 r'\d\s*(?:[-–]|to)\s*\d|(?<!\w)-\s*\.?\d|\d,\d', text.lower()):
        evidence['basis'] = 'variable_or_ambiguous_instruction'
        return json.dumps(evidence)
    count_rx = r'(?<![\w./])((?:\d+(?:\.\d+)?|\.\d+)(?:\s*/\s*\d+(?:\.\d+)?)?|one|two|three|half)\s+(tablets?|capsules?)\b'
    counts = []
    for match in re.finditer(count_rx, text.lower()):
        token = match.group(1)
        words = {'one': 1, 'two': 2, 'three': 3, 'half': .5}
        if token in words: amount = words[token]
        elif '/' in token:
            top, bottom = (_positive(v.strip()) for v in token.split('/'))
            amount = _positive(top / bottom) if top and bottom else None
        else: amount = _positive(token)
        counts.append((amount, match.group(2).rstrip('s')))
    if counts:
        if len(set(counts)) != 1 or counts[0][0] is None:
            evidence['basis'] = 'conflicting_or_invalid_unit_count'
            return json.dumps(evidence)
        amount, unit = counts[0]
        # A milligram number beside an explicit unit count is a strength only
        # when identified as per-unit. Otherwise it could be the total dose.
        per_unit = re.findall(r'(?<![\d.])(\d+(?:\.\d+)?|\.\d+)\s*mg\s*(?:/|per\s+)\s*'+unit+r'\b', text.lower())
        if per_unit:
            strengths = {_positive(v) for v in per_unit}
        elif re.search(r'\bmg\b', text.lower()):
            strengths = set()
        else:
            names = [p.get(k) for k in ('name', 'product_description', 'medication_display') if p.get(k)]
            strengths = {dose_mg(name) for name in names if len(re.findall(r'\bmg\b',name.lower())) == 1}
        strengths.discard(None)
        if len(strengths) == 1:
            strength = next(iter(strengths))
            evidence.update(per_admin_mg=_positive(strength * amount), basis='strength_times_unit_count',
                            strength_mg_per_unit=strength, unit_count=amount, unit=unit)
        else: evidence['basis'] = 'missing_or_ambiguous_product_strength'
    elif re.search(r'\b(?:tablets?|capsules?)\b', text.lower()):
        evidence['basis'] = 'missing_administration_unit_count'
    else:
        values = {dose_mg(t) for t in texts if len(re.findall(r'\bmg\b',t.lower())) == 1}
        values.discard(None)
        if len(values) == 1:
            evidence.update(per_admin_mg=next(iter(values)), basis='explicit_mg')
    return json.dumps(evidence)


def statement_mg(payload, stream):
    """Administration quantity, never a product strength with an assumed unit count."""
    return json.loads(statement_evidence(payload, stream))['per_admin_mg']


def statement_per_day(payload, stream):
    p = json.loads(payload)
    texts = [p.get(k) for k in ('frequency', 'directions_text', 'dose') if isinstance(p.get(k), str)]
    if any(re.search(r'\b(?:prn|as needed|every other|except|alternat\w*|weekly|hold|if|unless|taper\w*|then)\b|\d\s*[-–]\s*\d', t.lower()) for t in texts):
        return None
    frequencies = {_positive(p.get('times_per_day'))} if p.get('times_per_day') is not None else set()
    frequencies.update(per_day(t) for t in texts)
    frequencies.discard(None)
    return next(iter(frequencies)) if len(frequencies) == 1 else None


def statement_status(status):
    status = str(status or '').strip().lower().replace('-', '_').replace(' ', '_')
    if status in ('active', 'taking'): return 'active_documentation'
    if status in ('stopped', 'discontinued', 'held', 'on_hold', 'inactive', 'not_taking', 'cancelled', 'canceled'):
        return 'not_active'
    return 'unknown_status'


def statement_day(value):
    # Month/year precision and recorded-date substitutes cannot refresh a regimen.
    try: return date.fromisoformat(value).isoformat() if isinstance(value, str) and len(value) == 10 else None
    except ValueError: return None


def register(db):
    db.create_function('drug_key', 1, drug_key, deterministic=True)
    db.create_function('dose_mg', 1, dose_mg, deterministic=True)
    db.create_function('per_day', 1, per_day, deterministic=True)
    db.create_function('target_mg', 1, target_mg, deterministic=True)
    db.create_function('target_max_mg', 1, target_max_mg, deterministic=True)
    db.create_function('statement_mg', 2, statement_mg, deterministic=True)
    db.create_function('statement_evidence', 2, statement_evidence, deterministic=True)
    db.create_function('statement_per_day', 2, statement_per_day, deterministic=True)
    db.create_function('statement_status', 1, statement_status, deterministic=True)
    db.create_function('statement_day', 1, statement_day, deterministic=True)
    return db


# Governed per-record dose evidence: one row per received dose statement with its source record.
# A list/order statement without an event date is dated by when it was recorded (date_basis says which).
MEDICATION_DOSE_SQL = """(SELECT patient_id, source_stream, record_id, event_date, date_basis, drug_text, status,
  per_admin_mg, times_per_day, per_admin_mg * times_per_day AS daily_mg, dose_evidence,
  target_mg(drug_text) AS target_daily_mg, target_max_mg(drug_text) AS target_max_daily_mg
 FROM (SELECT patient_id, stream AS source_stream, record_id,
   statement_day(COALESCE(json_extract(payload,'$.event_date'), json_extract(payload,'$.recorded_date'))) AS event_date,
   CASE WHEN statement_day(json_extract(payload,'$.event_date')) IS NOT NULL THEN 'event_date'
        WHEN statement_day(json_extract(payload,'$.recorded_date')) IS NOT NULL THEN 'recorded_date' END AS date_basis,
   lower(CASE stream WHEN 'medication_orders' THEN json_extract(payload,'$.medication_name')
     WHEN 'medication_lists' THEN json_extract(payload,'$.name')
     ELSE COALESCE(json_extract(payload,'$.product_description'),json_extract(payload,'$.medication_display')) END) AS drug_text,
   CASE stream WHEN 'medication_orders' THEN json_extract(payload,'$.status')
     WHEN 'medication_lists' THEN json_extract(payload,'$.list_status') ELSE 'label' END AS status,
   statement_mg(payload, stream) AS per_admin_mg, statement_per_day(payload, stream) AS times_per_day,
   statement_evidence(payload, stream) AS dose_evidence
 FROM record WHERE stream IN ('medication_orders','medication_lists','medication_label_evidence'))
 WHERE drug_text IS NOT NULL)"""


def _structured_current_dose_sql(as_of, lookback_days):
    """Latest dated regimen documentation, with uncertainty and all selected sources.

    Orders/lists describe documentation, not ingestion or maximum tolerance.
    Labels are product evidence, not regimen updates. Undated relevant statements
    block silently selecting a prior dated regimen. Ties are retained, not voted.
    """
    as_of = date.fromisoformat(as_of).isoformat()
    days = int(lookback_days)
    if days <= 0: raise ValueError('Positive current-dose lookback required')
    return ("(SELECT patient_id, drug_key, dose_date, drug_text, record_id, source_stream, source_record_ids, "
            "statement_state, dose_evidence, 0 AS current_use_established, "
            "CASE WHEN statement_state='active_documentation' THEN raw_daily_mg END AS daily_mg, target_daily_mg, target_max_daily_mg, "
            "'aha-acc-hfsa-2022-table14' AS target_reference, "
            "CASE WHEN statement_state!='active_documentation' THEN statement_state "
            "WHEN raw_daily_mg IS NULL THEN 'unknown_dose' WHEN target_daily_mg IS NULL THEN 'no_target' "
            "WHEN raw_daily_mg>target_max_daily_mg THEN 'above_reference' "
            "WHEN raw_daily_mg>=target_daily_mg THEN 'at_target' ELSE 'below_target' END AS dose_status FROM ("
            "SELECT patient_id, drug_key, max(event_date) AS dose_date, min(drug_text) AS drug_text, "
            "CASE WHEN count(*)=1 THEN min(record_id) END AS record_id, "
            "CASE WHEN count(DISTINCT source_stream)=1 THEN min(source_stream) ELSE 'multiple' END AS source_stream, "
            "json_group_array(record_id) AS source_record_ids, "
            "json_group_array(json_object('record_id',record_id,'calculation',json(dose_evidence),"
            "'times_per_day',times_per_day,'daily_mg',daily_mg)) AS dose_evidence, "
            "CASE WHEN max(undated)=1 THEN 'unknown_date' "
            "WHEN count(DISTINCT statement_status(status))>1 OR "
            "count(DISTINCT json_array(per_admin_mg,times_per_day))>1 THEN 'conflicting' "
            "ELSE min(statement_status(status)) END AS statement_state, "
            "min(daily_mg) AS raw_daily_mg, min(target_daily_mg) AS target_daily_mg, min(target_max_daily_mg) AS target_max_daily_mg FROM ("
            "SELECT d.*, drug_key(d.drug_text) AS drug_key, "
            "max(d.event_date IS NULL) OVER (PARTITION BY d.patient_id,drug_key(d.drug_text)) AS undated, "
            "rank() OVER (PARTITION BY d.patient_id,drug_key(d.drug_text) ORDER BY d.event_date DESC) AS latest "
            "FROM " + MEDICATION_DOSE_SQL + " d WHERE d.source_stream!='medication_label_evidence' AND "
            f"(d.event_date IS NULL OR (d.event_date>date('{as_of}','-{days} days') AND d.event_date<='{as_of}'))) "
            "WHERE latest=1 OR undated=1 GROUP BY patient_id,drug_key))")


def current_dose_sql(as_of, lookback_days, *, note_changes=False):
    """Reconcile structured regimens with candidate note changes when available.

    A note extraction is an interpretation prerequisite, never a confirmed stop,
    new dose or actual use. Keep the structured result separately so uncertainty
    does not erase received evidence. Exact named-drug matching is deliberately
    narrower than class, brand or formulation equivalence.
    """
    structured = _structured_current_dose_sql(as_of, lookback_days)
    if not note_changes:
        return ("(SELECT s.*, s.statement_state AS documented_statement_state, "
                "s.daily_mg AS documented_daily_mg, s.dose_status AS documented_dose_status, "
                "0 AS note_scan_available, 0 AS note_review_required, '[]' AS note_evidence, "
                "NULL AS latest_note_date FROM " + structured + " s)")
    as_of = date.fromisoformat(as_of).isoformat()
    days = int(lookback_days)
    # Keep source dates separate from dose dates: extracted changes do not have a
    # verified clinical-effective date. Null/invalid dates cannot be ordered away.
    return ("(WITH structured_regimens AS (SELECT * FROM " + structured + "), "
            "candidate_changes AS (SELECT patient_id, drug_key(target) AS drug_key, record_id, "
            "statement_day(event_date) AS source_date, action, target, evidence_text, extractor "
            "FROM note_therapy_change WHERE target_kind='drug' AND drug_key(target) IS NOT NULL "
            "AND (statement_day(event_date) IS NULL OR "
            f"(statement_day(event_date)>date('{as_of}','-{days} days') AND statement_day(event_date)<='{as_of}'))), "
            "regimen_keys AS (SELECT patient_id,drug_key FROM structured_regimens UNION "
            "SELECT patient_id,drug_key FROM candidate_changes), "
            "combined AS (SELECT k.patient_id,k.drug_key,s.dose_date,s.drug_text,s.record_id,s.source_stream, "
            "s.source_record_ids,s.statement_state,s.daily_mg,s.target_daily_mg,s.target_max_daily_mg, "
            "s.target_reference,s.dose_status,s.dose_evidence, "
            "(SELECT json_group_array(json_object('record_id',n.record_id,'source_date',n.source_date, "
            "'action',n.action,'target',n.target,'evidence_text',n.evidence_text,'extractor',n.extractor, "
            "'authority','unreviewed_extraction','date_basis','source_statement_date')) "
            "FROM candidate_changes n WHERE n.patient_id=k.patient_id AND n.drug_key=k.drug_key "
            "AND (n.source_date IS NULL OR s.dose_date IS NULL OR n.source_date>=s.dose_date "
            "OR s.statement_state='unknown_date')) AS note_evidence "
            "FROM regimen_keys k LEFT JOIN structured_regimens s ON s.patient_id=k.patient_id AND s.drug_key=k.drug_key) "
            "SELECT patient_id,drug_key,dose_date,COALESCE(drug_text,drug_key) AS drug_text, "
            "CASE WHEN json_array_length(note_evidence)=0 THEN record_id END AS record_id, "
            "CASE WHEN json_array_length(note_evidence)=0 THEN source_stream "
            "WHEN source_stream IS NULL THEN 'note_therapy_change' ELSE 'multiple' END AS source_stream, "
            "(SELECT json_group_array(rid) FROM (SELECT value AS rid FROM json_each(COALESCE(source_record_ids,'[]')) "
            "UNION SELECT json_extract(value,'$.record_id') AS rid FROM json_each(note_evidence)) ORDER BY rid) AS source_record_ids, "
            "CASE WHEN json_array_length(note_evidence)>0 THEN 'interpretation_required' ELSE statement_state END AS statement_state, "
            "0 AS current_use_established, CASE WHEN json_array_length(note_evidence)=0 THEN daily_mg END AS daily_mg, "
            "target_daily_mg,target_max_daily_mg,target_reference,dose_evidence, "
            "CASE WHEN json_array_length(note_evidence)>0 THEN 'interpretation_required' ELSE dose_status END AS dose_status, "
            "statement_state AS documented_statement_state,daily_mg AS documented_daily_mg,dose_status AS documented_dose_status, "
            "1 AS note_scan_available,json_array_length(note_evidence)>0 AS note_review_required,note_evidence, "
            "(SELECT max(json_extract(value,'$.source_date')) FROM json_each(note_evidence)) AS latest_note_date "
            "FROM combined)")
