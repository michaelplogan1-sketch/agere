"""Versioned program packages and source-grounded, three-valued rule execution.

New CMS/commercial/Medicaid specifications can be installed without retraining a
foundation model. A quoted machine specification is still a research candidate;
quotation validates provenance, not interpretation of the program's requirements.
"""
from pathlib import Path
from datetime import date
import hashlib,json,sqlite3
from ...contracts import digest
from ..policy import Program,compile_document

ROOT=Path(__file__).resolve().parents[3]
TABLE='CREATE TABLE IF NOT EXISTS program_packages(id TEXT PRIMARY KEY,program TEXT,version TEXT,start TEXT,end TEXT,payload TEXT)'
BINDINGS={
 'latest_egfr':'Latest unambiguous usable eGFR by source/collection date; source date may be a surrogate.',
 'egfr_below_60_persistent':'Recorded low eGFR over at least 90 source-date days; does not establish chronic disease.',
 'difficulty_paying':'Latest structured difficulty-paying screen; same-date conflicting screens yield unknown.',
 'age':'Age from roster birth date and snapshot cutoff.',
}

def install_defaults(path):
    db=sqlite3.connect(Path(path)/'stack.sqlite3');db.execute(TABLE)
    try:
        with db:
            for spec in json.loads((ROOT/'config/agere/programs.json').read_text()):
                p=Program(spec)
                package={'spec':spec,'policy_hash':p.hash,'synthetic':True,'status':'existing_synthetic_review_rule',
                         'semantic_equivalence':'Previously configured bounded development rule; not full guideline or payer eligibility.',
                         'source_document_available':False,'installed_from':'config/agere/programs.json'}
                db.execute('INSERT OR IGNORE INTO program_packages VALUES (?,?,?,?,?,?)',
                  (p.hash,spec['id'],spec['version'],spec['effective_start'],spec['effective_end'],json.dumps(package)))
    finally:db.close()

def bounded_expression(node,depth=0):
    if depth>12 or not isinstance(node,dict) or len(node)!=1:raise ValueError('Bounded declarative expression required')
    op,value=next(iter(node.items()))
    if op in ('all','any'):
        if not isinstance(value,list) or not 1<=len(value)<=20:raise ValueError('Bounded predicate list required')
        for child in value:bounded_expression(child,depth+1)
    elif op=='not':bounded_expression(value,depth+1)
    else:
        if not isinstance(value,list) or len(value)!=2 or not isinstance(value[0],str) or not 1<=len(value[0])<=100:raise ValueError('Bounded input binding required')
        if len(json.dumps(value))>2000:raise ValueError('Bounded predicate values required')

def install_candidate(path,package):
    if not isinstance(package,dict) or set(package)!={'document','spec','bindings'}:raise ValueError('Document/spec/bindings package required')
    text=package['document'];spec=package['spec']
    if not isinstance(text,str) or not 1<=len(text)<=60000:raise ValueError('Bounded source document required')
    if not isinstance(spec,dict):raise ValueError('Specification required')
    for name in ('id','version','source','purpose'):
        if not isinstance(spec.get(name),str) or not 1<=len(spec[name])<=2000:raise ValueError('Bounded specification strings required')
    bounded_expression(spec.get('predicate'));compiled=compile_document(text,spec,package['bindings']);p=Program(spec)
    out={'spec':spec,**compiled,'synthetic':True,'status':'quoted_research_candidate',
         'source_document_available':True,'unsupported_input_bindings':sorted(p.inputs-set(BINDINGS)),
         'source_document':text,'clinical_execution_authorized':False,
         'scope':'Research rule simulation; quoted source is not verified semantic equivalence or clinical approval.'}
    db=sqlite3.connect(Path(path)/'stack.sqlite3');db.execute(TABLE)
    try:
        old=db.execute('SELECT id FROM program_packages WHERE program=? AND version=?',(spec['id'],spec['version'])).fetchone()
        if old and old[0]!=p.hash:raise ValueError('Existing version immutable; use a new version for changed rules')
        with db:db.execute('INSERT OR IGNORE INTO program_packages VALUES (?,?,?,?,?,?)',
                          (p.hash,spec['id'],spec['version'],spec['effective_start'],spec['effective_end'],json.dumps(out)))
        return {k:v for k,v in out.items() if k!='source_document'}
    finally:db.close()

