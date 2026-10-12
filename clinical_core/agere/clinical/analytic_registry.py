"""Executable analytic operators with explicit data requirements and source traces.

These operators expose observations and missing inputs. They never turn absent
documentation into missing care, or descriptive trajectories into a diagnosis.
"""
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import hashlib,json,math,sqlite3
from ..operators import trend, medication, MEDS, admission_episodes
from ..narrative_sources import coverage
from ...contracts import digest

VERSION='warehouse-analytic-registry/2'
def code_identity():
    from ..received_source_catalog import identity as source_catalog_identity
    parent=Path(__file__).parents[1]
    return digest({str(p.name):hashlib.sha256(p.read_bytes()).hexdigest() for p in
      [Path(__file__),parent/'operators.py',parent/'narrative_sources.py',parent/'policy.py',
       Path(__file__).with_name('program_registry.py'),Path(__file__).with_name('cohorts.py'),
       Path(__file__).with_name('clinical_events.py'),Path(__file__).with_name('population_math.py'),
       Path(__file__).with_name('medication_reconciliation.py'),Path(__file__).with_name('warehouse_interpretation.py'),
       parent/'mathematics.py',parent.parent/'process_monitoring.py',parent/'received_source_catalog.py']}|{'received_source_catalog':source_catalog_identity()})
CATALOG={
 'warehouse_interpretation':{'purpose':'Reconcile native source statements and missing dependencies; all_observed only.',
   'inputs':['received native records and explicit same-patient links'],
   'limits':'Source reports only. No ingestion, completed care, eligibility or legal-validity inference.'},
 'clinical_timeline':{'purpose':'Separate explicit clinical dates from source event, record and arrival dates.',
   'inputs':['explicit service/fill/dispense/collection date fields'],
   'limits':'No inferred dates from narrative. Undated and conflicting events remain unresolved.'},
 'source_volume_monitor':{'purpose':'Run existing CUSUM/EWMA on complete-month observed source counts for the selected cohort.',
   'inputs':['source-event-dated notes, pharmacy and labs','at least eight months with variable six-month reference'],
   'limits':'Engineering reference, not a clinical warning. Source capture, enrollment and seasonality are unadjusted.'},
 'claims_runout':{'purpose':'Calculate observed service-month/payment-lag paid-amount triangles for the selected cohort.',
   'inputs':['received claims with service, payment and availability dates and paid amounts'],
   'limits':'Observed claims only. No ultimate-cost projection or causal savings estimate.'},
 'heart_failure_review':{'purpose':'Open existing heart-failure evidence-review candidates with EF context and all four medication classes.',
   'inputs':['declared clinical-workflow snapshot','source-bound EF, medication, hold and dose evidence'],
   'limits':'Candidate documentation cohort, not confirmed HFrEF. Actual use, GDMT eligibility and maximum tolerated dose remain unresolved.'},
 'affordability_review':{'purpose':'Open existing affordability review candidates with structured/narrative source reconciliation.',
   'inputs':['declared clinical-workflow snapshot','reported affordability and source evidence'],
   'limits':'Reported support need, not financial-assistance qualification.'},
 'program_rule_review':{'purpose':'Evaluate versioned configured program rules against explicit observed input bindings.',
   'inputs':['quoted/versioned program specification','mapped input values with evidence'],
   'limits':'Only configured rule predicates; new unknown inputs abstain. Rule matches do not establish full program eligibility.'},
 'kidney_trajectory':{'purpose':'Reconcile observed renal measurements and describe eGFR/creatinine trajectories.',
   'inputs':['dated eGFR or creatinine, valid units','ACR, demographics and verified dates for further risk work'],
   'limits':'Source-date trajectories do not diagnose AKI, chronic progression or referral eligibility.'},
 'medication_evidence':{'purpose':'Compare orders, claim transactions, medication history and administration for one medication class.',
   'inputs':['source-specific medication records','drug class'],
   'limits':'Documented supply is not ingestion, adherence, GDMT eligibility or maximum tolerated dose.'},
 'source_completeness':{'purpose':'Inventory observed sources, mapped text and unread documents.',
   'inputs':['observed source records'], 'limits':'Unobserved source coverage and care remain unknown.'},
 'admission_patterns':{'purpose':'Expose observed ADT episode/readmission patterns with incomplete follow-up.',
   'inputs':['ADT admissions and discharges'], 'limits':'Source pairing is provisional; admission causes and preventability are not established.'},
}
ALIASES={
 'egfr':{'egfr','33914-3','62238-1'},
 'creatinine':{'creatinine','serum creatinine','2160-0'},
 'uacr':{'uacr','acr','urine albumin creatinine ratio','albumin creatinine ratio','9318-7','14959-1'},
}
UNITS={'egfr':{'ml/min/1.73m2','ml/min/1.73m²','ml/min/1.73m^2'},'creatinine':{'mg/dl'},'uacr':{'mg/g'}}

