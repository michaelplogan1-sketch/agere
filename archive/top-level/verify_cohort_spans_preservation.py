"""Final current-source coverage and preservation receipt for the new acquisition."""
import json
from pathlib import Path
from collections import Counter
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import cohort_span_acquisition as a, source_synthesis, question_span_availability as question
from clinical_core.agere.gateway import BudgetLedger


def main():
    stack=stack_path();key='8021f42a502aab70d962ecc3fce0fe3b4c1d706800e69e9135958bd071d5d02b';out=Path('artifacts/agere-cohort-spans-v1')
    s=a.Session(stack,key);status=a.status(stack,key);assert status['counts']=={'proposed':117,'excluded':7,'abstained':1}
    rows=[s.inspect(item) for item in s.selected];assert len(rows)==118
    proposals=[p for r in rows for p in r['proposals']];assert len(proposals)==230
    for r in rows:
        for p in r['proposals']:
            record,text,_=a.cohorts.reader.source(stack,p['target'])
            assert a.digest(record)==r['binding']['source_hash'] and text[p['target']['start']:p['target']['end']]==p['quote']
    question_result=json.loads((out/'question-availability-proposal.json').read_text());question.inspect(stack,question_result['id'])
    current=[]
    for answer in ['975024996abefa0c26ade1337cdb55810996df6397704e765c3efe4ed8655658','0227207af37a3865659acbd25461b5a1eb5d6eee00ce23f4a17d259f3cbac08b','d7d96be6f304b5649e24c329e38648d1ef6475c579c1645498dcd7fd177c8799','3c26f4b1afff1ff8048117a7c55bed945dcd40306ec1324c1240af2dd3cc8c36','fc6d15d4fd41a67aafd6615ba5c5c34c53688afc6f7373f3e69357abb3c59815']:
        source_synthesis.inspect(stack,answer);current.append(answer)
    usage=[r['usage'] for r in rows]+[question_result['usage']]
    report={'cohort_id':key,'plan_id':s.plan['id'],'inventory_fields':125,'inventory_categories':76,
        'completed_fields':118,'completed_categories':len({r['source']['source_table'] for r in rows}),
        'counts':status['counts'],'proposals':230,'proposal_categories':len({r['source']['source_table'] for r in rows if r['proposals']}),
        'proposed_concepts':len({p['concept'] for p in proposals}),'partition_counts':status['partition_counts'],
        'proposals_by_partition':dict(Counter(r['partition'] for r in rows for p in r['proposals'])),
        'fields_by_category':dict(Counter(r['source']['source_table'] for r in rows)),
        'selected_tasks':status['selected_tasks'],'typed_reference_counts':sum(len(r['source_availability']['typed_references']) for r in rows),
        'unresolved_typed_reference_counts':sum(len(r['source_availability']['unresolved_typed_references']) for r in rows),
        'request_bytes_min':min(r['request_bytes'] for r in rows),'request_bytes_max':max(r['request_bytes'] for r in rows),
        'new_paid_api_calls':sum(r['new_paid_api_calls'] for r in rows)+question_result['new_paid_api_calls'],
        'new_token_count_calls':sum(r['new_token_count_calls'] for r in rows)+question_result['new_token_count_calls'],
        'input_tokens':sum(u['usage']['input_tokens'] for u in usage),'output_tokens':sum(u['usage']['output_tokens'] for u in usage),
        'estimated_usd':sum(u['cost_estimate']['estimated_usd'] for u in usage),'invoice_verified':False,
        'frozen_answers_current':current,'human_reviews_created':0,'training_labels_created':0,'new_fitted_models':0,'clinical_actions':0,
        'clinical_quality_validated':False,'question_availability_receipt':question_result['id']}
    a.cohorts.queue.write(out/'final-coverage.json',report)
    a.cohorts.queue.write(out/'final-status.json',status)
    ledger=BudgetLedger(a.cohorts.reader.ROOT/'artifacts/agere-full/provider-budget.sqlite3')
    try:a.cohorts.queue.write(out/'ledger-current.json',ledger.receipt())
    finally:ledger.close()
    print(json.dumps({k:v for k,v in report.items() if k not in ('fields_by_category','frozen_answers_current')},indent=2))


if __name__=='__main__':main()
