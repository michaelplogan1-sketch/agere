"""Full original-cohort queries checked against public received source accounts.

No generator truth, private labels, paid calls or model-generated expected answers.
"""
import argparse,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import evidence_requirements as er,analysis_graph,graph_planner
from clinical_core.agere.clinical.graph_workbench import requirement_examples
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.warehouse_activation import code_identity as qualification_code
from clinical_core.agere.clinical.context_student import REQUEST_MAX_BYTES  # shared provider request bound

# Independent source comparison, deliberately not calling the runtime projector,
# account interpreter or its field/clock helpers to manufacture expected results.
CASES={
 'native_affordability_report':('financial_screens','difficulty_paying_medical_bills',True,
     ['difficulty_paying_medical_bills','income_exact']),
 'native_medication_nonuse':('medication_followup_reports','reported_intent','not_using',
     ['medication_name','reported_intent','days_used_reported','report_window_days','remaining_quantity_reported']),
 'native_outside_service':('outside_report_status','performed_service_documented',True,
     ['status','performed_service_documented']),
 'native_advance_document_review':('advance_care_documents','legal_validity','not_determined',
     ['document_type','extracted_treatment_fields','signature_observations','legal_validity','copy_quality']),
}


def write(path,value):path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def expected(src,ids,asof,case):
    stream,field,target,comparison=case;groups=defaultdict(list);values=defaultdict(list)
    for rid,pid,day,raw in src.execute('SELECT record_id,patient_id,event_date,facts FROM source_records WHERE source_table=? AND available_date<=?',(stream,asof)):
        f=json.loads(raw)
        entity=(f.get('pharmacy_prescription_id') or f.get('prescription_id') or f.get('account_id') or f.get('document_id')
                or f.get('medication_order_record_id') or f.get('referral_record_id') or str(f.get('medication_name',f.get('name',rid))).casefold())
        groups[pid,entity].append((day,f))
    for (pid,_),rows in groups.items():
        if any(day is None for day,_ in rows):values[pid].append(None);continue
        date=max(day for day,_ in rows);current=[f for day,f in rows if day==date]
        signatures={json.dumps({k:f[k] for k in comparison if k in f},sort_keys=True) for f in current}
        linked=True
        for f in current:
            for link,table in [('referral_record_id','referrals'),('imported_record_id','imported_documents')]:
                if f.get(link):
                    linked &= src.execute('SELECT count(*) FROM source_records WHERE record_id=? AND patient_id=? AND source_table=? AND available_date<=?',(f[link],pid,table,asof)).fetchone()[0]==1
        value=current[0].get(field)
        values[pid].append((value==target) if len(signatures)==1 and linked and type(value) is type(target) else None)
    return {pid:'supported' if any(v is True for v in values[pid]) else
            'unresolved' if not values[pid] or any(v is None for v in values[pid]) else 'contradicted' for pid in ids}


def main():
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    path=args.build.resolve()/'stack';out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    meta=json.loads((path/'run.json').read_text());source=Path(meta['snapshot']['source_dataset']['source'])
    src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True);db=sqlite3.connect((path/'stack.sqlite3').as_uri()+'?mode=ro',uri=True)
    ids=sorted(r[0] for r in src.execute('SELECT patient_id FROM patients'))
    assert ids==sorted(r[0] for r in db.execute('SELECT id FROM patients'))
    db.close();stack=Stack(path,allow_live=False);results=[]
    try:
        for example in requirement_examples():
            key=example['graph']['nodes'][1]['parameters']['contract']
            if key not in CASES:continue
            result=stack.compose(example['question']);write(out/(key+('-work' if len(example['graph']['nodes'])>2 else '')+'.json'),result)
            assert result['new_paid_api_calls']==0
            if len(example['graph']['nodes'])>2:
                work=result['results'][-1]['result'];write(out/'work-result.json',work)
                targets={pid for pid,status in expected(src,ids,meta['snapshot']['as_of'],CASES[key]).items() if status=='supported'}
                assert work['counts']['selected']==len(targets) and work['counts']['assessed']==len(targets)
                from clinical_core.agere.clinical import program_work
                workdb=program_work.database(path)
                try:
                    proposal=program_work.load(workdb,'work_proposals',work['proposal_id'])
                    assessments=[program_work.load(workdb,'work_assessments',aid) for aid in proposal['assessment_ids']]
                    assert {r['patient_id'] for r in assessments}==targets
                    assert all(r['decision_context']['requirements'][0]['contract_id']==key and
                               r['decision_context']['requirements'][0]['status']=='supported' and
                               not r['clinical_eligibility_established'] for r in assessments)
                finally:workdb.close()
                results.append({'contract':key,'work_proposal':work});continue
            detail=result['results'][-1]['result'];truth=expected(src,ids,meta['snapshot']['as_of'],CASES[key])
            assert detail['patients_evaluated']==len(ids)
            assert detail['status_counts']==dict(Counter(truth.values())),(key,detail['status_counts'],dict(Counter(truth.values())))
            selected,unknown,repeat=er.execute(path,ids,{'contract':key,'filter':'supported'},preview_limit=20)
            assert selected=={pid for pid,status in truth.items() if status=='supported'}
            assert unknown=={pid for pid,status in truth.items() if status=='unresolved'}
            assert repeat['processing_counts']=={'patient_cache_hits':len(ids)}
            example_row=next((r for r in repeat['patients'] if r['status']=='supported'),None)
            if example_row:write(out/(key+'-patient.json'),er.inspect(path,example_row['id']))
            results.append({'contract':key,'result_id':result['result_id'],'patient_status_counts':detail['status_counts'],
                'public_source_patient_sets_match':True,'processing_counts':detail['processing_counts'],
                'repeat_processing_counts':repeat['processing_counts'],'new_paid_api_calls':0})
            print(json.dumps(results[-1]),flush=True)
        from verify_scoped_planner import exercise
        wire=exercise(path);sizes=[r['request_bytes'] for r in wire['calls']]
        write(out/'planner-wire.json',wire)
        assert max(sizes)<=REQUEST_MAX_BYTES,sizes
        receipt={'synthetic':True,'source_snapshot':meta['snapshot'],'patients':len(ids),'results':results,
                 'qualification_code_identity':qualification_code(),
                 'frontier_request_bytes_1500_ascii_question':max(sizes),'new_paid_api_calls':0,
                 'clinical_accuracy_measured':False,'new_model_weights':False,'code_identity':er.code_identity()}
        write(out/'verification.json',receipt)
        print(json.dumps({'verified':True,'frontier_request_bytes':max(sizes),'patients':len(ids)}),flush=True)
    finally:src.close();stack.close()


if __name__=='__main__':main()
