"""Native received-source meanings and explicit same-patient clock dependencies.

This adapter describes source statements. It does not establish ingestion,
clinical eligibility, portable order validity, causality, or unobserved care.
"""
from collections import Counter,defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
import hashlib,json
from ...contracts import digest

VERSION='native-warehouse-interpretation/1'
ADAPTER='original-cohort-warehouse-adapter/1'
DIRECT_CLOCKS={
 'labs':('specimen_time',),'laboratory_specimens':('collected_time',),
 'encounters':('start_time',),'procedures':('performed_time',),
 'imported_documents':('service_date',),'medication_orders':('start_time',),
 'code_status_orders':('effective_time',),
}
# Each relation retains its own domain and evidence: an order cannot cancel or
# corroborate a reported-use statement, nor can a paid claim become a pickup.
STATEMENTS={
 'medication_orders':('medication','prescription_order',('medication_name','dose','dose_unit','frequency','status','prescription_id')),
 'medication_lists':('medication','listed_medication',('name','dose','frequency','indication')),
 'pharmacy_dispenses':('medication','pharmacy_fulfilment',('medication_name','status','quantity','quantity_unit','days_supply_reported','fill_id','completion_for_fill_id','payment_channel')),
 'pharmacy_history':('medication','reported_pharmacy_history',('medication_name','history_basis','reported_status','pickup_status','status','days_supply_reported','original_source_record_id','original_dispense_record_id')),
 'pharmacy_claim_transactions':('medication','claim_adjudication',('transaction_status','claim_id','medication_order_record_id','original_transaction_record_id','reject_code')),
 'pharmacy_claims':('medication','claim_adjudication',('medication_name','transaction_status','status','days_supply_reported','payment_channel')),
 'medication_administrations':('medication','administration_record',('medication_name','administration_status','dose_given','dose_unit','route','not_given_reason','order_record_id')),
 'medication_intent_changes':('medication','prescribing_intent_change',('status','medication_order_record_id','replacement_order_record_id')),
 'medication_followup_reports':('medication','reported_use',('medication_name','reported_intent','respondent','days_used_reported','report_window_days','remaining_quantity_reported','pharmacy_prescription_id')),
 'referrals':('care','referral_request',('status','specialty','requested_specialty','referral_type','requested_service','completion_status','patient_agrees_to_contact')),
 'referral_status_updates':('care','referral_followup_report',('status','status_source','service_performed_confirmed','referral_record_id')),
 'outside_report_status':('care','outside_service_documentation',('status','performed_service_documented','referral_record_id','imported_record_id')),
 'financial_screens':('access','reported_affordability',('difficulty_paying_medical_bills','respondent','income_exact')),
 'patient_billing_accounts':('finance','billing_account_snapshot',('account_id','status','balance_cents','patient_responsibility_cents','currency','previous_account_record_id')),
 'advance_care_documents':('goals','transcribed_document',('document_id','document_type','extracted_treatment_fields','signature_observations','legal_validity','copy_quality','duplicate_of_record_id')),
 'advance_care_document_status':('goals','document_status',('document_id','status','source_document_record_id')),
 'code_status_orders':('goals','documented_order',('status','scope','cpr_in_arrest','intubation','nutrition','hospital_transfer','consenting_party','portable_order_validity','source_document_record_id')),
 'reported_medication_entries':('medication','reported_product_use',('medication_name_reported','use_statement','dose_reported','frequency_reported','information_source','directions_verification','prescriber_stop_order_established','administration_observed','interview_record_id','historical_list_record_id')),
 'medication_history_interviews':('medication','medication_history_scope',('respondent','information_available','prescription_products_asked','inhalers_injections_topicals_eye_drops_asked','nonprescription_and_supplements_asked','additional_unlisted_products_response','permission_to_contact_reported_pharmacy','completeness')),
 'medication_information_requests':('medication','medication_information_request',('interview_record_id','destination_organization_id','request_status','requested_information','requested_medication_names','permission_documented_in_record_id','prescription_order','original_request_record_id')),
 'pharmacy_information_responses':('medication','pharmacy_information_report',('request_record_id','response_status','search_window_days','items','other_pharmacies_searched','current_ingestion_confirmed')),
 'received_pharmacy_information':('medication','imported_pharmacy_information',('request_record_id','original_response_record_id','original_organization_id','response_status','items','capture_channel')),
 'medication_review_decisions':('medication','medication_review_record',('interview_record_id','received_pharmacy_information_record_id','reviewing_clinician_id','entries','new_prescriptions_issued','historical_list_entries_deleted','review_status')),
 'medication_clarification_contacts':('medication','medication_clarification_report',('review_decision_record_id','communication_result','patient_response','directions_newly_verified','new_prescription_or_stop_instruction')),
 'allergy_history_reviews':('allergy','reported_allergy_history',('interview_record_id','history_status','reported_reactions','historical_allergy_record_ids','verification_status','onset_date','allergy_testing_performed','existing_alerts_removed')),
}
LINK_FIELDS={'medication_order_record_id':{'medication_orders'},'order_record_id':{'medication_orders'},
 'replacement_order_record_id':{'medication_orders'},'referral_record_id':{'referrals'},
 'imported_record_id':{'imported_documents'},'original_dispense_record_id':{'pharmacy_dispenses'},
 'source_document_record_id':{'advance_care_documents'},
 'interview_record_id':{'medication_history_interviews'},'historical_list_record_id':{'medication_lists'},
 'request_record_id':{'medication_information_requests'},'original_request_record_id':{'medication_information_requests'},
 'permission_documented_in_record_id':{'medication_history_interviews'},
 'original_response_record_id':{'pharmacy_information_responses'},
 'received_pharmacy_information_record_id':{'received_pharmacy_information'},'review_decision_record_id':{'medication_review_decisions'}}


