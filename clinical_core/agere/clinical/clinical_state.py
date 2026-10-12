"""Source-bound documentary state and reusable review prerequisites.

A documented value is an observation, not an independently verified clinical
state. Explicit negative reports, unknown capture and unsupported interpretations
remain different. No temporal, treatment or payer criteria are invented here.
"""
from collections import Counter,defaultdict
from datetime import date
from pathlib import Path
import hashlib,json,math,re,sqlite3,time
from ...contracts import digest
from .clinical_events import iso
from .medication_reconciliation import reconcile as supply_reconcile
from .state_contracts import CONTRACTS,DRUGS,definition,evaluate

VERSION='clinical-documentary-state/1'
LIMITS=[
 'These internal contracts assess evidence prerequisites, not clinical or payer eligibility.',
 'Latest documentation and dated observations do not establish current physiology, medication ingestion, tolerance or appropriateness.',
 'Unobserved evidence can reflect missing care, outside care, delayed feeds or extraction failure; the cause is not inferred.',
 'Text-derived facts retain their existing machine-extraction status. Exact source binding does not verify meaning.',
 'This reads the current observed snapshot, not a reconstruction of superseded historical records.',
]


def code_identity():
    from .analytic_registry import code_identity as analytic_identity
    from ..narrative_sources import __file__ as narrative_file
    return digest([analytic_identity(),hashlib.sha256(Path(narrative_file).read_bytes()).hexdigest(),
                   *[hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in
                   ('clinical_state.py','state_contracts.py','clinical_events.py','medication_reconciliation.py')]])


def identity(path):
    path=Path(path);meta=json.loads((path/'run.json').read_text())
    if meta.get('synthetic') is not True:raise ValueError('Synthetic warehouse required')
    db=sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        reviews=digest(list(db.execute("SELECT patient,operator,payload FROM analyses WHERE operator IN ('heart_failure_review','affordability_review') ORDER BY patient,operator")))
    finally:db.close()
    return {'code':code_identity(),'source_run':meta['source_run'],'source_digest':meta['source_digest'],
            'snapshot':meta['snapshot'],'reviews_digest':reviews}


def state(status,value=None,reason=None,evidence=None,**extra):
    return {'status':status,'value':value,'reason':reason,'evidence':evidence or [],'current_clinical_state_established':False,**extra}


def reference(record,field):
    return {'record_id':record['record_id'],'patient_id':record['patient_id'],'source_table':record['source_table'],
            'field':field,'value':record.get(field),'source_date':record.get('event_date'),
            'arrival_date':record.get('available_date'),'record_hash':digest(record)}


def resolve(observations,as_of,*,dated=False):
    """No majority voting, averaging conflicts, source-date substitution or copy refresh."""
    eligible=[];excluded=[];undated=[]
    for o in observations:
        if not iso(o.get('arrival_date')) or o['arrival_date']>as_of:
            excluded.append({'reason':'unavailable_or_arrival_unknown','observation':o});continue
        if o.get('subject') not in ('patient','patient_context') or o.get('assertion') not in ('affirmed','negated') or o.get('temporality') in ('historical','resolved'):
            excluded.append({'reason':'subject_assertion_or_historical_context','observation':o});continue
        day=o.get('clinical_date') if dated else o.get('source_date')
        if not iso(day):undated.append(o);continue
        if day>as_of:
            excluded.append({'reason':'future_selected_date','observation':o});continue
        eligible.append((day,o))
    basis='explicit_clinical_date' if dated else 'source_statement_date'
    if undated:
        return state('undated',reason='Some relevant observations cannot be placed on the requested clock; older dated evidence is not silently selected.',
                     evidence=observations,date_basis=basis,excluded=excluded)
    if not eligible:
        return state('historical_or_excluded' if excluded else 'unobserved',reason='No usable observation for this binding.',evidence=observations,date_basis=basis,excluded=excluded)
    newest=max(day for day,_ in eligible);current=[o for day,o in eligible if day==newest]
    signatures={json.dumps(o['value'],sort_keys=True,allow_nan=False) for o in current}
    return state('documented' if len(signatures)==1 else 'conflicting',
                 current[0]['value'] if len(signatures)==1 else None,
                 'Latest source documentation; not a clinical truth assertion.' if len(signatures)==1 else 'Conflicting same-date accounts; no vote or average taken.',
                 current,date_basis=basis,selected_date=newest,prior=[o for day,o in eligible if day<newest],excluded=excluded)


