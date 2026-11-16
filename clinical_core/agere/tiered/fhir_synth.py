"""A synthetic Bulk FHIR export: what an EMR's $export would hand Agere, written from the synthetic warehouse.

An EMR gives an analytics product its data as a FHIR Bulk Data export: one NDJSON file per resource type (one JSON resource
per line) and a manifest. This module writes that export from the synthetic warehouse, so Agere's FHIR importer
(fhir_import.py) is exercised on exactly the format Epic, athenahealth or Oracle Health produce, and connecting a real EMR later
changes where the files come from, not the code that reads them.

What comes from the warehouse's own feeds: patients; hospital encounters (ED, observation, inpatient) with their location
history (ED, then the inpatient unit), arrival, decision-to-admit, admission and discharge times and disposition, and the
principal diagnosis; conditions on record; lab results with their times (glucose, creatinine, potassium, hemoglobin, eGFR,
lactate); the medication administration record (every administration timed, with route).

What the warehouse lacks and the hospital harm eCQMs need is generated, deterministically per encounter, and every such
resource carries meta.tag synthetic-augmented: insulin administrations with point-of-care glucose for patients with diabetes
(occasional severe hypoglycemia, some with a quick repeat over 80 mg/dL); opioid administrations and occasional opioid
antagonist rescue (some in the operating room, some enteral, some over 12 hours later, so each exclusion is exercised);
operating-room procedures and locations; serial creatinine with occasional acute kidney injury; present-on-admission flags;
hospital-acquired pressure injuries and falls with injury. These are test data for the measure logic, not clinical facts.

    python -m clinical_core.agere.tiered.fhir_synth          # writes artifacts/agere-tiered-core/fhir/export/
"""
import hashlib
import json
import random
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import store

OUT = Path('artifacts/agere-tiered-core/fhir/export')
AUG = {'system': 'http://agere.local/fhir/tag', 'code': 'synthetic-augmented'}
POA_URL = 'http://hl7.org/fhir/us/qicore/StructureDefinition/qicore-encounter-diagnosisPresentOnAdmission'
DECISION_URL = 'http://agere.local/fhir/StructureDefinition/decision-to-admit'
LOINC = {'glucose': ('2345-7', 'Glucose [Mass/volume] in Serum or Plasma', 'mg/dL'), 'creatinine': ('2160-0', 'Creatinine [Mass/volume] in Serum or Plasma', 'mg/dL'),
         'potassium': ('2823-3', 'Potassium [Moles/volume] in Serum or Plasma', 'mmol/L'), 'hemoglobin': ('718-7', 'Hemoglobin [Mass/volume] in Blood', 'g/dL'),
         'egfr': ('98979-8', 'Glomerular filtration rate/1.73 sq M.predicted by CKD-EPI 2021', 'mL/min/1.73m2'), 'lactate': ('2524-7', 'Lactate [Moles/volume] in Serum or Plasma', 'mmol/L')}
POC_GLUCOSE = ('41653-7', 'Glucose [Mass/volume] in Capillary blood by Glucometer', 'mg/dL')
DRUGS = {'insulin': ('insulin lispro', '86009', 'subcutaneous', 4, 'U'), 'morphine': ('morphine sulfate', '7052', 'intravenous', 2, 'mg'),
         'oxycodone': ('oxycodone hydrochloride', '7804', 'oral', 5, 'mg'), 'fentanyl': ('fentanyl citrate', '4337', 'intravenous', 50, 'ug'),
         'naloxone': ('naloxone hydrochloride', '7242', 'intravenous', 0.4, 'mg')}
LOCATIONS = {'ED': ('Emergency Department', 'ED'), 'MED': ('Medical Unit', 'MED'), 'OBS': ('Observation Unit', 'OBS'), 'OR': ('Operating Room', 'OR'),
             'ICU': ('Intensive Care Unit', 'ICU')}


def _ts(x):
    return datetime.fromisoformat(str(x)[:16]) if x and len(str(x)) >= 16 else None