from ..received_source_catalog import DEFINITIONS as SOURCE_DEFINITIONS, identity as source_catalog_identity
STATEMENTS.update({table:(s['domain'],s['relation'],tuple(s['fields'])) for table,s in SOURCE_DEFINITIONS.items()})
DIRECT_CLOCKS.update({table:tuple(s['explicit_clinical_clock_fields']) for table,s in SOURCE_DEFINITIONS.items() if s['explicit_clinical_clock_fields']})


def identity():return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),source_catalog_identity()])
def native(r):return r.get('_warehouse_source',{}).get('adapter')==ADAPTER

def laboratory_status(r):
    values=[r[k] for k in ('status','result_status') if r.get(k) is not None] if native(r) else [r.get('status')]
    return values[0] if values and len(set(values))==1 else None


def index(records):
    by_id={};encounters=defaultdict(list)
    for r in records:
        if r['record_id'] in by_id:raise ValueError('Duplicate source record identity')
        by_id[r['record_id']]=r
        if r['source_table']=='encounters' and r.get('encounter_id'):
            encounters[(r['patient_id'],r['encounter_id'])].append(r)
    return {'records':by_id,'encounters':encounters}


def reference(r,field):
    return {'record_id':r['record_id'],'patient_id':r['patient_id'],'source_table':r['source_table'],
            'field':field,'value':r.get(field),'record_hash':digest(r),
            'source_date':r.get('event_date'),'arrival_date':r.get('available_date')}


def visible(r,asof):
    value=r.get('available_date')
    return isinstance(value,str) and len(value)==10 and day(value)[0]==value and value<=asof


def day(value):
    if not isinstance(value,str):return None,None
    try:
        if len(value)==10:
            d=date.fromisoformat(value);return (value,None) if d.isoformat()==value else (None,None)
        dt=datetime.fromisoformat(value)
        if 'T' not in value or dt.tzinfo is None:return None,None
        # Keep the explicitly represented calendar day; retain the original
        # offset in evidence. UTC is used only to compare exact instants.
        return dt.date().isoformat(),dt.astimezone(timezone.utc).isoformat()
    except ValueError:return None,None


def clock(r,asof,related=None,legacy_fields=(),role=None):
    ix=related or index([r]);evidence=[];issues=[]
    def collect(source,fields):
        if source['patient_id']!=r['patient_id']:
            issues.append({'reason':'cross_patient_link_rejected','record_id':source['record_id']});return
        if not visible(source,asof):
            issues.append({'reason':'linked_source_not_available_by_cutoff','record_id':source['record_id']});return
        for f in fields:
            if source.get(f) is not None:evidence.append(reference(source,f))
    collect(r,tuple(dict.fromkeys((*legacy_fields,*DIRECT_CLOCKS.get(r['source_table'],())))))
    if r['source_table']=='labs' and r.get('specimen_record_id'):
        target=ix['records'].get(r['specimen_record_id'])
        if target is None:issues.append({'reason':'referenced_specimen_not_received','record_id':r['specimen_record_id']})
        elif target['source_table']!='laboratory_specimens':issues.append({'reason':'wrong_linked_source_type','record_id':target['record_id']})
        else:collect(target,('collected_time',))
    if r['source_table']=='adt':
        kind=role or r.get('event');field={'admission':'start_time','observation_start':'start_time','discharge':'end_time'}.get(kind)
        if field:
            rows=ix['encounters'].get((r['patient_id'],r.get('encounter_id')),[])
            allowed={'inpatient'} if kind=='admission' else {'observation'} if kind=='observation_start' else {'inpatient','observation'}
            rows=[x for x in rows if x.get('type') in allowed]
            if not rows:issues.append({'reason':'matching_encounter_not_received','encounter_id':r.get('encounter_id')})
            for target in rows:collect(target,(field,))
    values=[day(e['value']) for e in evidence];days={d for d,_ in values};instants={t for _,t in values if t}
    status='not_observed';value=None
    if issues:status='unresolved_source_link'
    elif evidence and None in days:status='invalid_explicit_date'
    elif len(days)>1 or len(instants)>1:status='conflicting_explicit_dates'
    elif evidence:
        value=next(iter(days));status='explicit_future_date' if value>asof else 'explicit_date'
    return {'clinical_date':value,'clinical_date_status':status,
        'clinical_date_fields':{e['record_id']+'.'+e['field']:e['value'] for e in evidence},
        'clinical_date_evidence':evidence,'clock_link_issues':issues,
        'date_projection':'Calendar day of explicit date/offset timestamp; no arrival or signature fallback.',
        'clock_semantics':('prescription_intended_start' if r['source_table']=='medication_orders' else
                           'documented_order_effective_time' if r['source_table']=='code_status_orders' else 'source_explicit_event')}


