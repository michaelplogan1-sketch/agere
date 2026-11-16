"""Read a FHIR Bulk Data export (NDJSON per resource type, with its manifest) into an Agere semantic layer.

This is how Agere sits on an EMR: the EMR's $export (Epic, athenahealth, Oracle Health and other US Core servers) produces the
same files fhir_synth.py writes for testing. The importer reads Patient, Encounter (class, period, location history,
discharge disposition, diagnoses with present-on-admission), Condition, Observation (LOINC-coded, timed), MedicationAdministration
(RxNorm-coded, timed, with route) and Procedure, and writes the semantic-layer tables the core reads, so every governed view,
definition, check and recipe applies. Tables it adds for the hospital harm measures:
  medication_administration  one row per administration: time, drug, class, route, whether the route is enteral, and the
                             location the patient was in at that moment (from the encounter's location history)
  encounter_location         the location history: one row per encounter and location period
  encounter_diagnosis        one row per encounter diagnosis, with rank and present-on-admission (Y, N, U, W)
  procedure_event            procedures with their performed period and location

Drug classes are assigned from the RxNorm code or, failing that, the name (a short governed list below); a real deployment
would use the measure value sets (VSAC). Nothing is read from the original warehouse.

    python -m clinical_core.agere.tiered.fhir_import [export folder] [output .sqlite3]
"""
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

EXPORT = Path('artifacts/agere-tiered-core/fhir/export')
OUT = Path('artifacts/agere-tiered-core/fhir/fhir_semantic.sqlite3')
POA_URL = 'http://hl7.org/fhir/us/qicore/StructureDefinition/qicore-encounter-diagnosisPresentOnAdmission'
DECISION_URL = 'http://agere.local/fhir/StructureDefinition/decision-to-admit'
LOINC_CONCEPT = {'2345-7': 'glucose', '41653-7': 'glucose', '2339-0': 'glucose', '2160-0': 'creatinine', '2823-3': 'potassium', '718-7': 'hemoglobin',
                 '98979-8': 'egfr', '2524-7': 'lactate', '32693-4': 'lactate'}
POC_LOINC = {'41653-7'}
DRUG_CLASS = {'86009': 'insulin', '7052': 'opioid', '7804': 'opioid', '4337': 'opioid', '3423': 'opioid', '7242': 'opioid_antagonist', '31479': 'opioid_antagonist'}
NAME_CLASS = ((r'insulin', 'insulin'), (r'glipizide|glyburide|glimepiride', 'sulfonylurea'), (r'morphine|oxycodone|hydromorphone|fentanyl|hydrocodone|meperidine|methadone|tramadol', 'opioid'),
              (r'naloxone|nalmefene', 'opioid_antagonist'), (r'furosemide|bumetanide|torsemide', 'loop_diuretic'), (r'ceftriaxone|cefazolin|cephalexin', 'cephalosporin'),
              (r'azithromycin', 'macrolide'), (r'doxycycline', 'tetracycline_antibiotic'), (r'albuterol', 'short_acting_beta_agonist'), (r'acetaminophen', 'acetaminophen'),
              (r'prednis', 'systemic_corticosteroid'))
ENTERAL = {'oral', 'enteral', 'nasogastric', 'gastrostomy', 'sublingual', 'buccal', 'rectal', 'po'}


def _ref(x):
    return str(x or '').split('/')[-1] or None


def _code(cc, system=None):
    for c in (cc or {}).get('coding', []):
        if system is None or c.get('system') == system: return c.get('code')
    return None


def _date(x):
    return str(x)[:10] if x else None


def _minute(x):
    return str(x)[:16] if x and len(str(x)) >= 16 else None


def _ndjson(folder, kind):
    path = Path(folder) / f'{kind}.ndjson'
    if not path.exists(): return
    with open(path) as f:
        for line in f:
            if line.strip(): yield json.loads(line)