def normalize_dose(value):
    """Bounded single-ingredient dose notation only; never infer a current regimen."""
    if not isinstance(value,str):return {'status':'unresolved','raw':value}
    m=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(mg|mcg)\s*(daily|once daily|qd|twice daily|bid|three times daily|tid)\s*',value,re.I)
    if not m:return {'status':'unresolved','raw':value,'reason':'Unsupported notation; combination doses and PRN regimens are not collapsed.'}
    amount=float(m[1]);frequency={'daily':1,'once daily':1,'qd':1,'twice daily':2,'bid':2,'three times daily':3,'tid':3}[m[3].lower()]
    if not math.isfinite(amount) or amount<=0:return {'status':'unresolved','raw':value}
    return {'status':'normalized_notation','raw':value,'amount_per_dose':amount,'unit':m[2].lower(),
            'doses_per_day':frequency,'nominal_daily_amount':amount*frequency,
            'dose_equivalence_or_target_assessed':False,'actual_use_established':False}


def clinical_date(record):
    fields={'labs':('collection_date','specimen_date'),'financial':('assessment_date',),
            'echo':('observation_date',),'medication_history':('assessment_date',),'referrals':('attendance_date',)}
    values=[record[k] for k in fields.get(record['source_table'],()) if record.get(k) is not None]
    return values[0] if values and all(iso(x) for x in values) and len(set(values))==1 else None


def observation(record,field,value,**extra):
    return {'value':value,'source_date':record.get('event_date'),'arrival_date':record.get('available_date'),
            'clinical_date':clinical_date(record),'assertion':'affirmed','subject':'patient','temporality':'unspecified',
            'source':[reference(record,field)],'authority':'structured source documentation',**extra}


def bound_facts(analyses,records,pid):
    """Resolve each retained fact back to this patient's exact source; dedupe fact IDs."""
    index={r['record_id']:r for r in records};seen=set();observations=defaultdict(list)
    for a in analyses:
        for f in a['source_bound_facts']:
            if f['fact_id'] in seen:continue
            seen.add(f['fact_id'])
            if f['patient_id']!=pid:raise ValueError('Cross-patient clinical fact')
            evidence=f.get('evidence',[])
            if not evidence:continue
            resolved=[]
            for e in evidence:
                r=index.get(e['record_id'])
                if r is None or r['patient_id']!=pid:raise ValueError('Clinical fact source is not in this patient snapshot')
                # Record fields and quotes are checked against the actual source, not an arbitrary ID.
                field=e['field']
                if 'quote' in e:
                    from ..narrative_sources import text_fields
                    raw=next((part['text'] for part in text_fields(r) if part['field']==field),None)
                    if not isinstance(raw,str) or raw[e['start']:e['end']]!=e['quote']:raise ValueError('Clinical fact quote mismatch')
                else:
                    match=re.fullmatch(r'problems\[(\d+)\]',field)
                    if match:
                        try:value=r['problems'][int(match[1])]
                        except (KeyError,IndexError,TypeError):raise ValueError('Clinical fact field missing') from None
                    elif field in r:value=r[field]
                    else:raise ValueError('Clinical fact field not mapped')
                    if e.get('value')!=value:raise ValueError('Clinical fact value mismatch')
                resolved.append((r,e))
            first=min(resolved,key=lambda pair:(pair[0]['event_date'] or '',pair[0]['available_date'],pair[0]['record_id']))
            r,e=first
            o={'value':f['value'],'source_date':r['event_date'],'arrival_date':r['available_date'],
               'clinical_date':clinical_date(r),'assertion':f['assertion'],'subject':f['subject'],'temporality':f['temporality'],
               'source':[dict(ev,record_hash=digest(rr)) for rr,ev in resolved],
               'fact_id':f['fact_id'],'relationship':f.get('relationship'),
               'authority':'unvalidated machine extraction' if f['method']=='context_rules' else 'structured source documentation'}
            observations[f['concept']].append(o)
    return observations


