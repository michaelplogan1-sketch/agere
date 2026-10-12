"""Connect source interpretation and measurement states to review protocols.

Rule definitions never manufacture missing input semantics. A source-review
candidate is explicitly different from a supported documentary criterion, and
neither establishes treatment or program eligibility.
"""
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import time
import zlib

from ...contracts import digest
from . import clinical_inputs, source_state, interpretation_state as interpretation
from . import evidence_requirements as requirements
from . import patient_input_index
from .clinical_events import iso

VERSION='care-review-protocols/1'
CATALOG=Path(__file__).with_name('care_protocols.json')
HISTORY=Path(__file__).with_name('care_protocol_history.json')
FILTERS={'all','candidates','supported','source_review','unresolved'}
LIMITS=[
    'Synthetic development only. Guideline-informed review definitions are not complete clinical or payer protocols.',
    'Recorded numeric/text signals nominate review; unreviewed source interpretations never become accepted clinical facts.',
    'Source dates and explicit clinical dates remain distinct. A dated observation does not establish current physiology.',
    'Missing notes, medication reports or specialist attendance can reflect unavailable care records; missing care is not inferred.',
    'Documentary readiness does not satisfy the separately listed unimplemented clinical, consent or program requirements.',
    'No treatment, referral, outreach, enrollment, causal benefit or financial payment is authorized by this result.',
]


def read_catalog(path):
    raw=json.loads(path.read_text())
    if raw.get('schema')!=VERSION or raw.get('authority')!='guideline_informed_review_only':raise ValueError('Unknown care protocol authority')
    out={}
    for spec in raw['protocols']:
        validate_definition(spec)
        key=qualified(spec)
        if key in out:raise ValueError('Duplicate care protocol version')
        out[key]={**spec,'authority':raw['authority'],'hash':digest(spec)}
    return out


def qualified(spec):return spec['id']+'@'+spec['version']


def definitions():
    rows=read_catalog(CATALOG)
    if len({s['id'] for s in rows.values()})!=len(rows):raise ValueError('One active version per care protocol required')
    return {s['id']:s for s in rows.values()}


def validate_definition(s):
    if not isinstance(s,dict) or set(s)-{'supporting_documentary_contracts'}!={'id','version','title','purpose','sources','bindings','trigger','readiness','review_action','unimplemented_clinical_requirements','boundary'}:
        raise ValueError('Exact care protocol definition required')
    supporting=s.get('supporting_documentary_contracts',[])
    if not isinstance(supporting,list) or len(supporting)>4 or any(not isinstance(x,str) for x in supporting) or len(set(supporting))!=len(supporting):
        raise ValueError('Distinct bounded supporting documentary contracts required')
    for key in supporting:requirements.definition(key)
    for key in ('id','version','title','purpose','review_action','boundary'):
        if not isinstance(s[key],str) or not 1<=len(s[key])<=2000:raise ValueError('Bounded protocol text required')
    if not re.fullmatch('[a-z][a-z0-9_]{0,63}',s['id']):raise ValueError('Bounded protocol identifier required')
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',s['version']):raise ValueError('Explicit protocol version required')
    if not isinstance(s['sources'],list) or len(s['sources'])>8:raise ValueError('Bounded protocol source list required')
    for r in s['sources']:
        if not isinstance(r,dict) or set(r)!={'url','title','pages','checked_on','short_quote','interpretation'} or not r['url'].startswith('https://') or not iso(r['checked_on']):
            raise ValueError('Explicit source provenance required')
    extra=s['unimplemented_clinical_requirements']
    if not isinstance(extra,list) or len(extra)>20 or any(not isinstance(v,str) or not 1<=len(v)<=1000 for v in extra):raise ValueError('Bounded remaining requirements required')
    bindings=s['bindings']
    if not isinstance(bindings,dict) or not 1<=len(bindings)<=20:raise ValueError('Bounded protocol bindings required')
    for key,b in bindings.items():
        if not re.fullmatch('[a-z][a-z0-9_]{0,63}',key) or not isinstance(b,dict) or not isinstance(b.get('label'),str):raise ValueError('Named typed binding required')
        kind=b.get('kind')
        if kind=='measurement':
            if set(b)-{'expected'}!={'kind','label','measure','op','value','clock','selection'} or b.get('expected','match') not in ('match','known') or b['measure'] not in clinical_inputs.mappings()['measurements']:
                raise ValueError('Mapped measurement binding required')
            if b['clock'] not in ('source_statement_date','explicit_clinical_date') or b['selection'] not in ('latest','any_history'):raise ValueError('Explicit measurement clock and reducer required')
        elif kind=='age':
            if set(b)!={'kind','label','op','value'}:raise ValueError('Explicit age binding required')
        elif kind=='documentary':
            if set(b)!={'kind','label','source','expected'} or b['expected'] not in ('affirmed','negated','known'):raise ValueError('Documentary binding required')
            source_state.validate({**b['source'],'filter':'all'})
        elif kind=='requirement':
            if set(b)!={'kind','label','contract','version','contract_hash','expected'} or b['expected'] not in ('supported','contradicted','known'):
                raise ValueError('Pinned documentary requirement binding required')
            spec=requirements.definition(b['contract'])
            if b['version']!=spec['version'] or b['contract_hash']!=spec['hash']:
                raise ValueError('Documentary requirement version or hash changed')
        else:raise ValueError('Unimplemented clinical binding kind')
        if kind in ('measurement','age'):
            if b['op'] not in ('lt','le','gt','ge') or clinical_inputs.number(b['value']) is None:raise ValueError('Bounded numeric comparison required')
    used=requirements.validate_expression(s['trigger'],set(bindings))|requirements.validate_expression(s['readiness'],set(bindings))
    if used!=set(bindings):raise ValueError('Unused protocol binding')
    return s


