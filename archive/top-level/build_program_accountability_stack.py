"""Full original synthetic cohort: financial evidence -> program review proposals."""
import argparse,json,sqlite3
from pathlib import Path
from collections import Counter
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import accountability_examples,snapshot_examples
from clinical_core.agere.clinical import program_accountability as A,program_work as W,financial_accounts as F


def save(path,row):
    if path.exists():raise ValueError('Preserve existing verification artifact: '+str(path))
    path.write_text(json.dumps(row,indent=2,allow_nan=False)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(exist_ok=True)
    stack=Stack(stack_path(),allow_live=False);report=[]
    try:
        for i,ex in enumerate(snapshot_examples(accountability_examples(),stack.retriever.meta['snapshot']['as_of']),1):
            answer=stack.compose(ex['question']);save(a.output/f'question-{i}.json',answer)
            detail=next(r['result'] for r in answer['results'] if r['node']['operation']=='accountability_review')
            assert answer['new_paid_api_calls']==0
            proposal=A.load(stack.path,detail['id']);db=A.storage(stack.path);fdb=F.storage(stack.path)
            counts=Counter(r['kind'] for r in proposal['contract_requests']);clinical_authority=0;context_sources=0
            try:
                for ref in proposal['references']:
                    row=A.read(db,'receipts',ref['receipt_id']);assert digest(row)==ref['receipt_hash']
                    finance=json.loads(__import__('zlib').decompress(fdb.execute('select payload from receipts where id=?',(row['financial_receipt_id'],)).fetchone()[0]))
                    assert digest(finance)==row['financial_receipt_hash']
                    expected={account['account_id'] for account in finance['accounts'] if account['status']!='reconciled_received_records'}
                    actual={t['account_id'] for t in row['requests'] if t['kind']=='financial_account_review'}
                    assert actual==expected
                    assert all(t['patient_id']==ref['patient_id'] for t in row['requests'])
                    assert len([t for t in row['requests'] if t['kind']=='financial_account_binding_review'])==bool(finance['unassigned_sources'])
                    assert len([t for t in row['requests'] if t['kind']=='program_input_review'])==2
                    assert all(r['status']=='unbound' for r in row['requirements'])
                    assert not row['clinical_eligibility_established'] and not row['payment_entitlement_established']
                    counts.update(t['kind'] for t in row['requests']);context_sources+=len(row['source_context'])
                assert dict(counts)==detail['request_counts']
            finally:db.close();fdb.close()
            example=A.inspect(stack.path,detail['id'],patient=proposal['references'][0]['patient_id']);save(a.output/f'patient-example-{i}.json',example)
            summary={'title':ex['title'],'patients_evaluated':detail['patients_evaluated'],'request_counts':detail['request_counts'],
                'source_context_records':context_sources,'all_account_review_sets_match_financial_receipts':True,
                'immutable_receipts_checked':len(proposal['references']),'processing':detail['processing_counts']}
            if i==2:
                work=answer['results'][-1]['result'];db=W.database(stack.path)
                try:
                    saved=W.load(db,'work_proposals',work['proposal_id']);assessments=[W.load(db,'work_assessments',key) for key in saved['assessment_ids']]
                    assert len(assessments)==detail['patients_evaluated']==work['accountability_assessments']
                    assert all(row.get('decision_context',{}).get('protocols') and row.get('accountability_context') for row in assessments)
                    A.verify_contexts(stack.path,[row['accountability_context'] for row in assessments]);save(a.output/'program-assessments.json',assessments)
                    summary.update(program_assessments=len(assessments),candidate_counts=work['counts'])
                finally:db.close()
            else:assert detail['patients_evaluated']==1614
            report.append(summary);print(json.dumps(summary),flush=True)
            replay=stack.compose(ex['question']);assert replay['cache_hit'] and replay['new_paid_api_calls']==0;save(a.output/f'replay-{i}.json',replay)
        result={'passed':True,'synthetic':True,'source_snapshot':stack.retriever.meta['snapshot'],'questions':report,
            'new_paid_api_calls':0,'work_items_created':0,'requests_sent':0,'clinical_authority_established':False}
        save(a.output/'verification.json',result);print(json.dumps(result,indent=2))
    finally:stack.close()


if __name__=='__main__':main()
