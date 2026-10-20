"""Deterministic medication dose parsing for governed SQL.

Registered on every governed connection so any analysis can turn received dose
evidence into numbers: per-administration milligrams, administrations per day
and guideline target daily dose. Unparseable text returns NULL, never a guess.
"""
import re

VERSION = 'medication-dose/1'

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
    (r'losartan', 150.0, '50-150 mg daily'),
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
        return float(text) if isinstance(text, (int, float)) else None
    t = text.lower()
    if re.search(r'\d\s*(?:mcg|µg|ug|g/|units?|meq|ml)\b', t) and not re.search(r'\d\s*mg\b', t):
        return None
    m = re.search(r'(\d+(?:\.\d+)?)(?:\s*/\s*\d+(?:\.\d+)?)?\s*mg\b', t)
    return float(m.group(1)) if m else None


def per_day(text):
    """Administrations per day from frequency or directions text; NULL when not stated."""
    if not isinstance(text, str):
        return float(text) if isinstance(text, (int, float)) and text > 0 else None
    t = ' ' + text.lower().replace('_', ' ') + ' '
    for pattern, n in _FREQUENCY:
        if re.search(pattern.replace('_', ' '), t):
            return float(n)
    return None


def target_mg(text):
    """Guideline target daily mg for a recognized HF drug name, else NULL."""
    if not isinstance(text, str):
        return None
    t = text.lower()
    for pattern, mg, _ in TARGETS:
        if re.search(pattern, t):
            return mg
    return None


def register(db):
    db.create_function('dose_mg', 1, dose_mg, deterministic=True)
    db.create_function('per_day', 1, per_day, deterministic=True)
    db.create_function('target_mg', 1, target_mg, deterministic=True)
    return db


# Governed per-record dose evidence: one row per received dose statement with its source record.
MEDICATION_DOSE_SQL = """(SELECT patient_id, source_stream, record_id, event_date, drug_text, status,
  per_admin_mg, times_per_day, per_admin_mg * times_per_day AS daily_mg, target_mg(drug_text) AS target_daily_mg FROM (
 SELECT patient_id, 'medication_orders' AS source_stream, record_id,
   COALESCE(json_extract(payload,'$.event_date'), json_extract(payload,'$.recorded_date')) AS event_date,
   lower(json_extract(payload,'$.medication_name')) AS drug_text, json_extract(payload,'$.status') AS status,
   CASE WHEN json_extract(payload,'$.dose_unit') = 'mg' THEN CAST(json_extract(payload,'$.dose') AS REAL)
        WHEN json_extract(payload,'$.strength') IS NOT NULL AND json_extract(payload,'$.strength_unit') LIKE 'mg%'
        THEN CAST(json_extract(payload,'$.strength') AS REAL) * COALESCE(CAST(json_extract(payload,'$.dose') AS REAL), 1)
        ELSE dose_mg(json_extract(payload,'$.medication_name')) END AS per_admin_mg,
   COALESCE(CAST(json_extract(payload,'$.times_per_day') AS REAL), per_day(json_extract(payload,'$.frequency')),
            per_day(json_extract(payload,'$.directions_text'))) AS times_per_day
 FROM record WHERE stream = 'medication_orders'
 UNION ALL
 SELECT patient_id, 'medication_lists', record_id,
   COALESCE(json_extract(payload,'$.event_date'), json_extract(payload,'$.recorded_date')),
   lower(json_extract(payload,'$.name')), json_extract(payload,'$.list_status'),
   COALESCE(dose_mg(json_extract(payload,'$.dose')), dose_mg(json_extract(payload,'$.name'))),
   COALESCE(per_day(json_extract(payload,'$.frequency')), per_day(json_extract(payload,'$.name')))
 FROM record WHERE stream = 'medication_lists'
 UNION ALL
 SELECT patient_id, 'medication_label_evidence', record_id,
   COALESCE(json_extract(payload,'$.event_date'), json_extract(payload,'$.recorded_date')),
   lower(COALESCE(json_extract(payload,'$.product_description'), json_extract(payload,'$.medication_display'))), 'label',
   CASE WHEN json_extract(payload,'$.strength_unit') LIKE 'mg%' THEN CAST(json_extract(payload,'$.strength') AS REAL)
        ELSE dose_mg(json_extract(payload,'$.product_description')) END,
   per_day(json_extract(payload,'$.directions_text'))
 FROM record WHERE stream = 'medication_label_evidence') WHERE per_admin_mg IS NOT NULL)"""