def _iso(t):
    return t.strftime('%Y-%m-%dT%H:%M:00')


def _rng(*key):
    return random.Random(int(hashlib.sha256('|'.join(map(str, key)).encode()).hexdigest()[:12], 16))


class Writer:
    def __init__(self, out):
        self.out = out; self.files = {}; self.counts = {}
        out.mkdir(parents=True, exist_ok=True)
        for f in out.glob('*.ndjson'): f.unlink()

    def add(self, resource):
        t = resource['resourceType']
        if t not in self.files: self.files[t] = open(self.out / f'{t}.ndjson', 'w')
        self.files[t].write(json.dumps(resource, separators=(',', ':')) + '\n')
        self.counts[t] = self.counts.get(t, 0) + 1

    def close(self, as_of):
        for f in self.files.values(): f.close()
        manifest = {'transactionTime': as_of + 'T00:00:00Z', 'request': '[base]/Group/synthetic/$export', 'requiresAccessToken': False,
                    'output': [{'type': t, 'url': f'{t}.ndjson', 'count': n} for t, n in sorted(self.counts.items())], 'error': [],
                    'extension': {'synthetic': True, 'note': 'Synthetic test data; resources tagged synthetic-augmented were generated to exercise measure logic.'}}
        (self.out / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
        return self.counts


def _admin(w, rid, patient, enc, drug, when, route=None, dose=None, augmented=True):
    name, rxcui, default_route, default_dose, unit = DRUGS[drug]
    w.add({'resourceType': 'MedicationAdministration', 'id': rid, 'status': 'completed', **({'meta': {'tag': [AUG]}} if augmented else {}),
           'medicationCodeableConcept': {'coding': [{'system': 'http://www.nlm.nih.gov/research/umls/rxnorm', 'code': rxcui, 'display': name}], 'text': name},
           'subject': {'reference': f'Patient/{patient}'}, 'context': {'reference': f'Encounter/{enc}'}, 'effectiveDateTime': _iso(when),
           'dosage': {'route': {'text': route or default_route}, 'dose': {'value': dose or default_dose, 'unit': unit}}})


def _obs(w, rid, patient, enc, code, when, value, augmented=True):
    loinc, display, unit = code
    w.add({'resourceType': 'Observation', 'id': rid, 'status': 'final', **({'meta': {'tag': [AUG]}} if augmented else {}),
           'category': [{'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/observation-category', 'code': 'laboratory'}]}],
           'code': {'coding': [{'system': 'http://loinc.org', 'code': loinc, 'display': display}]}, 'subject': {'reference': f'Patient/{patient}'},
           **({'encounter': {'reference': f'Encounter/{enc}'}} if enc else {}), 'effectiveDateTime': when if isinstance(when, str) else _iso(when),
           'valueQuantity': {'value': round(value, 2), 'unit': unit}})


