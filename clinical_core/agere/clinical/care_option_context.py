"""Received coverage and patient-access reports attached to an option comparison.

This supplements review evidence without choosing a payer, service or patient
preference, changing the original match, or resolving clinical applicability.
"""
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import care_option_comparison as C,coverage_sources as coverage,patient_context as patient

VERSION='care-option-received-context/1'
LIMITS={'selected_coverage_record':None,'patient_preference_confirmed':False,'coverage_established':False,
        'clinical_applicability_established':False,'provider_calls':0,'external_actions':0}

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),coverage.identity(),patient.identity()])

def database(path):
    db=sqlite3.connect(Path(path)/'care-option-context.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS contexts(id TEXT PRIMARY KEY,comparison TEXT,payload TEXT)')
    for op in ('UPDATE','DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_contexts_{op} BEFORE {op} ON contexts BEGIN SELECT RAISE(ABORT,'Comparison context history immutable'); END")
    return db

def comparison(path,key):
    C.care.text(key,'comparison ID',64)
    rows=C.L.payloads(path,'care-option-comparisons.sqlite3','comparisons',' WHERE id=?',(key,))
    if not rows:raise ValueError('Unknown saved option comparison')
    row=C.R.verified(rows[0])
    if C.snapshot(path,row['request'])!=row:raise C.care.work.WorkConflict('Option comparison changed; prepare a current comparison before adding context')
    return row

def snapshot(path,request):
    C.care.exact(request,'comparison_id');c=comparison(path,request['comparison_id']);parameters={'patient_id':c['patient_id'],'as_of':c['as_of']}
    cov=coverage.prepare(path,parameters);pat=patient.prepare(path,parameters)
    if any(x['patient_id']!=c['patient_id'] or x['as_of']!=c['as_of'] for x in [cov,pat]):raise ValueError('Received context patient or cutoff changed')
    out={'schema':VERSION,'request':request,'comparison_id':c['id'],'comparison_hash':digest(c),'question':c['question'],
         'original_service_question':c['original_service_question'],'patient_id':c['patient_id'],'as_of':c['as_of'],'task_id':c['task_id'],
         'coverage_context':cov,'patient_context':pat,
         'coverage_review_targets':[q['target'] for q in c['questions'] if q['key']=='coverage'],
         'patient_review_targets':[q['target'] for q in c['questions'] if q['key'] in ('access','preference')],
         'summary':{'coverage_reports':len(cov['reports']),'patient_reports':len(pat['reports']),
                    'reported_plan_ids':cov['exact_plan_ids_observed'],'coverage_conflict_groups':len(cov['conflicts']),
                    'differing_patient_report_groups':len(pat['literal_differences'])},
         'meaning':'Dated received reports for the comparison. They supplement review only; original plan routing fields and service mappings remain unchanged.',
         'implementation':identity(),**LIMITS};out['id']=digest(out);return out

def preview(path,request):return snapshot(path,request)

def prepare(path,request):
    C.care.exact(request,'comparison_id preview_id');selection={'comparison_id':request['comparison_id']};out=snapshot(path,selection)
    if out['id']!=request['preview_id']:raise C.care.work.WorkConflict('Received context changed; preview again')
    db=database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if snapshot(path,selection)!=out:raise C.care.work.WorkConflict('Comparison or received reports changed while saving')
            db.execute('INSERT OR IGNORE INTO contexts VALUES (?,?,?)',(out['id'],out['comparison_id'],json.dumps(out,allow_nan=False)))
            return C.R.verified(json.loads(db.execute('SELECT payload FROM contexts WHERE id=?',(out['id'],)).fetchone()[0]))
    finally:db.close()

def listing(path,request):
    C.care.exact(request,'comparison_id');C.care.text(request['comparison_id'],'comparison ID',64);out=[]
    for r in C.L.payloads(path,'care-option-context.sqlite3','contexts',' WHERE comparison=?',(request['comparison_id'],)):
        C.R.verified(r);reasons=[]
        try:
            if snapshot(path,r['request'])!=r:reasons.append('Received context or comparison changed; preserve history and prepare a current context')
        except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
        out.append({**r,'current':not reasons,'stale_reasons':reasons})
    return {'comparison_id':request['comparison_id'],'contexts':out,**LIMITS}