def definition(key):
    if isinstance(key,str):
        if '@' not in key and key in definitions():return definitions()[key]
        rows=read_catalog(HISTORY);active=read_catalog(CATALOG)
        if set(rows)&set(active):raise ValueError('Active and archived protocol version overlap')
        if key in {**rows,**active}:return {**rows,**active}[key]
    raise ValueError('Unknown registered care protocol')


def validate(p):
    if not isinstance(p,dict) or set(p)!={'protocol','filter'} or not isinstance(p['filter'],str) or p['filter'] not in FILTERS:
        raise ValueError('Registered protocol and review filter required')
    definition(p['protocol']);return dict(p)


def code_identity():
    return digest([clinical_inputs.identity(),source_state.code_identity(),requirements.code_identity(),patient_input_index.identity(),
        *[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (__file__,CATALOG,HISTORY)]])


def source_scope(spec):
    bindings=[b for b in spec['bindings'].values() if b['kind']=='documentary']
    if not bindings:return None
    return {'streams':sorted({x for b in bindings for x in b['source']['streams']}),
            'concepts':sorted({b['source']['concept'] for b in bindings}),'filter':'all'}


def prepare(path,parameters):
    path=Path(path);p=validate(parameters);spec=definition(p['protocol']);meta=json.loads((path/'run.json').read_text())
    if meta.get('synthetic') is not True:raise ValueError('Synthetic protocol warehouse required')
    scope=source_scope(spec);snap=interpretation.snapshot(path,scope) if scope else None
    inputs=patient_input_index.snapshot(path)
    patient_inputs={pid:value['protocol_hash'] for pid,value in inputs['patients'].items()}
    supporting={key:requirements.snapshot(path,{'contract':key,'filter':'all'}) for key in spec.get('supporting_documentary_contracts',[])}
    dependencies=dict(supporting)
    for b in spec['bindings'].values():
        if b['kind']=='requirement' and b['contract'] not in dependencies:
            dependencies[b['contract']]=requirements.snapshot(path,{'contract':b['contract'],'filter':'all'})
    base={'code':code_identity(),'protocol_hash':spec['hash'],'source_digest':meta['source_digest'],'source_run':meta['source_run'],'snapshot':meta['snapshot']}
    return {'identity':digest([base,patient_inputs,snap['identity'] if snap else None,{k:v['identity'] for k,v in dependencies.items()}]),'base':base,
            'patient_inputs':patient_inputs,'interpretation':snap,'protocol':spec,'input_fingerprints':inputs['processing'],
            'supporting_documentary':supporting,'documentary_dependencies':dependencies,
            'supporting_code_identity':requirements.code_identity() if dependencies else None}