def renal_series(records,concept,as_of,*,reference_records=None):
    from .warehouse_interpretation import index,laboratory_status
    related=index(records if reference_records is None else reference_records)
    groups=defaultdict(list);excluded=[];conversions=0
    for r in records:
        if r['source_table']!='labs' or not {str(r.get('test','')).lower(),str(r.get('code','')).lower()}&ALIASES[concept]:continue
        ev={'record_id':r['record_id'],'source_table':'labs','field':'value','event_date':r['event_date'],
            'available_date':r['available_date'],'raw_value':r.get('value'),'raw_unit':r.get('unit'),
            'source_system':r.get('source_system'),'assay':r.get('assay')}
        reason=None
        if laboratory_status(r) not in ('final','corrected','amended'):reason='result_status_not_final'
        try:v=float(r['value'])
        except (KeyError,TypeError,ValueError):v=None;reason=reason or 'non_numeric_value'
        if v is not None and (not math.isfinite(v) or v<0):reason=reason or 'invalid_numeric_value'
        unit=str(r.get('unit','')).lower().replace(' ','');conversion=None
        if concept=='creatinine' and unit in {'umol/l','µmol/l','μmol/l'} and v is not None:
            v/=88.4;unit='mg/dl';conversion='µmol/L divided by 88.4 to mg/dL';conversions+=1
        if unit not in UNITS[concept]:reason=reason or 'unsupported_or_missing_unit'
        from .clinical_events import bind_event
        binding=bind_event(r,as_of,related)
        if binding['clinical_date_status'] in ('conflicting_explicit_dates','invalid_explicit_date','unresolved_source_link'):
            reason=reason or binding['clinical_date_status']
        measured=binding['clinical_date']
        basis='explicit_collection_date' if measured else 'source_event_date_surrogate';measured=measured or r['event_date']
        try:date.fromisoformat(measured)
        except (TypeError,ValueError):reason=reason or 'invalid_measurement_date'
        if not reason and measured>as_of:reason='future_measurement_date'
        ev.update(measurement_date=measured,date_basis=basis,conversion=conversion,
                  clinical_date_evidence=binding.get('clinical_date_evidence',[]),
                  result_status_fields={k:r[k] for k in ('status','result_status') if k in r})
        if reason:excluded.append({'reason':reason,'evidence':ev});continue
        groups[measured].append({'value':v,'unit':unit,'evidence':ev})
    series=[];conflicts=[];duplicate_observations=0
    for day,rows in sorted(groups.items()):
        distinct={(x['value'],x['unit']) for x in rows}
        if len(distinct)>1:
            conflicts.append({'date':day,'reason':'same_date_disagreement_not_averaged','observations':rows});continue
        duplicate_observations+=len(rows)-1
        series.append({'date':day,'value':rows[0]['value'],'unit':rows[0]['unit'],
                       'evidence':rows[0]['evidence'],'all_source_evidence':[x['evidence'] for x in rows]})
    trajectory=trend(series)
    if trajectory['status']=='computed':
        trajectory['measurement_time_verified']=all(x['evidence']['date_basis']=='explicit_collection_date' for x in series)
        trajectory['independence_and_error_model_validated']=False
        trajectory['interval_scope']='Nominal Theil-Sen interval; not a validated clinical risk interval.'
    comparison=None
    if len(series)>=2:
        a,b=series[0],series[-1]
        comparison={'first_date':a['date'],'last_date':b['date'],'first_value':a['value'],'last_value':b['value'],
          'absolute_change':b['value']-a['value'],'relative_change_percent':100*(b['value']-a['value'])/a['value'] if a['value'] else None,
          'evidence':[a['evidence'],b['evidence']],'interpretation':'Observed endpoints; no clinical cause or severity assigned.'}
    return {'concept':concept,'series':series,'trajectory':trajectory,'endpoint_comparison':comparison,
            'excluded':excluded,'same_date_conflicts':conflicts,'duplicate_observations_retaining_provenance':duplicate_observations,
            'converted_observations':conversions,'clinical_state_established':False}