def patient_state(patient,records,analyses,as_of):
    pid=patient['patient_id']
    for r in records:
        if r['patient_id']!=pid or r.get('synthetic') is not True or r.get('identity_status')!='matched':raise ValueError('Synthetic matched patient sources required')
        if not iso(r.get('available_date')) or r['available_date']>as_of or (r.get('event_date') is not None and (not iso(r['event_date']) or r['event_date']>as_of)):raise ValueError('Records outside the source snapshot')
    facts=bound_facts(analyses,records,pid);bindings={};therapy=[]
    hf=[o for key,obs in facts.items() if key.startswith('diagnosis:I50') or key=='diagnosis:heart_failure' for o in obs]
    bindings['hf_documentation']=resolve([{**o,'value':o['assertion']=='affirmed'} for o in hf],as_of)
    bindings['ef_documentation']=resolve(facts['ef'],as_of)
    bindings['ef_clinical']=resolve(facts['ef'],as_of,dated=True)
    financial=[observation(r,'difficulty_paying_medical_bills',r['difficulty_paying_medical_bills']) for r in records
               if r['source_table'] in {'financial','financial_screens'} and type(r.get('difficulty_paying_medical_bills')) is bool]
    financial+=facts['financial_hardship']
    # Distinct copied occurrences are retained as provenance, not additional votes.
    financial=list({digest(o):o for o in financial}.values())
    bindings['financial_report']=resolve(financial,as_of)
    for key,reason in {
      'financial_current':'Current-need confirmation validity interval is not mapped.',
      'assistance_consent':'Program-specific consent evidence binding is not implemented; queue enrollment is a separate staff report.',
      'payer_eligibility':'Effective payer-specific requirements and required inputs are not bound by this contract.',
      'goals':'Confirmed patient goals/preferences are not yet reconciled.',
      'renal_context':'Clinical cause, acute/chronic context and full referral criteria are not implemented.',
    }.items():bindings[key]=state('unsupported',reason=reason)
    for drug in DRUGS:
        rs=[r for r in records if r.get('drug_class')==drug]
        observations=facts['medication:'+drug]
        reported=[o for o in observations if o.get('relationship')=='reported_use']
        exceptions=[o for o in observations if o.get('relationship')=='hold_or_exception_mention']
        supply=supply_reconcile(rs,drug,as_of)
        details={'drug_class':drug,'supply':supply,'reported_use':resolve(reported,as_of),'exception_mentions':resolve(exceptions,as_of),
                 'dose_notation':[{'source':reference(r,'dose'),'normalized':normalize_dose(r.get('dose'))} for r in rs if r.get('dose')],
                 'taking_observation_flags':[reference(r,'medication_taking_observed') for r in rs if 'medication_taking_observed' in r],
                 'flag_meaning':'medication_taking_observed=false means ingestion was not observed. It is not a report that the patient is not taking medication.'}
        therapy.append(details)
        for key,reason in {
          'use':'Current ingestion/use is not established by an order, fill, supply, mention or observation flag.',
          'exception':'An exception mention is not a current, clinically appropriate hold or contraindication assessment.',
          'tolerance':'A recorded or normalized dose is not a maximum tolerated dose assessment.',
        }.items():bindings['therapy.'+drug+'.'+key]=state('unknown_semantics',reason=reason,evidence=reported if key=='use' else exceptions if key=='exception' else details['dose_notation'])
    from .analytic_registry import renal_series
    for name in ('egfr','creatinine','uacr'):
        series=renal_series(records,name,as_of)
        obs=[]
        for point in series['series']:
            ev=point['all_source_evidence'];clinical=point['date'] if point['evidence']['date_basis']=='explicit_collection_date' else None
            obs.append({'value':{'value':point['value'],'unit':point['unit']},'clinical_date':clinical,'source_date':point['date'],
                        'arrival_date':max(e['available_date'] for e in ev),'assertion':'affirmed','subject':'patient','temporality':'unspecified','source':ev})
        resolved=resolve(obs,as_of,dated=True)
        if series['same_date_conflicts'] or series['excluded']:
            resolved=state('conflicting' if series['same_date_conflicts'] else 'unknown_semantics',
                reason='Unresolved measurement conflicts or excluded results prevent assuming a latest adequate measurement.',
                evidence=series['same_date_conflicts']+series['excluded'],dated_observations=resolved)
        bindings['renal.'+name]=resolved
    attendance=[observation(r,'attendance_status',r['attendance_status']) for r in records if r['source_table']=='referrals'
                and 'nephro' in str(r.get('service','')).lower() and r.get('attendance_status') in ('completed','not_attended')]
    bindings['nephrology_attendance']=resolve(attendance,as_of,dated=True)
    from .warehouse_interpretation import interpret
    received=interpret(patient,records,as_of)
    return {'patient_id':pid,'bindings':bindings,'therapy':therapy,'synthetic':True,'clinical_state_verified':False,
            'native_source_accounts':{'relation_counts':received['relation_counts'],'state_counts':received['state_counts'],
                'evidence_acquisition_steps':received['evidence_acquisition_steps'],
                'details_operator':'warehouse_interpretation','clinical_truth_established':False},
            'limits':LIMITS,'review_sources':[a['operator'] for a in analyses]}