def build(out=OUT):
    src = sqlite3.connect(Path(store.DEFAULT_DB).resolve().as_uri() + '?mode=ro', uri=True)
    q = lambda s, a=(): src.execute(s, a).fetchall()
    as_of = q("SELECT value FROM meta WHERE key = 'as_of'")[0][0][:10]
    w = Writer(out)
    for code, (name, _) in LOCATIONS.items():
        w.add({'resourceType': 'Location', 'id': code, 'name': name, 'physicalType': {'text': code},
               'type': [{'text': {'ED': 'emergency', 'MED': 'inpatient ward', 'OBS': 'observation', 'OR': 'operating room', 'ICU': 'intensive care'}[code]}]})
    sex = {}
    for pid, birth, s in q('SELECT patient_id, birth_date, sex_reported FROM patient'):
        sex[pid] = s
        w.add({'resourceType': 'Patient', 'id': pid, 'identifier': [{'system': 'http://agere.local/mrn', 'value': pid}],
               'gender': {'F': 'female', 'M': 'male'}.get(s, 'unknown'), **({'birthDate': birth} if birth else {})})
    diabetic = {p for (p,) in q("SELECT DISTINCT patient_id FROM condition WHERE concept IN ('diabetes', 'type_2_diabetes', 'type_1_diabetes')")}
    for rid, pid, concept, icd, onset in q("SELECT record_id, patient_id, concept, icd10, COALESCE(onset, event_date) FROM condition WHERE icd10 IS NOT NULL"):
        w.add({'resourceType': 'Condition', 'id': rid, 'subject': {'reference': f'Patient/{pid}'},
               'category': [{'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/condition-category', 'code': 'problem-list-item'}]}],
               'code': {'coding': [{'system': 'http://hl7.org/fhir/sid/icd-10-cm', 'code': icd}], 'text': concept}, **({'onsetDateTime': str(onset)[:10]} if onset else {})})

    visits = q('SELECT visit_group_id, patient_id, visit_class, has_ed, has_inpatient, has_observation, start_date, end_date, ed_arrival, ed_departure, '
               'admit_decision, inpatient_admit, discharge_time, time_precision, disposition, principal_icd10, unit FROM visit_group')
    encounter_of = {}
    for (vid, pid, vclass, has_ed, has_ip, has_obs, start, end, arr, dep, decision, admit, discharge, prec, disp, dx, unit) in visits:
        minute = prec == 'minute'
        t_arr, t_dep, t_admit, t_dis = (_ts(x) if minute else None for x in (arr, dep, admit, discharge))
        if has_ip and t_admit and (t_dep is None or t_dep > t_admit): t_dep = t_admit   # the ED stay ends when the patient reaches the unit
        begin = t_arr or t_admit
        finish = t_dis or (t_dep if not has_ip else None)
        period = {'start': _iso(begin) if begin else str(start)[:10]}
        if finish: period['end'] = _iso(finish)
        elif end: period['end'] = str(end)[:10]
        locs = []
        if has_ed and t_arr:
            locs.append({'location': {'reference': 'Location/ED'}, 'status': 'completed', 'period': {'start': _iso(t_arr), **({'end': _iso(t_dep)} if t_dep else {})}})
        if has_ip and t_admit:
            locs.append({'location': {'reference': f"Location/{unit if unit in ('MED', 'ICU') else 'MED'}"}, 'status': 'completed',
                         'period': {'start': _iso(t_admit), **({'end': _iso(t_dis)} if t_dis else {})}})
        elif has_obs and t_dep:
            locs.append({'location': {'reference': 'Location/OBS'}, 'status': 'completed', 'period': {'start': _iso(t_dep)}})
        rng = _rng('enc', vid)
        diagnoses = []
        if dx:
            diagnoses.append(({'code': dx, 'rank': 1, 'poa': 'Y', 'augmented': False}))
        procs, augmented_admins, augmented_obs, or_period = [], [], [], None
        if has_ip and t_admit and t_dis and t_dis > t_admit + timedelta(hours=6):
            stay_h = (t_dis - t_admit).total_seconds() / 3600
            # -- surgery in the operating room on the first day (20% of stays)
            if rng.random() < 0.2:
                s0 = t_admit + timedelta(hours=rng.randint(3, 12)); s1 = s0 + timedelta(minutes=rng.choice((90, 120, 180)))
                if s1 < t_dis:
                    or_period = (s0, s1)
                    procs.append({'name': rng.choice(('Total knee arthroplasty', 'Laparoscopic cholecystectomy', 'Open reduction internal fixation of hip')), 'start': s0, 'end': s1})
                    augmented_admins.append(('fentanyl', s0 + timedelta(minutes=10), 'intravenous', None))
                    if rng.random() < 0.15: augmented_admins.append(('naloxone', s1 - timedelta(minutes=15), 'intravenous', None))   # in the OR: excluded
            # -- insulin and point-of-care glucose for patients with diabetes
            if pid in diabetic:
                t = t_admit + timedelta(hours=2)
                hypo_done = rng.random() >= 0.03   # about 3% of insulin stays have one severe low
                hyper_stay = rng.random() < 0.15
                while t < t_dis - timedelta(hours=1):
                    augmented_obs.append((POC_GLUCOSE, t - timedelta(minutes=30), min(450, max(70, rng.gauss(170, 45)))))
                    augmented_admins.append(('insulin', t, 'subcutaneous', rng.choice((2, 4, 6, 8))))
                    if not hypo_done and rng.random() < 0.5:
                        low = t + timedelta(hours=rng.randint(2, 20))
                        if low < t_dis:
                            augmented_obs.append((POC_GLUCOSE, low, rng.choice((32, 36, 38))))
                            if rng.random() < 0.3: augmented_obs.append((POC_GLUCOSE, low + timedelta(minutes=4), rng.choice((92, 104))))   # repeat over 80: excluded
                            hypo_done = True
                    if hyper_stay and rng.random() < 0.15: augmented_obs.append((POC_GLUCOSE, t + timedelta(hours=3), rng.choice((320, 345, 380))))
                    t += timedelta(hours=6)
            # -- opioids and opioid antagonist rescue (35% of stays)
            if rng.random() < 0.35:
                drug = rng.choice(('morphine', 'oxycodone'))
                t = t_admit + timedelta(hours=1); last = None
                while t < t_dis - timedelta(hours=1) and t < t_admit + timedelta(days=3):
                    if not (or_period and or_period[0] <= t <= or_period[1]): augmented_admins.append((drug, t, None, None)); last = t
                    t += timedelta(hours=8)
                r = rng.random()
                if last and r < 0.03:
                    augmented_admins.append(('naloxone', last + timedelta(hours=rng.randint(1, 10)), rng.choice(('intravenous', 'intravenous', 'intranasal')), None))
                elif last and r < 0.05:
                    augmented_admins.append(('naloxone', last + timedelta(hours=14), 'intravenous', None))   # over 12 hours later: excluded
                elif last and r < 0.06:
                    augmented_admins.append(('naloxone', last + timedelta(hours=2), 'oral', None))   # enteral: excluded
            # -- creatinine: twice in the first day, then daily; occasional acute kidney injury
            if stay_h >= 48:
                base = rng.uniform(0.6, 0.95) if sex.get(pid) == 'F' else rng.uniform(0.7, 1.1)
                early_rise = rng.random() < 0.03
                aki = (not early_rise) and rng.random() < 0.04
                times = [t_admit + timedelta(hours=1), t_admit + timedelta(hours=20)]
                d = (t_admit + timedelta(days=1)).replace(hour=6, minute=0)
                while d < t_dis: times.append(d); d += timedelta(days=1)
                for k, t in enumerate(sorted(set(times))):
                    h = (t - t_admit).total_seconds() / 3600
                    v = base * rng.uniform(0.95, 1.05)
                    if early_rise and 10 <= h <= 47: v = base + 0.4
                    if aki and h >= 60: v = base * rng.uniform(2.1, 2.6)
                    augmented_obs.append((LOINC['creatinine'], t, v))
            # -- hospital-acquired pressure injuries and falls with injury, and conditions present on admission
            if stay_h >= 72:
                r = rng.random()
                if r < 0.015: diagnoses.append({'code': 'L89.152', 'rank': 2, 'poa': 'N', 'augmented': True})
                elif r < 0.03: diagnoses.append({'code': 'L89.153', 'rank': 2, 'poa': 'Y', 'augmented': True})
            r = rng.random()
            if r < 0.005: diagnoses += [{'code': 'W19.XXXA', 'rank': 3, 'poa': 'N', 'augmented': True}, {'code': 'S72.001A', 'rank': 4, 'poa': 'N', 'augmented': True}]
            elif r < 0.01: diagnoses.append({'code': 'W19.XXXA', 'rank': 3, 'poa': 'N', 'augmented': True})
        enc = {'resourceType': 'Encounter', 'id': vid, 'status': 'finished',
               'class': {'system': 'http://terminology.hl7.org/CodeSystem/v3-ActCode', 'code': 'IMP' if has_ip else 'OBSENC' if has_obs else 'EMER'},
               'subject': {'reference': f'Patient/{pid}'}, 'period': period, 'location': locs,
               **({'hospitalization': {'dischargeDisposition': {'text': disp}}} if disp else {}),
               **({'extension': [{'url': DECISION_URL, 'valueDateTime': _iso(_ts(decision))}]} if decision and minute and _ts(decision) else {})}
        if or_period:
            enc['location'].append({'location': {'reference': 'Location/OR'}, 'status': 'completed', 'period': {'start': _iso(or_period[0]), 'end': _iso(or_period[1])},
                                    'extension': [{'url': AUG['system'], 'valueCode': AUG['code']}]})
        if diagnoses:
            enc['diagnosis'] = []
            for k, dgx in enumerate(diagnoses):
                cid = f'{vid}-dx{k}'
                w.add({'resourceType': 'Condition', 'id': cid, **({'meta': {'tag': [AUG]}} if dgx['augmented'] else {}), 'subject': {'reference': f'Patient/{pid}'},
                       'category': [{'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/condition-category', 'code': 'encounter-diagnosis'}]}],
                       'encounter': {'reference': f'Encounter/{vid}'}, 'code': {'coding': [{'system': 'http://hl7.org/fhir/sid/icd-10-cm', 'code': dgx['code']}]}})
                enc['diagnosis'].append({'condition': {'reference': f'Condition/{cid}'}, 'rank': dgx['rank'],
                                         'extension': [{'url': POA_URL, 'valueCodeableConcept': {'coding': [{'code': dgx['poa']}]}}]})
        w.add(enc)
        encounter_of[vid] = (pid, begin, finish)
        for k, (drug, when, route, dose) in enumerate(augmented_admins):
            _admin(w, f'{vid}-ma{k}', pid, vid, drug, when, route, dose)
        for k, (code, when, value) in enumerate(augmented_obs):
            _obs(w, f'{vid}-ob{k}', pid, vid, code, when, value)
        for k, p in enumerate(procs):
            w.add({'resourceType': 'Procedure', 'id': f'{vid}-pr{k}', 'status': 'completed', 'meta': {'tag': [AUG]}, 'subject': {'reference': f'Patient/{pid}'},
                   'encounter': {'reference': f'Encounter/{vid}'}, 'code': {'text': p['name']}, 'location': {'reference': 'Location/OR'},
                   'performedPeriod': {'start': _iso(p['start']), 'end': _iso(p['end'])}})
    # the warehouse's own lab results and medication administration record
    for rid, pid, concept, value, day, t in q("SELECT record_id, patient_id, concept, value_canonical, event_date, event_time FROM measurement WHERE concept IN "
                                              "('glucose', 'creatinine', 'potassium', 'hemoglobin', 'egfr', 'lactate') AND value_canonical IS NOT NULL AND event_date IS NOT NULL"):
        _obs(w, rid, pid, None, LOINC[concept], (t[:16] + ':00') if t else str(day)[:10], value, augmented=False)
    for (payload,) in q("SELECT payload FROM record WHERE stream = 'medication_administrations'"):
        d = json.loads(payload)
        if d.get('administration_status') != 'given' or not d.get('event_time'): continue
        w.add({'resourceType': 'MedicationAdministration', 'id': d['record_id'], 'status': 'completed',
               'medicationCodeableConcept': {'coding': [{'system': 'http://www.nlm.nih.gov/research/umls/rxnorm', 'code': str(d.get('rxcui') or ''), 'display': d.get('medication_name')}],
                                             'text': d.get('medication_name')},
               'subject': {'reference': f"Patient/{d['patient_id']}"}, 'effectiveDateTime': d['event_time'][:16] + ':00',
               'dosage': {'route': {'text': d.get('route')}, **({'dose': {'value': d['dose_given'], 'unit': d.get('dose_unit')}} if d.get('dose_given') is not None else {})}})
    return w.close(as_of)


if __name__ == '__main__':
    print(json.dumps(build(), indent=1))
