"""Bounded patient-level HF review, composed from existing source and program rules.

Returns documentary findings, not a prescribing verdict. Unsupported question
constraints decline to the general planner instead of disappearing into this route.
"""
from datetime import date
import json
import re
import time
from ..plans import router, catalog
from . import clinical_inputs

VERSION = 'patient-therapy-review/2'


def accepts(question):
    q = question.lower().replace('heartfailure', 'heart failure')
    if not re.search(r'\b(?:hfref|heart failure)\b', q) or not re.search(r'\b(?:gdmt|dose|doses|titration)\b', q):
        return False
    if not re.search(r'\b(?:hfref|reduced ef|reduced ejection fraction)\b', q):
        return False
    if not re.search(r'\b(?:flag|review|assess|assessment)\b', q):
        return False
    if not re.search(r"\b(?:not|missing|aren'?t|aren t|isn'?t|isn t)\b", q):
        return False  # This tool cannot select a positive/full-treatment cohort.
    # A bounded family, never a loose keyword match that swallows payer/date/age filters.
    words = set(re.findall(r"[a-z0-9]+", q))
    allowed = set('find every all patient patients with heart failure hfref and reduced ef ejection fraction flag which who arent aren t are not on defect free full gdmt guideline directed medical therapy or max maximum target tolerated dose doses their pcp is cardiologist cardiologists primary care provider providers show list them the each review titration assess assessment of for current please medication medications regimen missing classes at a an'.split())
    return not words - allowed


def assessment(path):
    db = router._DB(path / 'semantic.sqlite3')
    try:
        as_of = db.meta_as_of()
        dx = db.rows("SELECT * FROM condition WHERE concept IN ('heart_failure','heart_failure_reduced_ef') AND (event_date IS NULL OR event_date<=?)", (as_of,))
        by_dx = router._group(dx)
        ids = sorted(by_dx)
        people = {r['patient_id']: r for r in db.by_patient('patient', ids)}
        ef = router._group(db.by_patient('note_fact', ids, "fact_type='ejection_fraction' AND negated=0 AND experiencer='patient' AND temporality='current' AND unit='%' AND event_date<=?", (as_of,)))
        # Reuse the native measurement adapters, including quality and source-clock dependencies.
        spec = clinical_inputs.mappings()['measurements']['lvef']
        streams = sorted({s['stream'] for s in spec['sources']} | {t for s in spec['sources'] for ts in s.get('dependencies', {}).values() for t in ts})
        native_rows = router._group(db.by_patient('record', ids, f"stream IN ({','.join('?'*len(streams))})", streams))
        for pid, sources in native_rows.items():
            records = [json.loads(r['payload']) for r in sources]
            observations = clinical_inputs.measurements(pid, records, [], as_of, adapter={'measurements': {'lvef': spec}}).get('lvef', [])
            mapped = {e['record_id'] for o in observations for e in o['evidence']}
            ef[pid] = [f for f in ef.get(pid, []) if f['record_id'] not in mapped]
            for o in observations:
                if not o['exclusion'] and o['clinical_date']:
                    ef[pid].append({'patient_id': pid, 'record_id': o['evidence'][0]['record_id'], 'value': o['lower'],
                        'event_date': o['clinical_date'], 'basis': 'Explicit clinical date and structured EF', 'observation': o})
        cat = catalog.load()
        cfg = {**cat['gaps']['gdmt_pillar_missing'], '_barrier_policy': cat.get('barrier_policy')}
        evaluator = router._GDMT(db, ids, date.fromisoformat(as_of), cfg)
        clinicians = {r['clinician_id']: r for r in db.rows('SELECT * FROM clinician')}
        visits = router._group(db.by_patient('encounter', ids, "event_date<=? AND COALESCE(status,'') NOT IN ('cancelled','canceled','no_show','scheduled')", (as_of,)))
        relationships = router._group([json.loads(r['payload']) for r in db.by_patient('record', ids, "stream='care_team_relationships'")])
        med_rows = db.by_patient('medication_list_entry', ids)
        by_med = router._group(med_rows)
        source = {}
        rids = sorted({r['record_id'] for r in med_rows if r.get('record_id')})
        for i in range(0, len(rids), 400):
            chunk = rids[i:i+400]
            for r in db.rows(f"SELECT record_id,payload FROM record WHERE record_id IN ({','.join('?'*len(chunk))})", chunk):
                source[r['record_id']] = json.loads(r['payload'])
        rows, uncertain, excluded = [], [], []
        for pid in ids:
            observed = [r for r in ef.get(pid, []) if isinstance(r['value'], (int,float)) and 0 <= r['value'] <= 100]
            newest = max((r['event_date'] for r in observed), default=None)
            latest = [r for r in observed if r['event_date'] == newest]
            values = {r['value'] for r in latest}
            low = bool(values) and max(values) <= 40
            hfref = any(r['concept'] == 'heart_failure_reduced_ef' for r in by_dx[pid])
            if not low and not (hfref and not values):
                (excluded if values and min(values)>40 else uncertain).append(pid)
                continue
            gap, _ = evaluator.evaluate(pid, None)
            cards = sorted([r for r in visits.get(pid, []) if 'cardiol' in clinicians.get(r.get('attending_clinician_id'), {}).get('specialty','').lower()], key=lambda r:r['event_date'], reverse=True)
            assigned = [r for r in relationships.get(pid, []) if r.get('status') in ('assigned','active')
                        and (r.get('effective_start') or r.get('relationship_start') or '9999') <= as_of
                        and (r.get('effective_end') or r.get('relationship_end') or '9999') >= as_of
                        and ('cardiol' in str(r.get('role','')).lower() or 'cardiol' in clinicians.get(r.get('clinician_id'), {}).get('specialty','').lower())]
            cardiologists = sorted({r['clinician_id'] for r in assigned if r.get('clinician_id')})
            pcp = people.get(pid, {}).get('pcp_clinician_id')
            missing = gap['missing_pillars']['value']
            details = []
            for med in by_med.get(pid, []):
                if not any(router._ingredient_hit(med, p.get('ingredients', [])) for p in cfg['pillars'].values()):
                    continue
                raw = source.get(med.get('record_id'), {})
                details.append({'name': med['name'], 'dose': raw.get('dose'), 'dose_unit': raw.get('dose_unit'),
                    'frequency': raw.get('frequency'), 'list_status': med.get('list_status'),
                    'source_date': raw.get('event_date'), 'record_id': med.get('record_id'),
                    'current_use_established': False})
            rows.append({'patient_id': pid, 'ef_percent': '/'.join(str(v) for v in sorted(values)) if values else 'Unknown',
                'ef_source_date': newest, 'ef_basis': ('Structured EF with clinical date' if latest and all('observation' in x for x in latest) else 'Extracted source statement; clinical date/phenotype need confirmation') if values else 'HFrEF diagnosis; numeric EF unavailable',
                'gdmt_review_flag': 'Reconcile unobserved pillars: '+', '.join(missing) if missing else 'Review regimen, exceptions and tolerance',
                'pillars_observed': ', '.join(gap['present_pillars']['value']) or 'None in lookback',
                'barrier_documented_pillars': ', '.join(gap['barrier_documented_pillars']['value']) or 'None recorded',
                'recorded_doses': '; '.join(f"{m['name']}: {m['dose'] if m['dose'] is not None else 'dose unknown'} {m['dose_unit'] or ''} {m['frequency'] or 'frequency unknown'} ({m['list_status'] or 'status unknown'})" for m in details) or 'Not recorded',
                'maximum_tolerated_dose': 'Unknown — reconcile current use, titration, contraindications and tolerance',
                'pcp': pcp or 'Unknown', 'cardiologist': ', '.join(cardiologists) if cardiologists else cards[0]['attending_clinician_id'] if cards else 'Unknown',
                'cardiologist_basis': 'Active dated care-team assignment' if cardiologists else 'Latest observed cardiology encounter; ongoing care relationship unconfirmed' if cards else 'No active assignment or completed cardiology encounter found',
                'evidence': {'diagnoses': by_dx[pid], 'ef': latest, 'gdmt': gap, 'doses': details,
                             'pcp': {'table':'patient','field':'pcp_clinician_id','value':pcp}, 'cardiology': assigned or cards[:1]}})
        return {'rows': rows, 'as_of': as_of, 'hf_patients': len(ids), 'ef_unresolved_patient_ids': uncertain,
                'latest_ef_above_40_patient_ids': excluded, 'catalog_version': cat['version'],
                'lookback_days': cfg['lookback_days']}
    finally:
        db.close()