def build(path,output):
    path=Path(path);output=Path(output)
    if (output/'run.json').exists():raise ValueError('Choose a new clinical-state artifact directory')
    output.mkdir(parents=True,exist_ok=True);ident=identity(path);as_of=ident['snapshot']['as_of'];start=time.perf_counter()
    src=sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    out=sqlite3.connect(output/'states.sqlite3');counts=Counter();patients=0
    out.executescript('CREATE TABLE IF NOT EXISTS states(patient TEXT PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS evaluations(patient TEXT,contract TEXT,status TEXT,payload TEXT,PRIMARY KEY(patient,contract));')
    try:
        with out:
            for pid,raw in src.execute('SELECT id,payload FROM patients ORDER BY id'):
                records=[json.loads(r[0]) for r in src.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
                analyses=[json.loads(r[0]) for r in src.execute("SELECT payload FROM analyses WHERE patient=? AND operator IN ('heart_failure_review','affordability_review')",(pid,))]
                for a in analyses:
                    if a['source_run']!=ident['source_run'] or a['workflow']['as_of']!=as_of:raise ValueError('Clinical source identity mismatch')
                row=patient_state(json.loads(raw),records,analyses,as_of);patients+=1
                out.execute('INSERT INTO states VALUES (?,?)',(pid,json.dumps(row,allow_nan=False)))
                for name in CONTRACTS:
                    result=evaluate(definition(name),row['bindings'])
                    out.execute('INSERT INTO evaluations VALUES (?,?,?,?)',(pid,name,result['status'],json.dumps(result,allow_nan=False)))
                    counts[name+':'+result['status']]+=1
        if identity(path)!=ident:raise ValueError('Source or code changed during clinical-state build')
        receipt={'schema':VERSION,'synthetic':True,'identity':ident,'patients':patients,'contract_counts':dict(counts),
                 'elapsed_seconds':time.perf_counter()-start,'new_paid_api_calls':0,'limits':LIMITS}
        (output/'run.json').write_text(json.dumps(receipt,indent=2));return receipt
    finally:src.close();out.close()


def attached(path):
    link=json.loads((Path(path)/'clinical-state-link.json').read_text());root=Path(link['path'])
    meta=json.loads((root/'run.json').read_text())
    if meta.get('schema')!=VERSION or meta.get('synthetic') is not True or meta['identity']!=identity(path):raise ValueError('Clinical-state artifact is stale; rebuild and reattach')
    return root,meta


def review(path,patient_ids,contract='all',limit=10):
    if contract!='all':definition(contract)
    if type(limit) is not int or not 1<=limit<=25:raise ValueError('Bounded state preview required')
    root,meta=attached(path);db=sqlite3.connect((root/'states.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    ids=set(patient_ids);names=list(CONTRACTS) if contract=='all' else [contract];counts=Counter();inputs=defaultdict(Counter);preview=[];seen=set()
    try:
        for pid in sorted(ids):
            raw=db.execute('SELECT payload FROM states WHERE patient=?',(pid,)).fetchone()
            if raw is None:raise ValueError('Unknown patient in clinical-state population')
            seen.add(pid);row=json.loads(raw[0]);ev=[]
            for name in names:
                result=json.loads(db.execute('SELECT payload FROM evaluations WHERE patient=? AND contract=?',(pid,name)).fetchone()[0]);ev.append(result);counts[name+':'+result['status']]+=1
                for r in result['requirements']:inputs[r['binding']][r['state']['status']]+=1
            if len(preview)<limit:preview.append({'patient_id':pid,'evaluations':ev,'therapy':row['therapy'] if contract in ('all','hf_therapy') else []})
        if seen!=ids:raise ValueError('Unknown patient in clinical-state population')
        return {'schema':VERSION,'synthetic':True,'contracts':[definition(n) for n in names],
                'patients_evaluated':len(seen),'status_counts':dict(counts),
                'requirement_counts':{k:dict(v) for k,v in sorted(inputs.items())},
                'patients':preview,'patients_returned':len(preview),'source_identity':meta['identity'],
                'new_paid_api_calls':0,'clinical_eligibility_established':False,'limits':LIMITS}
    finally:db.close()
