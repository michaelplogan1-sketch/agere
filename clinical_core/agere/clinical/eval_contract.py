"""Prediction-file contract of the sealed healthcare-warehouse evaluation scorer (v2.8 EVAL_SCORER.md), as a validator.

JSON lines, one directory, every file optional:
  conditions.jsonl       {patient_id, icd10, basis?: documented|suspect}
  hcc.jsonl              {patient_id, year, hccs: [int | 'HCC<n>']}            CMS-HCC V28
  quality_measures.jsonl {patient_id, year, measure, eligible, met, pdc?}     measure in MEASURES
  acute_events.jsonl     {patient_id, kind: ed|inpatient|observation, start: YYYY-MM-DD, unplanned_readmission_30d?, planned?}
  medications.jsonl      {patient_id, drug_class, year, pdc, therapy_event?: initiation|switch|add_on|restart, first_fill?}
  deaths.jsonl           {patient_id, date_of_death}
  duplicates.jsonl       {patient_id, person_id}
  sdoh.jsonl             {patient_id, needs: [transport|food|housing|work_schedule_constraint]}
"""
from datetime import date
from pathlib import Path
import json, re

VERSION = 'eval-contract/v2.8'
MEASURES = ('BCS', 'COL', 'KED', 'SPD', 'HBD', 'CBP', 'PDC-DR', 'PDC-RASA', 'PDC-STA')
KINDS = ('ed', 'inpatient', 'observation')
THERAPY_EVENTS = ('initiation', 'switch', 'add_on', 'restart')
NEEDS = ('transport', 'food', 'housing', 'work_schedule_constraint')
FILES = ('conditions', 'hcc', 'quality_measures', 'acute_events', 'medications', 'deaths', 'duplicates', 'sdoh')
ICD10 = re.compile(r'^[A-Z][0-9][0-9A-Z](\.?[0-9A-Z]{1,4})?$')
HCC = re.compile(r'^HCC\d{1,3}$')
KEYS = {'conditions': ('patient_id', 'icd10'), 'hcc': ('patient_id', 'year'), 'quality_measures': ('patient_id', 'year', 'measure'),
        'acute_events': ('patient_id', 'kind', 'start'), 'medications': ('patient_id', 'drug_class', 'year'), 'deaths': ('patient_id',),
        'duplicates': ('patient_id',), 'sdoh': ('patient_id',)}


def _date(v):
    if not isinstance(v, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', v): return False
    try: date.fromisoformat(v); return True
    except ValueError: return False


def _year(v): return isinstance(v, int) and not isinstance(v, bool) and 1900 <= v <= 2100


def _unit(v): return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1


def check_row(name, r):
    """List of problems with one row (empty = valid)."""
    if not isinstance(r, dict): return ['row is not an object']
    errs = []
    if not isinstance(r.get('patient_id'), str) or not r['patient_id']: errs.append('patient_id must be a non-empty string')
    if name == 'conditions':
        if not isinstance(r.get('icd10'), str) or not ICD10.match(r['icd10']): errs.append(f'icd10 {r.get("icd10")!r} is not an ICD-10-CM code')
        if 'basis' in r and r['basis'] not in ('documented', 'suspect'): errs.append('basis must be documented or suspect')
    elif name == 'hcc':
        if not _year(r.get('year')): errs.append('year must be an integer year')
        h = r.get('hccs')
        if not isinstance(h, list) or not all((isinstance(x, int) and not isinstance(x, bool)) or (isinstance(x, str) and HCC.match(x)) for x in h):
            errs.append('hccs must be a list of integers or "HCC<n>" strings')
    elif name == 'quality_measures':
        if not _year(r.get('year')): errs.append('year must be an integer year')
        if r.get('measure') not in MEASURES: errs.append(f'measure {r.get("measure")!r} not in {MEASURES}')
        for k in ('eligible', 'met'):
            if not isinstance(r.get(k), bool): errs.append(f'{k} must be true/false')
        if 'pdc' in r and not _unit(r['pdc']): errs.append('pdc must be 0-1')
        if 'pdc' in r and not str(r.get('measure', '')).startswith('PDC-'): errs.append('pdc only on PDC measures')
    elif name == 'acute_events':
        if r.get('kind') not in KINDS: errs.append(f'kind must be one of {KINDS}')
        if not _date(r.get('start')): errs.append('start must be YYYY-MM-DD')
        for k in ('unplanned_readmission_30d', 'planned'):
            if k in r and not isinstance(r[k], bool): errs.append(f'{k} must be true/false')
    elif name == 'medications':
        if not isinstance(r.get('drug_class'), str) or not r['drug_class']: errs.append('drug_class must be a non-empty string')
        if not _year(r.get('year')): errs.append('year must be an integer year')
        if not _unit(r.get('pdc')): errs.append('pdc must be 0-1')
        if 'therapy_event' in r and r['therapy_event'] not in THERAPY_EVENTS: errs.append(f'therapy_event must be one of {THERAPY_EVENTS}')
        if 'first_fill' in r and not _date(r['first_fill']): errs.append('first_fill must be YYYY-MM-DD')
        if 'first_fill' in r and 'therapy_event' not in r: errs.append('first_fill belongs with a therapy_event')
    elif name == 'deaths':
        if not _date(r.get('date_of_death')): errs.append('date_of_death must be YYYY-MM-DD')
    elif name == 'duplicates':
        if not isinstance(r.get('person_id'), str) or not r['person_id']: errs.append('person_id must be a non-empty string')
    elif name == 'sdoh':
        n = r.get('needs')
        if not isinstance(n, list) or not n or any(x not in NEEDS for x in n): errs.append(f'needs must be a non-empty list from {NEEDS}')
    return errs


def validate_dir(directory, patient_ids=None, max_errors=20):
    """{'files': {name: {'rows', 'errors', 'duplicate_keys', 'unknown_patients'}}, 'ok': bool, 'unexpected_files': [...]}"""
    d = Path(directory)
    report, ok = {}, True
    unexpected = sorted(p.name for p in d.glob('*.jsonl') if p.stem not in FILES)
    for name in FILES:
        f = d / f'{name}.jsonl'
        if not f.exists(): continue
        rows, errors, seen, dup, unknown = 0, [], set(), 0, 0
        with f.open() as fh:
            for i, line in enumerate(fh, 1):
                if not line.strip(): continue
                rows += 1
                try: r = json.loads(line)
                except ValueError: errors.append(f'line {i}: not JSON'); continue
                for e in check_row(name, r): errors.append(f'line {i}: {e}')
                if isinstance(r, dict):
                    key = tuple(json.dumps(r.get(k)) for k in KEYS[name])
                    if key in seen: dup += 1
                    seen.add(key)
                    if patient_ids is not None and r.get('patient_id') not in patient_ids: unknown += 1
        if errors or dup or unknown: ok = False
        report[name] = {'rows': rows, 'errors': errors[:max_errors], 'error_count': len(errors), 'duplicate_keys': dup, 'unknown_patients': unknown}
    return {'version': VERSION, 'ok': ok and not unexpected, 'files': report, 'unexpected_files': unexpected}