def packages(path):
    db=sqlite3.connect(Path(path)/'stack.sqlite3')
    try:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM program_packages ORDER BY program,start,version')]
    finally:db.close()

def fact_bindings(patient,records,renal,as_of):
    e=renal['measurements']['egfr']['series'];values={'latest_egfr':None,'egfr_below_60_persistent':None,'difficulty_paying':None,'age':None};evidence={k:[] for k in values}
    if e:
        values['latest_egfr']=e[-1]['value'];evidence['latest_egfr']=e[-1]['all_source_evidence']
        low=[r for r in e if r['value']<60]
        if e[-1]['value']>=60:values['egfr_below_60_persistent']=False
        elif low and (date.fromisoformat(low[-1]['date'])-date.fromisoformat(low[0]['date'])).days>=90:values['egfr_below_60_persistent']=True
        evidence['egfr_below_60_persistent']=[ev for r in e for ev in r['all_source_evidence']]
        if any(c['date']>=e[-1]['date'] for c in renal['measurements']['egfr']['same_date_conflicts']):
            # Older usable values cannot silently replace an unresolved latest date.
            values['latest_egfr']=None;values['egfr_below_60_persistent']=None
            evidence['latest_egfr']=[x['evidence'] for c in renal['measurements']['egfr']['same_date_conflicts'] for x in c['observations']]
    financial=[r for r in records if r['source_table']=='financial' and type(r.get('difficulty_paying_medical_bills')) is bool]
    if financial:
        day=max(r['event_date'] for r in financial);latest=[r for r in financial if r['event_date']==day]
        reported={r['difficulty_paying_medical_bills'] for r in latest}
        values['difficulty_paying']=reported.pop() if len(reported)==1 else None
        evidence['difficulty_paying']=[{'record_id':r['record_id'],'source_table':'financial','field':'difficulty_paying_medical_bills',
                                       'event_date':r['event_date'],'available_date':r['available_date'],'value':r['difficulty_paying_medical_bills']} for r in latest]
    dob=patient.get('birth_date')
    if dob:
        a,b=date.fromisoformat(as_of),date.fromisoformat(dob)
        values['age']=a.year-b.year-((a.month,a.day)<(b.month,b.day))
        evidence['age']=[{'source_table':'patients','field':'birth_date','value':dob,'as_of':as_of}]
    return values,evidence

def evaluate_patient(patient,records,renal,as_of,installed):
    facts,evidence=fact_bindings(patient,records,renal,as_of);evaluations=[]
    active=_active_version_counts(installed,as_of)
    for package in installed:
        spec=package['spec'];p=Program(spec);result=p.evaluate(facts,as_of)
        overlap=active.get(spec['id'],0)>1 and result['status']!='not_effective'
        evaluations.append({**result,'version':spec['version'],'package_status':package['status'],
          'input_values':{k:facts.get(k) for k in p.inputs},'input_evidence':{k:evidence.get(k,[]) for k in p.inputs},
          'unsupported_input_bindings':sorted(p.inputs-set(BINDINGS)),
          'overlapping_active_versions':overlap,'version_resolution':'explicit_selection_required' if overlap else 'one_effective_version_or_not_effective',
          'clinical_eligibility_established':False,'care_gap_established':False,
          'scope':'Criteria against recorded bound inputs; full medical/program eligibility is unresolved.'})
    return {'patient_id':patient['patient_id'],'operator':'program_rule_review','synthetic':True,
            'evaluations':evaluations,'clinical_execution_authorized':False}

def _active_version_counts(installed,as_of):
    from collections import Counter
    return Counter(x['spec']['id'] for x in installed if x['spec']['effective_start']<=as_of<=x['spec']['effective_end'])
