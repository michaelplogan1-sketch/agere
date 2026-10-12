"""Compose source-reviewed meanings with existing documentary timelines.

No inference or voting occurs here. Complete populations and immutable per-patient
receipts make saved interpretations usable by downstream analytical operations.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import time
import zlib

from ...contracts import digest
from . import assertion_reconciliation as assertions, semantic_reader as reader
from . import source_structure

VERSION = 'reviewed-source-population/1'
FILTERS = ('all', 'needs_review', 'model_disagreement', 'review_conflict', 'unresolved_review', 'reviewed_source')
LIMITS = [
    'Membership describes source/review status, not disease, treatment need or eligibility.',
    'Machine proposals remain separate from source-reviewed interpretations. Review identity is self-reported.',
    'Different reviewers can disagree; no provider, score, majority or newest model overrides that conflict.',
    'Source, arrival, clinical and literal-reference clocks retain their meanings; source currentness is not current care.',
    'Unassessed units and unavailable sources are not clinical negatives. No treatment or outreach is executed.',
]


def validate(parameters):
    if not isinstance(parameters, dict) or set(parameters) != {'streams', 'concepts', 'filter'}:
        raise ValueError('Explicit interpretation streams, concepts and review-status filter required')
    if parameters['filter'] not in FILTERS:
        raise ValueError('Unknown interpretation status filter; clinical predicates are not supported here')
    return {**assertions.validate({k: parameters[k] for k in ('streams','concepts')}), 'filter': parameters['filter']}


def code_identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   hashlib.sha256(Path(reader.__file__).read_bytes()).hexdigest(), reader.identity()])


def occurrence(target):
    return digest(target)


def review_groups(readings):
    """Agreement among explicit reviews is a consistent review, not clinical truth."""
    grouped = defaultdict(list)
    for row in readings:
        grouped[occurrence(row['target'])].append(row)
    result = []
    for key, rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda r:r['id'])
        accepted = [(r,r['reviews'][-1]) for r in rows if r.get('reviews') and r['reviews'][-1]['status'] in ('accepted','corrected')]
        unresolved = [r['id'] for r in rows if r.get('reviews') and r['reviews'][-1]['status']=='unresolved']
        signatures = {digest(review['labels']) for _,review in accepted}
        incomplete = any(not r['context']['context_complete'] for r,_ in accepted)
        status = ('review_conflict' if len(signatures)>1 else 'unresolved_review' if unresolved else
                  'incomplete_review_context' if incomplete else 'reviewed_source' if accepted else 'proposal_only')
        result.append({'occurrence_id':key, 'target':rows[0]['target'], 'status':status,
            'reading_ids':[r['id'] for r in rows],
            'model_disagreement_axes':[axis for axis in reader.AXES if len({r['reading']['labels'][axis] for r in rows})>1],
            'reviews':[{'reading_id':r['id'], **r['reviews'][-1]} for r in rows if r.get('reviews')],
            'accepted_reading_ids':[r['id'] for r,_ in accepted] if status=='reviewed_source' else [],
            'effective_labels':accepted[0][1]['labels'] if status=='reviewed_source' else None,
            'automatic_clinical_acceptance':False})
    return result


def snapshot(path, parameters):
    """Capture current source-verified readings and latest review revisions once."""
    p=validate(parameters);base,meta,_=assertions.dependency(path,{k:p[k] for k in ('streams','concepts')})
    by_patient=defaultdict(list);stale=defaultdict(list);database=reader.db(path)
    try:
        candidates=[json.loads(r[0]) for r in database.execute(
            "SELECT payload FROM readings WHERE json_extract(payload,'$.reader_identity')=? ORDER BY id",(reader.identity(),))]
    finally:database.close()
    for candidate in candidates:
        target=candidate['target']
        if target['concept'] not in p['concepts']:continue
        try:
            row=reader.inspect(path,candidate['id'])
            record,_,_=reader.source(path,target)
            if record['source_table'] not in p['streams']:continue
        except ValueError as error:
            stale[target['patient_id']].append({'reading_id':candidate['id'],'reason':str(error)})
            continue
        by_patient[target['patient_id']].append(row)
    revisions={pid:digest([[r['id'],r['source_hash'],r['review_revision'],r['reviews'][-1] if r['reviews'] else None]
                           for r in rows]) for pid,rows in sorted(by_patient.items())}
    identity=digest([code_identity(),base,p,revisions,dict(stale)])
    return {'identity':identity,'base_identity':base,'meta':meta,'parameters':p,
            'readings':dict(by_patient),'revisions':revisions,'stale':dict(stale)}


def reviewed_assertion(base,group,readings):
    r=next(r for r in readings if r['id'] in group['accepted_reading_ids']);target=r['target'];labels=group['effective_labels']
    source=base['sources'].get(target['record_id'])
    if source is None or source['record_hash']!=r['source_hash'] or source['patient_id']!=target['patient_id']:
        raise ValueError('Reviewed statement and patient timeline do not share an exact source')
    matches=[a for a in base['assertions'] if matches_target(a,target)]
    relation={'hold_stop':'treatment_hold_stop','administration':'administration_record','care_plan':'care_order_plan'}.get(labels['relation'],labels['relation'])
    if target['concept']=='financial hardship' and relation=='clinical_observation':relation='reported_barrier'
    statement=labels['statement'];polarity={'patient_affirmed':'affirmed','patient_negated':'negated'}.get(statement,statement)
    p=r['context']
    a={'patient_id':target['patient_id'],'concept':target['concept'],'relation':relation,'polarity':polarity,
       'value':polarity,'subject':'patient' if statement in ('patient_affirmed','patient_negated','hypothetical','retracted') else 'other' if statement=='other_person' else 'unknown',
       'source_unit_id':matches[0]['source_unit_id'] if matches else None,'source':source,
       'evidence':{**{k:target[k] for k in ('field','start','end')},'quote':p['target_quote']},
       'authority':'source_review','semantic_validation':'explicit_local_source_review',
       'clocks':{'source_date':p['source_date'],'arrival_date':p['available_date'],'clinical_date':None,'reference_date':p['literal_reference_date']},
       'clinical_date_status':'not_observed','clinical_date_fields':{},
       'temporality':{'current_at_source':'current'}.get(labels['time'],labels['time']),
       'copied_history':p['copied_history'],'reference_status':p['reference_status'],
       'repeated_statement_family':digest([target['patient_id'],target['concept'],relation,' '.join(p['text'].casefold().split())]),
       'reviewed_labels':labels,'review_refs':[{'reading_id':v['reading_id'],'revision':v['revision']} for v in group['reviews'] if v['status'] in ('accepted','corrected')],
       'reviewer_identity_verified':False,'current_clinical_state_established':False}
    a['source_structure']=source_structure.locate(p['text'],p['target_start'],p['target_end'])
    a['id']=digest([VERSION,a]);return a


def matches_target(a,target):
    e=a['evidence']
    return (a['authority']!='literal_structured_field' and a['patient_id']==target['patient_id'] and
            a['concept']==target['concept'] and a['source']['record_id']==target['record_id'] and
            all(e.get(k)==target[k] for k in ('field','start','end')))


def patient_receipt(base,readings,stale):
    patient=base['patient_id']
    if any(r['target']['patient_id']!=patient for r in readings):raise ValueError('Cross-patient interpretation')
    groups=review_groups(readings);effective=deepcopy(base['assertions']);superseded=[]
    for group in groups:
        if group['status']!='reviewed_source':continue
        item=reviewed_assertion(base,group,readings)
        old=[a for a in effective if matches_target(a,group['target'])]
        effective=[a for a in effective if not matches_target(a,group['target'])]+[item]
        superseded.extend({'prior_assertion_id':a['id'],'reviewed_assertion_id':item['id']} for a in old)
    reconciled=assertions.reconcile(patient,effective,base['as_of'])
    for g in reconciled:
        for account in g['dated_accounts']:
            account['source_reviewed_assertions']=sum(a['authority']=='source_review' and a['id'] in account['assertion_ids'] for a in effective)
    unresolved=sum(a['authority']=='machine_annotation' for a in effective)
    flags={'model_disagreement':any(g['model_disagreement_axes'] for g in groups),
           'review_conflict':any(g['status']=='review_conflict' for g in groups),
           'unresolved_review':any(g['status']=='unresolved_review' for g in groups),
           'reviewed_source':any(g['status']=='reviewed_source' for g in groups)}
    ambiguous=any(g['effective_labels'] and ('unknown' in g['effective_labels'].values()) for g in groups)
    flags['needs_review']=bool(unresolved or stale or ambiguous or flags['review_conflict'] or
        any(g['status']!='reviewed_source' for g in groups) or base['coverage']['units_without_in_scope_assertions'] or
        base['coverage']['streams_without_records'] or base['coverage']['concepts_without_assertions'] or
        any(g['status'] in ('same_date_conflict','opposing_reports_over_time_or_unresolved_clock') or g['excluded_from_dated_accounts'] for g in reconciled))
    flags['all']=True
    steps=[]
    for kind,condition,detail in [
        ('resolve_review_conflict',flags['review_conflict'],'Inspect conflicting or unresolved explicit reviews; no provider or vote overrides them.'),
        ('complete_source_review',flags['unresolved_review'],'Resolve explicit source-review uncertainty before reusing a prior accepted interpretation.'),
        ('review_model_disagreement',any(g['model_disagreement_axes'] and g['status']!='reviewed_source' for g in groups),'Compare the marked proposition and its time/relationship across saved readers.'),
        ('review_remaining_annotations',unresolved>0,'Review remaining baseline machine interpretations against their source context.'),
        ('refresh_stale_readings',bool(stale),'Rebuild interpretations whose received source or interpretation contract changed.'),
        ('establish_missing_source_or_meaning',bool(base['coverage']['units_without_in_scope_assertions'] or base['coverage']['streams_without_records']),'Inspect unassessed content and source availability; absence of evidence is not missing care.'),
        ('resolve_documentary_accounts',any(g['status'] in ('same_date_conflict','opposing_reports_over_time_or_unresolved_clock') or g['excluded_from_dated_accounts'] for g in reconciled),'Reconcile opposing reports, unresolved dates and copied history before reasoning about current care.')]:
        if condition:steps.append({'kind':kind,'detail':detail,'execution_authorized':False})
    return {'schema':VERSION,'synthetic':True,'patient_id':patient,'as_of':base['as_of'],'base_receipt_id':base['id'],
        'source_groups':groups,'effective_assertions':effective,'documentary_timeline':reconciled,
        'superseded_assertions':superseded,'coverage':base['coverage'],'sources':base['sources'],
        'stale_readings':stale,'flags':flags,'next_evidence_steps':steps,
        'counts':{'source_groups':len(groups),'saved_proposals':len(readings),'reviewed_source_groups':sum(g['status']=='reviewed_source' for g in groups),
                  'remaining_machine_assertions':unresolved,'superseded_machine_assertions':len(superseded),
                  'review_conflicts':sum(g['status']=='review_conflict' for g in groups),
                  'unresolved_reviews':sum(g['status']=='unresolved_review' for g in groups)},
        'current_clinical_state_established':False,'clinical_eligibility_established':False,'new_paid_api_calls':0,'limits':LIMITS}


def storage(path):
    db=sqlite3.connect(Path(path)/'interpretation-state.sqlite3',timeout=30)
    db.executescript('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,patient TEXT NOT NULL,payload BLOB NOT NULL);')
    return db


def execute(path,patient_ids,parameters,*,prepared=None,preview_limit=6):
    started=time.perf_counter();p=validate(parameters)
    if type(preview_limit) is not int or not 0<=preview_limit<=20:raise ValueError('Bounded interpretation preview required')
    snap=prepared or snapshot(path,p);code=code_identity()
    if snap['parameters']!=p:raise ValueError('Interpretation snapshot scope mismatch')
    ids=sorted(set(patient_ids));scope={k:p[k] for k in ('streams','concepts')}
    base_summary=assertions.execute(path,ids,scope,preview_limit=0)
    if base_summary['identity']!=snap['base_identity']:raise ValueError('Source assertions changed during interpretation')
    baseline=assertions.storage(path);out=storage(path);counts=Counter();flags=Counter();steps=Counter();preview=[];selected=set()
    try:
        with out:
            for patient in ids:
                base_id=digest([snap['base_identity'],patient])
                revision=snap['revisions'].get(patient,digest([]));stale=snap['stale'].get(patient,[])
                key=digest([code,base_id,revision,stale])
                old=out.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
                if old:row=json.loads(zlib.decompress(old[0]));counts['patient_cache_hits']+=1
                else:
                    raw=baseline.execute('SELECT payload FROM receipts WHERE id=?',(base_id,)).fetchone()
                    if raw is None:raise ValueError('Source assertion receipt missing')
                    base=json.loads(zlib.decompress(raw[0]));row=patient_receipt(base,snap['readings'].get(patient,[]),stale)
                    row.update(id=key,parameters=scope,base_identity=snap['base_identity'],reader_revision=revision,code_identity=code)
                    out.execute('INSERT INTO receipts VALUES (?,?,?)',(key,patient,zlib.compress(json.dumps(row).encode())))
                    counts['patient_receipts_computed']+=1
                counts.update(row['counts']);flags.update(k for k,v in row['flags'].items() if v)
                steps.update(s['kind'] for s in row['next_evidence_steps'])
                if row['flags'][p['filter']]:
                    selected.add(patient)
                    candidate={k:row[k] for k in ('id','patient_id','flags','counts','next_evidence_steps')}
                    preview=sorted(preview+[candidate],key=lambda r:(not r['flags']['review_conflict'],not r['counts']['saved_proposals'],r['patient_id']))[:preview_limit]
        # No aggregate result is committed under a revision that changed mid-run.
        if snapshot(path,p)['identity']!=snap['identity']:raise ValueError('Source reviews changed during interpretation; rerun with the new revision')
        return selected,{'schema':VERSION,'synthetic':True,'identity':snap['identity'],'parameters':p,
            'patients_evaluated':len(ids),'population_digest':digest(ids),'selected_by_review_status':len(selected),
            'status_counts':dict(flags),'counts':dict(counts),'patients_needing_evidence_step':dict(steps),'patients':preview,
            'base_assertion_counts':base_summary['counts'],'all_patient_receipts_persisted':True,
            'preview_scope':'Matching patients only: review conflicts, then saved proposals, then patient ID. Preview is not prevalence.',
            'new_paid_api_calls':0,'elapsed_seconds':time.perf_counter()-started,
            'current_clinical_state_established':False,'clinical_eligibility_established':False,'limits':LIMITS}
    finally:baseline.close();out.close()


def inspect(path,receipt_id):
    if not isinstance(receipt_id,str) or not re.fullmatch('[a-f0-9]{64}',receipt_id):raise ValueError('Exact interpretation receipt ID required')
    db=storage(path)
    try:raw=db.execute('SELECT payload FROM receipts WHERE id=?',(receipt_id,)).fetchone()
    finally:db.close()
    if raw is None:raise ValueError('Unknown interpretation receipt')
    row=json.loads(zlib.decompress(raw[0]));snap=snapshot(path,{**row['parameters'],'filter':'all'})
    if (row['base_identity']!=snap['base_identity'] or row['code_identity']!=code_identity() or
        row['reader_revision']!=snap['revisions'].get(row['patient_id'],digest([])) or row['stale_readings']!=snap['stale'].get(row['patient_id'],[])):
        raise ValueError('Source interpretations changed; rebuild the population question')
    assertions.inspect(path,row['base_receipt_id'])
    return row