def build(folder=EXPORT, out=OUT):
    folder, out = Path(folder), Path(out)
    manifest = json.loads((folder / 'manifest.json').read_text())
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists(): out.unlink()
    db = sqlite3.connect(out)
    x = db.execute
    x('CREATE TABLE meta(key TEXT, value TEXT)')
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    db.executemany('INSERT INTO meta VALUES (?, ?)', [('version', 'fhir-import/1'), ('source_digest', digest), ('as_of', manifest['transactionTime'][:10]),
                                                       ('source', 'FHIR Bulk Data export: ' + str(folder))])
    x('CREATE TABLE patient(patient_id TEXT PRIMARY KEY, sex_reported TEXT, birth_date TEXT)')
    for r in _ndjson(folder, 'Patient'):
        x('INSERT INTO patient VALUES (?,?,?)', (r['id'], {'female': 'F', 'male': 'M'}.get(r.get('gender')), r.get('birthDate')))
    locations = {r['id']: (r.get('physicalType') or {}).get('text') or r['id'] for r in _ndjson(folder, 'Location')}
    x('CREATE TABLE condition(record_id TEXT, patient_id TEXT, concept TEXT, icd10 TEXT, event_date TEXT, category TEXT, encounter_id TEXT)')
    conditions = {}
    for r in _ndjson(folder, 'Condition'):
        icd = _code(r.get('code'), 'http://hl7.org/fhir/sid/icd-10-cm')
        cat = _code((r.get('category') or [{}])[0])
        conditions[r['id']] = icd
        x('INSERT INTO condition VALUES (?,?,?,?,?,?,?)', (r['id'], _ref(r['subject']['reference']), (r.get('code') or {}).get('text'), icd,
                                                            _date(r.get('onsetDateTime')), cat, _ref((r.get('encounter') or {}).get('reference'))))
    x('CREATE TABLE encounter_location(encounter_id TEXT, patient_id TEXT, location_type TEXT, start TEXT, end TEXT)')
    x('CREATE TABLE encounter_diagnosis(encounter_id TEXT, patient_id TEXT, rank INTEGER, icd10 TEXT, poa TEXT)')
    x('CREATE TABLE visit_group(visit_group_id TEXT PRIMARY KEY, patient_id TEXT, organization_id TEXT, visit_class TEXT, has_ed INTEGER, has_inpatient INTEGER, '
      'has_observation INTEGER, start_date TEXT, end_date TEXT, ed_arrival TEXT, ed_departure TEXT, admit_decision TEXT, inpatient_admit TEXT, discharge_time TEXT, '
      'time_precision TEXT, disposition TEXT, principal_icd10 TEXT, unit TEXT, los_days REAL, discharge_known INTEGER)')
    x('CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT, organization_id TEXT, discharge_known INTEGER)')
    x('CREATE TABLE utilization_event(patient_id TEXT, kind TEXT, event_date TEXT, end_date TEXT, discharge_known INTEGER, organization_id TEXT)')
    encounters = {}
    for r in _ndjson(folder, 'Encounter'):
        eid, pid, cls = r['id'], _ref(r['subject']['reference']), (r.get('class') or {}).get('code')
        period = r.get('period') or {}
        locs = [(locations.get(_ref(l['location']['reference']), _ref(l['location']['reference'])), (l.get('period') or {}).get('start'), (l.get('period') or {}).get('end'))
                for l in r.get('location') or []]
        for typ, s, e in locs: x('INSERT INTO encounter_location VALUES (?,?,?,?,?)', (eid, pid, typ, s, e))
        dxs = []
        for d in r.get('diagnosis') or []:
            poa = next((_code(e.get('valueCodeableConcept')) for e in d.get('extension') or [] if e.get('url') == POA_URL), None)
            icd = conditions.get(_ref(d['condition']['reference']))
            dxs.append((d.get('rank') or 99, icd, poa))
            x('INSERT INTO encounter_diagnosis VALUES (?,?,?,?,?)', (eid, pid, d.get('rank'), icd, poa))
        ed = next(((s, e) for t, s, e in locs if t == 'ED'), (None, None))
        unit = next(((t, s, e) for t, s, e in locs if t in ('MED', 'ICU')), (None, None, None))
        obs = any(t == 'OBS' for t, _, _ in locs) or cls == 'OBSENC'
        has_ip = int(cls == 'IMP'); has_ed = int(ed[0] is not None or cls == 'EMER'); has_obs = int(obs and not has_ip)
        decision = next((e.get('valueDateTime') for e in r.get('extension') or [] if e.get('url') == DECISION_URL), None)
        start, end = period.get('start'), period.get('end')
        minute = bool(_minute(start))
        principal = next((icd for rank, icd, _ in sorted(dxs, key=lambda d: d[0]) if rank == 1), None)
        disp = ((r.get('hospitalization') or {}).get('dischargeDisposition') or {}).get('text')
        discharge = unit[2] if has_ip else None
        los = None
        if has_ip and unit[1] and unit[2]:
            los = round((datetime.fromisoformat(unit[2][:16]) - datetime.fromisoformat(unit[1][:16])).total_seconds() / 86400, 2)
        vclass = ('ed_to_inpatient' if has_ed and has_ip else 'direct_inpatient' if has_ip else 'ed_to_observation' if has_ed and has_obs else
                  'observation' if has_obs else 'emergency')
        x('INSERT INTO visit_group VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (eid, pid, None, vclass, has_ed, has_ip, has_obs, _date(start), _date(end), _minute(ed[0]), _minute(ed[1]), _minute(decision), _minute(unit[1]),
           _minute(discharge), 'minute' if minute else 'day', disp, principal, unit[0] if has_ip else ('OBS' if has_obs else ('ED' if has_ed else None)), los,
           int(bool(end))))
        if has_ip:
            x('INSERT INTO stay VALUES (?,?,?,?,?,?)', (pid, _date(unit[1] or start), _date(unit[2] or end), 'inpatient', None, int(bool(unit[2] or end))))
        if has_obs:
            x('INSERT INTO stay VALUES (?,?,?,?,?,?)', (pid, _date(start), _date(end), 'observation', None, int(bool(end))))
        for kind, flag in (('ed_visit', has_ed), ('inpatient', has_ip), ('observation', has_obs)):
            if flag: x('INSERT INTO utilization_event VALUES (?,?,?,?,?,?)', (pid, kind, _date(ed[0] if kind == 'ed_visit' and ed[0] else start), _date(end), int(bool(end)), None))
        encounters[eid] = (pid, locs, start, end)
    by_patient = {}
    for eid, (pid, locs, start, end) in encounters.items(): by_patient.setdefault(pid, []).append((eid, locs, start, end))

    def where(pid, t, eid=None):
        """The encounter and the location the patient was in at time t."""
        candidates = [e for e in by_patient.get(pid, []) if eid is None or e[0] == eid]
        for e, locs, start, end in candidates:
            # periods can nest (an operating-room period inside the unit's): the latest-starting period that contains t is the most specific
            inside = [(s[:16], typ) for typ, s, f in locs if s and _minute(s) and t >= s[:16] and (not f or t <= f[:16])]
            if inside: return e, max(inside)[1]
        for e, locs, start, end in candidates:
            if start and t[:10] >= start[:10] and (not end or t[:10] <= end[:10]): return e, None
        return (eid, None)

    x('CREATE TABLE measurement(record_id TEXT, patient_id TEXT, concept TEXT, name TEXT, value_canonical REAL, unit_canonical TEXT, event_date TEXT, event_time TEXT, '
      'encounter_id TEXT, point_of_care INTEGER)')
    for r in _ndjson(folder, 'Observation'):
        loinc = _code(r.get('code'), 'http://loinc.org')
        when = r.get('effectiveDateTime')
        q = r.get('valueQuantity') or {}
        x('INSERT INTO measurement VALUES (?,?,?,?,?,?,?,?,?,?)', (r['id'], _ref(r['subject']['reference']), LOINC_CONCEPT.get(loinc),
                                                                   ((r.get('code') or {}).get('coding') or [{}])[0].get('display'), q.get('value'), q.get('unit'), _date(when),
                                                                   _minute(when), _ref((r.get('encounter') or {}).get('reference')), int(loinc in POC_LOINC)))
    x('CREATE TABLE medication_administration(record_id TEXT, patient_id TEXT, encounter_id TEXT, administered_at TEXT, drug_name TEXT, rxnorm TEXT, drug_class TEXT, '
      'route TEXT, enteral INTEGER, location_type TEXT, dose REAL, dose_unit TEXT)')
    for r in _ndjson(folder, 'MedicationAdministration'):
        med = r.get('medicationCodeableConcept') or {}
        rx = _code(med, 'http://www.nlm.nih.gov/research/umls/rxnorm')
        name = med.get('text') or ((med.get('coding') or [{}])[0].get('display'))
        klass = DRUG_CLASS.get(rx) or next((c for pat, c in NAME_CLASS if re.search(pat, str(name or ''), re.I)), None)
        when = _minute(r.get('effectiveDateTime') or (r.get('effectivePeriod') or {}).get('start'))
        pid = _ref(r['subject']['reference'])
        route = (((r.get('dosage') or {}).get('route')) or {}).get('text')
        eid, loc = where(pid, when, _ref((r.get('context') or {}).get('reference'))) if when else (_ref((r.get('context') or {}).get('reference')), None)
        dose = (r.get('dosage') or {}).get('dose') or {}
        x('INSERT INTO medication_administration VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
          (r['id'], pid, eid, when, name, rx, klass, route, int(str(route or '').lower() in ENTERAL), loc, dose.get('value'), dose.get('unit')))
    x('CREATE TABLE procedure_event(record_id TEXT, patient_id TEXT, encounter_id TEXT, name TEXT, start TEXT, end TEXT, location_type TEXT)')
    for r in _ndjson(folder, 'Procedure'):
        p = r.get('performedPeriod') or {}
        x('INSERT INTO procedure_event VALUES (?,?,?,?,?,?,?)', (r['id'], _ref(r['subject']['reference']), _ref((r.get('encounter') or {}).get('reference')),
                                                                 (r.get('code') or {}).get('text'), _minute(p.get('start')), _minute(p.get('end')),
                                                                 locations.get(_ref((r.get('location') or {}).get('reference')))))
    for idx in ('CREATE INDEX ma_p ON medication_administration(patient_id, administered_at)', 'CREATE INDEX m_p ON measurement(patient_id, concept, event_time)',
                'CREATE INDEX ed_e ON encounter_diagnosis(encounter_id)', 'CREATE INDEX el_e ON encounter_location(encounter_id)', 'CREATE INDEX s_p ON stay(patient_id)'):
        x(idx)
    db.commit()
    counts = {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    db.close()
    return counts


if __name__ == '__main__':
    print(json.dumps(build(*(sys.argv[1:3])), indent=1))
