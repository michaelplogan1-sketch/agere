"""Reproducible machine-authored documentary audit, never a runtime classifier."""
from collections import Counter
import json
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import record_context as C, question_context as Q
from clinical_core.agere.clinical.native_snapshot_cache import install

OUT=Path('artifacts/agere-source-selection-v1/decision')


def judgment(case,r):
    f={k:r.get(k) for k in r['_warehouse_fields']}; cat=r['source_table']; text=f.get('text','')
    label,reason='uncertain','No narrow documentary correspondence established; this does not mean clinically irrelevant.'
    if case=='medication':
        if cat in ('medication_orders','pharmacy_dispenses','reported_medication_entries','medication_history_interviews','medication_review_decisions','pharmacy_information_responses','received_pharmacy_information'):
            label,reason='direct','Received medication order, dispensing/use account or explicit unresolved/negative report.'
        elif cat=='notes' and ('reports use on' in text or 'medication_history_review'==f.get('note_type')):
            label,reason='direct','Narrative explicitly discusses reported medication use or unresolved medication history.'
        elif cat in ('pharmacy_claim_transactions','pharmacy_history','prescription_reassessment_decisions','medication_lists','medication_clarification_contacts','medication_information_requests'):
            label,reason='supporting','Documentary medication history, transaction or clarification context; not proof of ingestion.'
    elif case=='respiratory':
        if cat=='condition_assessments' and f.get('condition')=='lipid_medication_tolerability':
            label,reason='off_question','Statin muscle/dark-urine tolerability fields contain no requested respiratory/oxygen/rehabilitation account.'
        elif f.get('note_type')=='lipid_result_treatment_decision':
            label,reason='off_question','LDL, statin supply and muscle warnings; no requested respiratory account.'
        elif 'No chest pain, resting shortness of breath' in text:
            label,reason='direct','Explicit dated negative resting breathlessness report, not proof of no pulmonary condition.'
        else: reason='Breathing-related safety advice is present, but it is not a current symptom, oxygen-use or rehabilitation observation.'
    elif case=='cardiometabolic':
        if (any(k.startswith('blood_pressure_') for k in f) or f.get('measurement') in ('bp_systolic','bp_diastolic') or f.get('name',f.get('test')) in ('Glucose','HbA1c') or 'Pressure today' in text):
            label,reason='direct','Explicit blood-pressure or glycemic measurement and, where supplied, dated narrative interpretation.'
        elif cat=='injury_followup_reports' and f.get('anatomic_site')=='ankle':
            label,reason='off_question','Ankle pain/support follow-up contains no requested pressure, glycemic or diabetes account.'
        else: reason='Other laboratory, vital or medication context may matter, but its relation to this question needs interpretation; not labeled irrelevant from category alone.'
    elif case=='functional':
        if cat in ('functional_assessments','functional_mobility_exams','care_support_interviews','care_management_contacts') or 'day-to-day function' in text or f.get('physical_activity_reported'):
            label,reason='direct','Explicit function, mobility, activity, support or pending needs assessment account.'
        elif cat=='encounters' and ('function' in f.get('chief_complaint','')):
            label,reason='supporting','Encounter purpose supplies timing/context for the function review, not proof of a functional finding.'
    elif case=='appointment':
        if 'appointment_id' in f or cat=='telephone_messages':
            label,reason='direct','Booking/status or explicit appointment offer/request communication.'
        elif cat=='medical_payment_plans' and f.get('status')=='partial_balance_followup':
            label,reason='off_question','Account-balance review date is not an appointment booking or attendance report.'
        elif cat=='encounters' and f.get('encounter_id')=='WHL-E-8d036cf4e15b72a20be036':
            label,reason='supporting','Exact encounter identifier matches the fulfilled booking; retains in-progress/completed reports.'
        elif cat=='notes' and r.get('encounter_id')=='WHL-E-8d036cf4e15b72a20be036':
            label,reason='supporting','Same encounter narrative supplies documentary follow-through context.'
        else: reason='Sports-medicine encounter mentions another scheduled procedure; no exact link to these bookings is established.'
    elif case=='payer':
        if cat=='coverage' and 'effective_start' in f or cat=='coverage_verifications' and 'service_reference_date' in f:
            label,reason='direct','Dated enrollment or returned eligibility/benefit account; no independent current-entitlement verification.'
        else:
            label,reason='off_question','Inspected clinical visit/medication narrative contains no enrollment or eligibility account or plan dates.'
    return label,reason,f


def main():
    path=stack_path();install(path);report=[]
    protocol=json.loads((OUT/'protocol.json').read_text())
    for case in protocol['cases']:
        cid=case['id'];data=json.loads((OUT/(cid+'-audit-input.json')).read_text())
        records,meta=C.received(path,case['patient']);assert meta['snapshot']==protocol['snapshot']
        by_id={r['record_id']:r for r in records};rows=[]
        for row in data['rows']:
            r=row['record'];assert digest(by_id[r['record_id']])==row['record_hash']
            label,reason,fields=judgment(cid,r)
            rows.append({'record_id':r['record_id'],'record_hash':row['record_hash'],'category':r['source_table'],
                'baseline':row['baseline'],'label':label,'reason':reason,'received_fields':fields})
        selected={r['record_id'] for r in rows};closure=Q.select_sources(records,selected,meta['snapshot']['as_of'])
        missing=set(closure['connected_record_ids'])-selected
        result={'case':cid,'label_authority':'assistant_authored_documentary_audit_not_clinical_validation',
            'units':'record counts, not independent clinical facts; copies are not deduplicated',
            'counts':dict(Counter(r['label'] for r in rows)),
            'baseline_counts':dict(Counter(r['label'] for r in rows if r['baseline'])),
            'additional_counts':dict(Counter(r['label'] for r in rows if not r['baseline'])),
            'additional_reference_closure':{'missing_received_record_ids':sorted(missing),
                'missing_categories':dict(Counter(by_id[k]['source_table'] for k in missing)),
                'boundary_edges':[e for e in closure['reference_edges'] if (e['from'] in selected)!=(e['to'] in selected)],
                'meaning':'Same existing typed-reference traversal applied to every candidate root, not only baseline roots. Missing from scope is not absent from warehouse.'},
            'rows':rows,'clinical_validated':False}
        (OUT/(cid+'-audit.json')).write_text(json.dumps(result,indent=2)+'\n')
        report.append({k:v for k,v in result.items() if k!='rows'})
        print(cid,result['counts'],'added',result['additional_counts'],'new closure absent',len(missing))
    (OUT/'audit-summary.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