def age_binding(patient,binding,as_of):
    dob=patient.get('birth_date');value=None
    if iso(dob) and dob<=as_of:
        a,b=date.fromisoformat(as_of),date.fromisoformat(dob)
        age=a.year-b.year-((a.month,a.day)<(b.month,b.day))
        value=clinical_inputs.interval_test(age,age,binding['op'],binding['value'])
    return {'value':value,'literal_signal':value,'status':'documented' if value is not None else 'unobserved',
            'source':{'table':'patients','patient_id':patient['patient_id'],'field':'birth_date','value':dob,'as_of':as_of}}


def requirement_binding(row,b,pid,as_of):
    if (not isinstance(row,dict) or row.get('patient_id')!=pid or row.get('as_of')!=as_of or
        row.get('schema')!=requirements.VERSION or row.get('synthetic') is not True or
        any(row['contract'][k]!=b[v] for k,v in [('id','contract'),('version','version'),('hash','contract_hash')])):
        raise ValueError('Same-patient pinned documentary requirement receipt required')
    if row['status'] not in ('supported','contradicted','unresolved') or row['value'] is not {'supported':True,'contradicted':False,'unresolved':None}[row['status']]:
        raise ValueError('Requirement status/value mismatch')
    value=(True if b['expected']=='known' else row['status']==b['expected']) if row['value'] is not None else None
    conflict=any(x['state']['status']=='conflicting' or any(a['account_status']=='conflicting' for a in x['state'].get('pending_accounts',[])) for x in row['bindings'].values())
    return {'value':value,'literal_signal':value,'status':'documented' if value is not None else 'conflicting' if conflict else 'unresolved_interpretation',
        'requirement_reference':{'contract_id':b['contract'],'version':b['version'],'contract_hash':b['contract_hash'],
            'receipt_id':row['id'],'receipt_hash':digest(row),'status':row['status'],'value':row['value'],
            'role':'documentary_trigger_or_readiness_input','clinical_eligibility_established':False},
        'requirement_evidence':row}


def evaluate(patient,records,analyses,interpreted,spec,as_of,*,requirement_rows=None):
    observations=clinical_inputs.measurements(patient['patient_id'],records,analyses,as_of) if any(b['kind']=='measurement' for b in spec['bindings'].values()) else {}
    bindings={}
    for name,b in spec['bindings'].items():
        if b['kind']=='measurement':r=clinical_inputs.measurement_binding(observations.get(b['measure'],[]),b,as_of)
        elif b['kind']=='age':r=age_binding(patient,b,as_of)
        elif b['kind']=='requirement':r=requirement_binding((requirement_rows or {}).get(b['contract']),b,patient['patient_id'],as_of)
        else:
            if interpreted is None:raise ValueError('Documentary interpretation missing')
            state=source_state.project(requirements.scoped_receipt(interpreted,b),{**b['source'],'filter':'all'})
            value=(True if b['expected']=='known' else state['value']==b['expected']) if state['status']=='documented' else None
            r={'value':value,'literal_signal':value,'status':state['status'],'state':state}
        bindings[name]={'label':b['label'],**r}
    values={k:r['value'] for k,r in bindings.items()};literal={k:r['literal_signal'] for k,r in bindings.items()}
    trigger=requirements.logic(spec['trigger'],values);signal=requirements.logic(spec['trigger'],literal)
    ready=requirements.logic(spec['readiness'],values)
    trigger_keys=requirements.validate_expression(spec['trigger'],set(bindings))
    conflict=any(bindings[k]['status']=='conflicting' for k in trigger_keys)
    status=('review_criterion_supported' if trigger is True else 'criterion_not_supported' if trigger is False else
            'source_interpretation_required' if signal is True else 'conflicting_evidence' if conflict else 'insufficient_evidence')
    candidate=trigger is True or signal is True or conflict
    actions=[]
    if candidate:
        if trigger is not True:actions.append({'kind':'source_review','text':'Resolve the listed interpretation or conflicting measurement evidence before applying the clinical criterion.','binding_ids':sorted(trigger_keys)})
        actions.append({'kind':'clinical_review','text':spec['review_action'],'binding_ids':sorted(trigger_keys),
                        'status':'proposed' if trigger is True else 'conditional_on_source_review'})
    acquisition=[]
    for key,value in values.items():
        if value is not None:continue
        effects={str(v).lower():{'trigger':requirements.logic(spec['trigger'],{**values,key:v}),
            'readiness':requirements.logic(spec['readiness'],{**values,key:v})} for v in (False,True)}
        acquisition.append({'binding':key,'label':bindings[key]['label'],'status':bindings[key]['status'],
            'conditional_effect':effects,'clinical_probability_assigned':False})
    return {'schema':VERSION,'synthetic':True,'patient_id':patient['patient_id'],'as_of':as_of,'protocol':spec,
        'status':status,'review_candidate':candidate,'trigger':trigger,'literal_signal':signal,
        'documentary_readiness':ready,'bindings':bindings,'proposed_actions':actions,'missing_inputs':acquisition,
        'unimplemented_clinical_requirements':spec['unimplemented_clinical_requirements'],
        'interpretation_receipt_id':interpreted['id'] if interpreted else None,
        'clinical_eligibility_established':False,'clinical_execution_authorized':False,'limits':LIMITS}