def kidney(patient,records,as_of,*,reference_records=None):
    axes={c:renal_series(records,c,as_of,reference_records=reference_records) for c in ALIASES}
    missing=[]
    for concept,value in axes.items():
        if not value['series']:missing.append('usable_'+concept)
    if not patient.get('birth_date'):missing.append('birth_date')
    if not patient.get('sex'):missing.append('sex')
    if not all(x['evidence']['date_basis']=='explicit_collection_date' for a in axes.values() for x in a['series']):missing.append('verified_measurement_dates')
    t=axes['egfr']['trajectory'];direction='insufficient_data'
    if t['status']=='computed':
        low,high=t['slope_interval_per_year'];direction='declining_source_date_pattern' if high<0 else 'rising_source_date_pattern' if low>0 else 'direction_uncertain'
    referrals=[{'record_id':r['record_id'],'event_date':r['event_date'],'available_date':r['available_date'],
                'service':r.get('service'),'status':r.get('status')} for r in records
               if r['source_table']=='referrals' and 'nephro' in str(r.get('service','')).lower()]
    return {'patient_id':patient['patient_id'],'operator':'kidney_trajectory','synthetic':True,'measurements':axes,
            'egfr_pattern':direction,'missing_inputs':missing,'kidney_failure_probability':None,
            'risk_calculation_status':'not_implemented_and_required_inputs_not_assumed',
            'nephrology_referral_records':referrals,'acute_vs_chronic':'unresolved_requires_verified_dates_and_illness_context',
            'recommended_review':'Retrieve missing renal inputs and reconcile dates, corrections and outside nephrology records.',
            'care_gap_established':False,'clinical_execution_authorized':False}

def patient_operator(operator,patient,records,as_of,drug_class=None,*,reference_records=None):
    if operator=='warehouse_interpretation':
        from .warehouse_interpretation import interpret
        return interpret(patient,records,as_of)
    if operator=='clinical_timeline':
        from .clinical_events import timeline
        return timeline(patient,records,as_of)
    if operator=='kidney_trajectory':return kidney(patient,records,as_of,reference_records=reference_records)
    if operator=='source_completeness':
        return {'patient_id':patient['patient_id'],'operator':operator,'synthetic':True,**coverage(records),
                'coverage_status':'Only contributing source records observed; external care completeness unknown.'}
    if operator=='admission_patterns':
        return {'patient_id':patient['patient_id'],'operator':operator,'synthetic':True,
                'episodes':admission_episodes(records,as_of),'cause':'not_established','care_gap_established':False}
    if operator=='medication_evidence':
        from .medication_reconciliation import reconcile
        if drug_class not in MEDS:raise ValueError('Supported medication class required')
        rows=[r for r in records if r.get('drug_class')==drug_class]
        return {'patient_id':patient['patient_id'],'operator':operator,'synthetic':True,
                'drug_class':drug_class,'observed_sources':dict(Counter(r['source_table'] for r in rows)),
                'transaction_types':dict(Counter(str(r.get('transaction_type')) for r in rows if r['source_table']=='pharmacy_claims')),
                'supply':reconcile(records,drug_class,as_of),
                'supply_limitations':['Actual fill/dispense dates required; missing dates are explicit exclusions.',
                                     'Recorded transaction order and cross-source disagreement exposed; no universal claims adjudication claim.'],
                'max_tolerated_dose':'not_established','care_gap_established':False}
    raise ValueError('Unknown analytic operator')