def statement(r,asof,ix):
    spec=STATEMENTS.get(r['source_table'])
    if not spec:return None
    domain,relation,fields=spec;values={f:r[f] for f in fields if f in r};links=[];issues=[]
    link_spec=SOURCE_DEFINITIONS.get(r['source_table'],{}).get('links',LINK_FIELDS)
    for f,streams in link_spec.items():
        raw=r.get(f)
        if raw is None:continue
        refs=raw if isinstance(raw,list) else [raw]
        for position,ref in enumerate(refs):
            field=f+'['+str(position)+']' if isinstance(raw,list) else f
            linked=ix['records'].get(ref) if isinstance(ref,str) else None;reason=None
            if not isinstance(ref,str) or not ref:reason='invalid_source_reference'
            elif linked is None:reason='referenced_source_not_received'
            elif linked['patient_id']!=r['patient_id']:reason='cross_patient_link_rejected'
            elif not streams:reason='source_reference_type_unmapped'
            elif linked['source_table'] not in streams:reason='wrong_linked_source_type'
            elif not visible(linked,asof):reason='linked_source_not_available_by_cutoff'
            if reason:issues.append({'field':field,'record_id':ref,'reason':reason})
            else:links.append({'field':field,'target':reference(linked,'record_id')})
    # Only a report is normalized. No statement here establishes ingestion.
    reported_use=None
    if relation=='reported_use':reported_use={'using':True,'not_using':False,'not_started':False,'ran_out':False}.get(r.get('reported_intent'))
    entity=r.get('pharmacy_prescription_id') or r.get('prescription_id') or r.get('account_id') or r.get('document_id')
    entity=entity or r.get('medication_order_record_id') or r.get('referral_record_id')
    if relation=='reported_product_use':entity=entity or r.get('historical_list_record_id')
    entity=entity or str(r.get('medication_name',r.get('medication_name_reported',r.get('name',r['record_id'])))).casefold()
    return {'id':digest([VERSION,r,relation]),'patient_id':r['patient_id'],'domain':domain,'relation':relation,
        'entity':entity,'values':values,'reported_use':reported_use,'source_date':r.get('event_date'),
        'evidence':[reference(r,f) for f in fields if f in r],'linked_sources':links,'link_issues':issues,
        'clinical_truth_established':False,'authority':'Literal received source statement',
        'boundary':'Prepared supply is not pickup; adjudication is not use; unconfirmed completion is unknown; documents are not legal or clinical validation.'}


def interpret(patient,records,asof):
    pid=patient['patient_id']
    if any(r['patient_id']!=pid for r in records) or any(r.get('synthetic') is not True or not visible(r,asof) for r in records if native(r)):
        raise ValueError('Visible same-patient synthetic records required')
    ix=index(records);rows=[s for r in records if native(r) for s in [statement(r,asof,ix)] if s]
    groups=defaultdict(list)
    for s in rows:groups[(s['domain'],s['relation'],s['entity'])].append(s)
    states=[];steps=[]
    for (domain,relation,entity),rr in sorted(groups.items()):
        undated=any(day(r['source_date'])[0] is None for r in rr)
        newest=max((r['source_date'] for r in rr if r['source_date']),default=None)
        current=rr if undated else [r for r in rr if r['source_date']==newest]
        signatures={digest({k:v for k,v in r['values'].items() if not k.endswith('_id') and k not in ('respondent','status_source')}) for r in current}
        issues=[x for r in current for x in r['link_issues']]
        status='undated' if undated else 'source_link_unresolved' if issues else 'conflicting' if len(signatures)>1 else 'documented_source_statement'
        states.append({'domain':domain,'relation':relation,'entity':entity,'status':status,'selected_source_date':None if undated else newest,
            'selected_statement_ids':[r['id'] for r in current],'prior_statement_ids':[r['id'] for r in rr if r not in current],
            'meaning':'Latest account on the source clock; current clinical state is not established.'})
        if status!='documented_source_statement':steps.append({'relation':relation,'entity':entity,'reason':status,
            'action':'Retrieve referenced source' if issues else 'Verify statement timing' if undated else 'Reconcile competing source statements',
            'evidence_statement_ids':[r['id'] for r in current]})
    return {'patient_id':pid,'operator':'warehouse_interpretation','synthetic':True,'schema':VERSION,
        'statements':rows,'states':states,'evidence_acquisition_steps':steps,
        'relation_counts':dict(Counter(s['relation'] for s in rows)),'state_counts':dict(Counter(s['status'] for s in states)),
        'unmapped_record_categories':dict(Counter(r['source_table'] for r in records if r['source_table'] not in STATEMENTS)),
        'missingness_alternatives':['Not captured by contributing sources','Received later','Stored in another field/source','Interpretation not mapped','Event did not occur'],
        'clinical_state_established':False,'care_gap_established':False,'new_paid_api_calls':0}