def storage(path):
    db=sqlite3.connect(Path(path)/'care-protocols.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,patient TEXT,payload BLOB)')
    return db


def receipt_id(prepared,pid,interpretation_code=None):
    snap=prepared['interpretation']
    interpreted=source_state.receipt_id(snap,pid,interpretation_code=interpretation_code) if snap else None
    supporting={key:requirements.receipt_id(value,pid,code=prepared.get('supporting_code_identity'),interpretation_code=interpretation_code)
                for key,value in prepared.get('documentary_dependencies',prepared.get('supporting_documentary',{})).items()}
    return digest([prepared['base'],prepared['patient_inputs'][pid],interpreted,supporting])


def requirement_inputs(db,source,prepared,pid,ic,patient,records):
    """Use persisted requirement results only after checking their native inputs.

    Re-project received native statements on a cache miss to detect added, removed
    or changed sources behind an outdated materialized account analysis.
    """
    from . import native_requirements as native
    result={};fresh=None
    for b in prepared['protocol']['bindings'].values():
        if b['kind']!='requirement' or b['contract'] in result:continue
        snap=prepared['documentary_dependencies'][b['contract']]
        key=requirements.receipt_id(snap,pid,code=prepared['supporting_code_identity'],interpretation_code=ic)
        raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(key,pid)).fetchone()
        if raw is None:raise ValueError('Required documentary receipt missing')
        row=json.loads(zlib.decompress(raw[0]))
        requirement_binding(row,b,pid,prepared['base']['snapshot']['as_of'])
        if row['id']!=key:raise ValueError('Documentary receipt identity mismatch')
        ns=snap.get('native')
        if ns:
            original=native.load(source,pid,ns)
            relations=set(ns['relations'])-{native.medication_accounts.RELATION}
            if ns['available'] and relations:
                if fresh is None:fresh=native.native.interpret(patient,records,prepared['base']['snapshot']['as_of'])
                for field in ('statements','states'):
                    subset=lambda r:sorted(digest(x) for x in (r or {}).get(field,[]) if x['relation'] in relations)
                    if subset(original)!=subset(fresh):raise ValueError('Native requirement inputs changed; rebuild source accounts')
            # Medication reconciliations and explicitly linked records carry raw
            # field hashes. Check all of them, not merely the positive witness.
            indexed={r['record_id']:r for r in records}
            for statement in (original or {}).get('statements',[]):
                if statement['relation'] not in ns['relations']:continue
                for ev in [*statement['evidence'],*(x['target'] for x in statement['linked_sources'])]:
                    record=indexed.get(ev['record_id'])
                    if not record or record['patient_id']!=pid or ev['patient_id']!=pid or digest(record)!=ev['record_hash']:
                        raise ValueError('Native requirement source hash changed')
                    value=native.medication_accounts.value_at(record,ev['field_path']) if 'field_path' in ev else record.get(ev['field'])
                    if value!=ev['value']:raise ValueError('Native requirement source field changed')
        result[b['contract']]=row
    return result