def compute_all(path):
    path=Path(path);meta=json.loads((path/'run.json').read_text());db=sqlite3.connect(path/'stack.sqlite3')
    counts=Counter();source_counts=Counter();unread=0
    try:
        with db:
            for pid,payload in db.execute('SELECT id,payload FROM patients ORDER BY id').fetchall():
                patient=json.loads(payload);records=[json.loads(r[0]) for r in db.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
                for op in ('kidney_trajectory','source_completeness','admission_patterns','warehouse_interpretation'):
                    result=patient_operator(op,patient,records,meta['snapshot']['as_of'])
                    db.execute('INSERT OR REPLACE INTO analyses VALUES (?,?,?)',(pid,op,json.dumps(result,allow_nan=False)))
                    if op=='kidney_trajectory':
                        counts[result['egfr_pattern']]+=1
                        for c in ALIASES:
                            counts['patients_with_usable_'+c]+=bool(result['measurements'][c]['series'])
                            counts['same_date_conflicts_'+c]+=len(result['measurements'][c]['same_date_conflicts'])
                    elif op=='source_completeness':
                        source_counts.update(result['records_by_source']);unread+=len(result['documents_without_received_text'])
        report={'version':VERSION,'synthetic':True,'patients':meta['patients'],'kidney':dict(counts),
                'records_by_source':dict(source_counts),'documents_without_received_text':unread,
                'new_paid_api_calls':0,'operators':CATALOG,
                'code_identity':code_identity(),
                'scope':'Warehouse calculations and data readiness; no measured clinical accuracy or outcome benefit.'}
        (path/'operators.json').write_text(json.dumps(report,indent=2));return report
    finally:db.close()

def attach_clinical_reviews(path,clinical_run):
    from ..warehouse import lines
    path,clinical_run=Path(path),Path(clinical_run)
    meta=json.loads((path/'run.json').read_text());source=json.loads((clinical_run/'run.json').read_text())
    if source.get('synthetic') is not True or source['cache_key_digest']!=meta['source_run']:raise ValueError('Clinical workflow snapshot mismatch')
    db=sqlite3.connect(path/'stack.sqlite3');counts=Counter()
    try:
        with db:
            for row in lines(clinical_run/'patients.jsonl'):
                for workflow,op in [('heart_failure','heart_failure_review'),('affordability','affordability_review')]:
                    result=row['workflows'][workflow]
                    references=set(result['support_fact_ids']+result['excluded_fact_ids'])
                    if workflow=='heart_failure':
                        for m in result['medications']:
                            references.update(m['text_fact_ids']);references.update(x['fact_id'] for x in m['dose_sources'])
                    bound=[f for f in row['state']['facts'] if f['fact_id'] in references]
                    payload={'patient_id':row['patient_id'],'operator':op,'synthetic':True,'workflow':result,
                             'source_bound_facts':bound,'source_run':source['cache_key_digest'],
                             'clinical_execution_authorized':False}
                    db.execute('INSERT OR REPLACE INTO analyses VALUES (?,?,?)',(row['patient_id'],op,json.dumps(payload,allow_nan=False)))
                    counts[op+':patients']+=1;counts[op+':review_candidates']+=result['review_candidate']
        receipt={'synthetic':True,'source_run':source['cache_key_digest'],'counts':dict(counts),
                 'scope':'Previously declared review workflows now callable by the new planner. Clinical rules and source model labels unchanged.'}
        (path/'clinical-review-link.json').write_text(json.dumps(receipt,indent=2));return receipt
    finally:db.close()

def execute(path,operator,*,patient=None,drug_class=None,view='all',limit=10,cohort=None,analysis_window=None,patient_ids=None):
    if operator not in CATALOG or view not in ('all','declining','missing_inputs','source_conflicts'):raise ValueError('Unsupported operator/view')
    if view!='all' and operator!='kidney_trajectory':raise ValueError('View only applies to kidney trajectory')
    if type(limit) is not int or not 1<=limit<=50:raise ValueError('Bounded page required')
    from .cohorts import select
    from .clinical_events import validate_window,scoped_records
    path=Path(path);meta=json.loads((path/'run.json').read_text())
    window=validate_window(analysis_window,meta['snapshot']['as_of'])
    if window['basis']!='all_observed' and operator in ('heart_failure_review','affordability_review','program_rule_review','warehouse_interpretation'):
        raise ValueError('This operator requires the full snapshot and its source dependencies')
    if window['basis'] not in ('all_observed','source_event') and operator=='source_volume_monitor':
        raise ValueError('Source-volume monitor requires source-event dates')
    selection=select(path,cohort,patient=patient,limit=limit,patient_ids=patient_ids);ids=selection['patient_ids'];eligible=set(ids)
    db=sqlite3.connect(path/'stack.sqlite3')
    try:
        operator_meta=json.loads((path/'operators.json').read_text())
        code=code_identity()
        if operator_meta['code_identity']!=code:raise ValueError('Analytic operator code changed; recompute warehouse operators')
        if patient and not db.execute('SELECT 1 FROM patients WHERE id=?',(patient,)).fetchone():raise ValueError('Unknown patient')
        installed=None
        if operator=='program_rule_review':
            from .program_registry import packages,evaluate_patient
            installed=packages(path)
        cache_key=digest([VERSION,code,meta['source_digest'],operator,patient,drug_class,view,limit,selection['id'],window,
                         digest(installed) if installed is not None else None])
        old=db.execute('SELECT payload FROM result_cache WHERE id=?',(cache_key,)).fetchone()
        if old:return {**json.loads(old[0]),'cache_hit':True,
                       'compute':{'new_patient_computations':0,'patient_cache_hits':0,'aggregate_result_replayed':True}}
        scopes=[]
        patient_cache_hits=0;patient_computations=0
        if operator in ('source_volume_monitor','claims_runout'):
            from .population_math import calculate
            records=[]
            streams=('notes','labs','pharmacy_claims') if operator=='source_volume_monitor' else ('medical_claims',)
            for pid,payload in db.execute('SELECT patient,payload FROM records WHERE stream IN ('+','.join('?' for _ in streams)+') ORDER BY id',streams):
                if pid in eligible:records.append(json.loads(payload))
            records,scope=scoped_records(records,window,meta['snapshot']['as_of']);scopes=[scope]
            calculation=calculate(operator,records,meta['snapshot']['as_of'])
            rows=[];summary={'patients':len(ids),'records_analyzed':len(records)}
        elif operator in ('heart_failure_review','affordability_review'):
            if not (path/'clinical-review-link.json').exists():raise ValueError('Clinical workflow results not attached')
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM analyses WHERE operator=?'+(' AND patient=?' if patient else '')+' ORDER BY patient',[operator,*([patient] if patient else [])])]
            rows=[r for r in rows if r['patient_id'] in eligible]
            evaluated=len(rows);status_counts=dict(Counter(r['workflow']['status'] for r in rows))
            if not patient:rows=[r for r in rows if r['workflow']['review_candidate']]
            summary={'patients':len(rows),'patients_evaluated':evaluated,'status_counts':status_counts,
                     'cohort':'Existing documentation-based review candidates; no disease prevalence claim.'}
        elif operator=='program_rule_review':
            rows=[];counts=Counter()
            for pid in ids:
                p=json.loads(db.execute('SELECT payload FROM patients WHERE id=?',(pid,)).fetchone()[0])
                records=[json.loads(r[0]) for r in db.execute('SELECT payload FROM records WHERE patient=?',(pid,))]
                renal=json.loads(db.execute("SELECT payload FROM analyses WHERE patient=? AND operator='kidney_trajectory'",(pid,)).fetchone()[0])
                result=evaluate_patient(p,records,renal,meta['snapshot']['as_of'],installed)
                for e in result['evaluations']:counts[e.get('policy_id','not_effective')+':'+e['status']]+=1
                rows.append(result)
            summary={'patients':len(rows),'program_versions':len(installed),'status_counts':dict(counts)}
        elif operator in ('medication_evidence','clinical_timeline') or window['basis']!='all_observed':
            if operator=='medication_evidence' and drug_class not in MEDS:raise ValueError('Explicit supported medication class required')
            rows=[]
            for pid in ids:
                pkey=digest(['scoped-patient-analysis/1',code,meta['source_digest'],operator,pid,drug_class,window])
                cached=db.execute('SELECT payload FROM result_cache WHERE id=?',(pkey,)).fetchone()
                if cached:
                    row=json.loads(cached[0]);rows.append(row);scopes.append(row['record_scope']);patient_cache_hits+=1;continue
                p=json.loads(db.execute('SELECT payload FROM patients WHERE id=?',(pid,)).fetchone()[0])
                records=[json.loads(r[0]) for r in db.execute('SELECT payload FROM records WHERE patient=?',(pid,))]
                selected,scope=scoped_records(records,window,meta['snapshot']['as_of']);scopes.append(scope)
                if operator=='clinical_timeline':
                    from .clinical_events import timeline
                    unknown=set(scope['unknown_date_record_ids'])
                    row=timeline(p,selected,meta['snapshot']['as_of'],[r for r in records if r['record_id'] in unknown],reference_records=records)
                else:row=patient_operator(operator,p,selected,meta['snapshot']['as_of'],drug_class,reference_records=records)
                row['record_scope']=scope;rows.append(row)
                db.execute('INSERT OR REPLACE INTO result_cache VALUES (?,?)',(pkey,json.dumps(row,allow_nan=False)))
                patient_computations+=1
            if operator=='medication_evidence':summary={'patients':len(rows),'explicit_dispense_date_supply_documented':sum(r['supply']['status']=='supply_documented' for r in rows),
                     'with_order_records':sum(bool(r['supply']['order_evidence']) for r in rows),
                     'with_source_disagreements':sum(bool(r['supply']['source_disagreements']) for r in rows),
                     'with_excluded_dispense_observations':sum(bool(r['supply']['excluded_dispense_observations']) for r in rows),
                     'with_transaction_issues':sum(bool(r['supply']['transaction_issues']) for r in rows)}
            else:summary={'patients':len(rows)}
        else:
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM analyses WHERE operator=?'+(' AND patient=?' if patient else '')+' ORDER BY patient',[operator,*([patient] if patient else [])])]
            rows=[r for r in rows if r['patient_id'] in eligible]
            summary={'patients':len(rows)}
        if operator=='kidney_trajectory':
            if view=='declining':rows=[r for r in rows if r['egfr_pattern']=='declining_source_date_pattern']
            if view=='missing_inputs':rows=[r for r in rows if r['missing_inputs']]
            if view=='source_conflicts':rows=[r for r in rows if any(m['same_date_conflicts'] for m in r['measurements'].values())]
            summary['patients']=len(rows)
        if operator=='medication_evidence':
            rows.sort(key=lambda r:(not bool(r['supply']['source_disagreements']),
                                    not bool(r['supply']['excluded_dispense_observations']),r['patient_id']))
            summary['patient_preview_order']='Source disagreements, then excluded dispense observations, then patient ID; no inferred clinical risk score.'
        result={'schema':VERSION,'synthetic':True,'operator':operator,'view':view,'summary':summary,'patients':rows[:limit],
                'patients_returned':min(limit,len(rows)),'snapshot':meta['snapshot'],'requirements':CATALOG[operator],
                'cohort':{'id':selection['id'],'specification':selection['specification'],'counts':selection['counts'],
                          'examples':selection['examples'],'scope':selection['scope']},
                'analysis_window':window,'date_scope':{
                    'records_selected':sum(s['records_selected'] for s in scopes),
                    'records_with_unknown_date':sum(s['records_with_unknown_date'] for s in scopes),
                    'records_outside_window':sum(s['records_outside_window'] for s in scopes)} if scopes else None,
                'compute':{'new_patient_computations':patient_computations,'patient_cache_hits':patient_cache_hits,
                           'aggregate_result_replayed':False},
                'new_paid_api_calls':0,'care_gap_established':False,'clinical_execution_authorized':False,'cache_hit':False}
        if operator in ('source_volume_monitor','claims_runout'):result['calculation']=calculation
        with db:db.execute('INSERT OR REPLACE INTO result_cache VALUES (?,?)',(cache_key,json.dumps(result,allow_nan=False)))
        return result
    finally:db.close()