def answer(path, question):
    t = time.perf_counter()
    r = assessment(path)
    rows = r.pop('rows')
    limits = ['Documentary review candidates, not a determination of undertreatment or program eligibility.',
              'EF uses mapped structured measurements with clinical dates where available, plus affirmative note extractions dated by source statement. Current phenotype remains unconfirmed; dates and source evidence stay attached.',
              f"GDMT coverage uses existing program rules over {r['lookback_days']} days. Missing fills/list entries do not prove missing care.",
              'Recorded doses are literal source notation. Target and maximum tolerated dose are not established from these records.',
              'Cardiologist uses active dated care-team assignment where documented, otherwise the latest observed cardiology encounter with ongoing responsibility unconfirmed.']
    headline = f"{len(rows)} HF patients for reduced-EF therapy review; {len(r['ef_unresolved_patient_ids'])} additional HF patients have unresolved EF."
    return {'schema': VERSION, 'synthetic': True, 'question': question, 'route': 'patient_therapy_review',
        'answer': {'status': 'partial', 'answer_type': 'table', 'headline': 'Incomplete documentary review. ' + headline, 'table': rows,
            'table_columns': ['patient_id','ef_percent','gdmt_review_flag','recorded_doses','maximum_tolerated_dose','pcp','cardiologist'],
            'patient_ids': [x['patient_id'] for x in rows],
            'missing': ['Requested treatment-gap and dose classification is not implemented reliably.',
                        'Current phenotype, medication reconciliation and complete care-team attribution remain unverified.'],
            'actionable_cohort': False, 'limitations': limits,
            'interpretation': 'Interpret defect-free GDMT as a review of four medication pillars, exceptions, dose evidence and care-team attribution. Clinical appropriateness remains unresolved.',
            'overall': r},
        'planner_receipt': {'route': 'patient_therapy_review', 'version': VERSION, 'new_paid_api_calls': 0,
                            'scope': 'Bounded HF documentary assessment using existing GDMT catalog and semantic sources'},
        'planning_economics': {'new_api_estimate_usd': 0, 'known_api_estimate_usd': 0, 'total_cost_known': False,
                              'scope': 'Zero hosted API calls; local compute and review costs unmeasured'},
        'new_paid_api_calls': 0, 'elapsed_seconds': time.perf_counter()-t, 'clinical_execution_authorized': False}