def supporting_evidence(db,prepared,pid,interpretation_code):
    """Keep source-review context outside clinical trigger/readiness logic."""
    result=[]
    for contract,snapshot in prepared.get('supporting_documentary',{}).items():
        key=requirements.receipt_id(snapshot,pid,code=prepared.get('supporting_code_identity'),interpretation_code=interpretation_code)
        raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(key,pid)).fetchone()
        if raw is None:raise ValueError('Supporting documentary receipt missing')
        row=json.loads(zlib.decompress(raw[0]))
        if row['contract']['id']!=contract or row['contract']['hash']!=snapshot['contract_hash'] or row['patient_id']!=pid:
            raise ValueError('Supporting documentary source mismatch')
        result.append({'contract_id':contract,'title':row['contract']['title'],'receipt_id':key,
            'status':row['status'],'value':row['value'],'next_step':row['next_step'],
            'unresolved_bindings':[k for k,v in row['bindings'].items() if v['result'] is None],
            'role':'supporting_documentary_review_context','clinical_trigger_or_readiness_input':False,
            'clinical_eligibility_established':False})
    return result


def matches(row,filter_name):
    if filter_name=='all':return True
    if filter_name=='candidates':return True if row['review_candidate'] else None if row['trigger'] is None else False
    if filter_name=='unresolved':return row['trigger'] is None
    if filter_name=='source_review':return row['status'] in ('source_interpretation_required','conflicting_evidence')
    return None if row['trigger'] is None else row['trigger']


def execute(path,patient_ids,parameters,*,prepared=None,preview_limit=6):
    start=time.perf_counter();p=validate(parameters);prepared=prepared or prepare(path,p);spec=definition(p['protocol'])
    if spec['hash']!=prepared['protocol']['hash']:raise ValueError('Protocol preparation mismatch')
    ids=sorted(set(patient_ids))
    if set(ids)-set(prepared['patient_inputs']):raise ValueError('Unknown protocol patient')
    if type(preview_limit) is not int or not 0<=preview_limit<=20:raise ValueError('Bounded protocol preview required')
    snap=prepared['interpretation'];interpretation_summary=None
    if snap:_,interpretation_summary=interpretation.execute(path,ids,source_scope(spec),prepared=snap,preview_limit=0)
    supporting_summaries={}
    for key,value in prepared.get('documentary_dependencies',prepared.get('supporting_documentary',{})).items():
        _,_,supporting_summaries[key]=requirements.execute(path,ids,{'contract':key,'filter':'all'},prepared=value,preview_limit=0)
    source=sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    meanings=interpretation.storage(path) if snap else None;db=storage(path);ic=interpretation.code_identity()
    supporting_db=requirements.storage(path) if supporting_summaries else None
    yes=set();unknown=set();counts=Counter();statuses=Counter();binding_counts=defaultdict(Counter);preview=[]
    try:
        with db:
            for pid in ids:
                key=receipt_id(prepared,pid,ic);old=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
                if old:row=json.loads(zlib.decompress(old[0]));counts['patient_cache_hits']+=1
                else:
                    patient_raw=source.execute('SELECT payload FROM patients WHERE id=?',(pid,)).fetchone()[0]
                    record_rows=[r[0] for r in source.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
                    analysis_rows=source.execute("SELECT operator,payload FROM analyses WHERE patient=? AND operator IN ('heart_failure_review','affordability_review') ORDER BY operator",(pid,)).fetchall()
                    if patient_input_index.parts(patient_raw,record_rows,analysis_rows)['protocol_hash']!=prepared['patient_inputs'][pid]:
                        raise ValueError('Clinical inputs changed during evaluation; prepare again')
                    patient=json.loads(patient_raw);records=list(map(json.loads,record_rows));analyses=[json.loads(raw) for _,raw in analysis_rows]
                    for a in analyses:
                        if a.get('patient_id')!=pid or a.get('source_run')!=prepared['base']['source_run'] or a['workflow']['as_of']!=prepared['base']['snapshot']['as_of']:
                            raise ValueError('Clinical fact snapshot mismatch')
                    interpreted=None
                    if snap:
                        raw=meanings.execute('SELECT payload FROM receipts WHERE id=?',(source_state.receipt_id(snap,pid,interpretation_code=ic),)).fetchone()
                        if raw is None:raise ValueError('Required source interpretation receipt absent')
                        interpreted=json.loads(zlib.decompress(raw[0]))
                    requirement_rows=requirement_inputs(supporting_db,source,prepared,pid,ic,patient,records)
                    row=evaluate(patient,records,analyses,interpreted,spec,prepared['base']['snapshot']['as_of'],requirement_rows=requirement_rows)
                    row['supporting_documentary_evidence']=supporting_evidence(supporting_db,prepared,pid,ic) if supporting_db else []
                    row.update(id=key,base=prepared['base'],patient_input_hash=prepared['patient_inputs'][pid])
                    db.execute('INSERT INTO receipts VALUES (?,?,?)',(key,pid,zlib.compress(json.dumps(row,allow_nan=False).encode())))
                    counts['patient_protocols_computed']+=1
                statuses[row['status']]+=1;counts['review_candidates']+=row['review_candidate']
                counts['documentary_readiness_supported']+=row['documentary_readiness'] is True
                for name,b in row['bindings'].items():binding_counts[name][b['status']]+=1
                match=matches(row,p['filter'])
                if match is True:yes.add(pid)
                elif match is None:unknown.add(pid)
                sample={k:row[k] for k in ('id','patient_id','status','review_candidate','trigger','literal_signal','documentary_readiness','proposed_actions','missing_inputs')}
                preview=sorted(preview+[sample],key=lambda r:(not r['review_candidate'],r['status']!='source_interpretation_required',r['patient_id']))[:preview_limit]
        if snap and interpretation.snapshot(path,source_scope(spec))['identity']!=snap['identity']:raise ValueError('Source reviews changed during protocol evaluation')
        current_inputs=patient_input_index.snapshot(path)['patients']
        if any(current_inputs.get(pid,{}).get('protocol_hash')!=prepared['patient_inputs'][pid] for pid in ids):
            raise ValueError('Clinical inputs changed during evaluation; prepare again')
        for key,old in prepared.get('documentary_dependencies',prepared.get('supporting_documentary',{})).items():
            fresh=requirements.snapshot(path,{'contract':key,'filter':'all'})
            current_code=requirements.code_identity()
            if any(requirements.receipt_id(old,pid,code=prepared.get('supporting_code_identity'),interpretation_code=ic)!=requirements.receipt_id(fresh,pid,code=current_code,interpretation_code=ic) for pid in ids):
                raise ValueError('Supporting documentary evidence changed during protocol evaluation')
    finally:
        source.close();db.close()
        if meanings:meanings.close()
        if supporting_db:supporting_db.close()
    return yes,unknown,{'schema':VERSION,'synthetic':True,'protocol':spec,'identity':prepared['identity'],
        'patients_evaluated':len(ids),'population_digest':digest(ids),'status_counts':dict(statuses),
        'processing_counts':dict(counts),'input_fingerprints':prepared.get('input_fingerprints'),
        'binding_status_counts':{k:dict(v) for k,v in binding_counts.items()},
        'interpretation_processing_counts':interpretation_summary['counts'] if interpretation_summary else {},
        'supporting_documentary':{k:v for k,v in supporting_summaries.items() if k in prepared.get('supporting_documentary',{})},
        'documentary_requirements':{k:v for k,v in supporting_summaries.items() if any(b.get('contract')==k for b in spec['bindings'].values())},
        'patients':preview,'all_patient_receipts_persisted':True,'new_paid_api_calls':0,
        'elapsed_seconds':time.perf_counter()-start,'clinical_eligibility_established':False,'limits':LIMITS}


def inspect(path,key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact care protocol receipt required')
    db=storage(path)
    try:raw=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
    finally:db.close()
    if raw is None:raise ValueError('Unknown protocol receipt')
    row=json.loads(zlib.decompress(raw[0]));fresh=prepare(path,{'protocol':qualified(row['protocol']),'filter':'all'})
    if key!=receipt_id(fresh,row['patient_id']):raise ValueError('Protocol, source or review changed; reevaluate')
    return row
