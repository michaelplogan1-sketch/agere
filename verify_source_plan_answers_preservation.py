"""Independent source, citation, request and retained-artifact verification."""
import hashlib
import json
import sqlite3
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.economics import usage_cost
from clinical_core.agere.narrative_sources import text_fields
from clinical_core.agere.clinical import program_work, case_passes as P, source_synthesis as S
from clinical_core.agere.clinical import record_context as C, meaning_source_stage as M
from clinical_core.agere.clinical import reference_table_context as F, reference_reconciliation_transport as T
from clinical_core.agere.clinical.structured_transport import install as install_structured
from clinical_core.agere.clinical.meaning_source_dispatch import install as install_meaning, pinned
from clinical_core.agere.clinical.warehouse_retrieval import Retriever
from clinical_core.agere.clinical import source_plan_answers as A, source_plan_reconciliation as R
from clinical_core.agere.clinical import source_plan_recovery as Q
from clinical_core.agere.gateway import BudgetLedger

OUT=Path('artifacts/agere-source-plan-answers-v1');PRIOR=Path('artifacts/agere-reader-comparison-v1')


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    install_structured();F.install();T.install();install_meaning(stack_path())
    protected=json.loads((PRIOR/'protected-for-continuation.json').read_text())['protected_files']
    for p,h in protected.items():assert sha(p)==h,p
    prior_count=len(protected)
    for n in ('source_plan_answers.py','source_plan_reconciliation.py','reference_reconciliation_transport.py','source_plan_recovery.py'):
        p='clinical_core/agere/clinical/'+n;protected[p]=sha(p)
    histories=json.loads((PRIOR/'history-baseline.json').read_text());db=program_work.database(stack_path())
    for t,old in histories.items():assert [list(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY 1,2')]==old
    db.close()
    semantic=json.loads((OUT/'semantic-baseline.json').read_text());db=sqlite3.connect(stack_path()/'semantic-readings.sqlite3')
    for t,old in semantic.items():assert [list(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY 1,2')]==old
    db.close()
    oldledger=json.loads((PRIOR/'ledger-row-baseline.json').read_text());db=sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3')
    for old in oldledger:assert list(db.execute('SELECT * FROM calls WHERE id=?',(old[0],)).fetchone())==old
    ledger=[list(r) for r in db.execute('SELECT * FROM calls ORDER BY id')];db.row_factory=sqlite3.Row
    oldids={r[0] for r in oldledger};new=[dict(r) for r in db.execute('SELECT * FROM calls') if r['id'] not in oldids];db.close()
    costs=[];failed=[]
    for call in new:
        if call['status']=='failed':
            assert call['error']=='Ambiguous claim-review unit reference'
            failed.append(call['id'])
            raw=json.loads((stack_path()/'source-plan-reconciliations'/'provider'/(call['id']+'.json')).read_text())['response']
            costs.append(usage_cost(raw['model'],raw.get('usage'))['estimated_usd']);continue
        assert call['status']=='validated',call['id']
        raw=json.loads(call['response']);costs.append(usage_cost(raw['model'],raw.get('usage'))['estimated_usd'])
    docs=[r for r in json.loads(Path('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json').read_text())['files'] if r['path'].startswith('/Users/mikel/Documents/')]
    for r in docs:assert sha(r['path'])==r['sha256']
    assert len(pinned(stack_path()))==33
    db=sqlite3.connect((stack_path()/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    plan=json.loads(Path('artifacts/agere-retrieval-source-plan-v1/payer-source-plan.json').read_text());sources={}
    for s in plan['plan']['preview']['records']:
        patient,raw=db.execute('SELECT patient,payload FROM records WHERE id=?',(s['record_id'],)).fetchone();record=json.loads(raw)
        assert patient=='P-1006' and digest(record)==s['record_hash'];sources[s['record_id']]=record
    assert db.execute('SELECT COUNT(*) FROM patients').fetchone()[0]==1614
    assert db.execute('SELECT COUNT(*) FROM records').fetchone()[0]==1238686;db.close()
    audits=[];submitted=[];narrative_count=0;source_request_keys=[]
    for context in plan['contexts']:
        assert C.verified(stack_path(),context['id'])==context
        row,bundle,links,packets,key=M.prepare(stack_path(),context['id']);saved=M.inspect(stack_path(),key)
        by_unit={u['id']:u for u in row['model_context']['evidence']}
        for stage,(body,units) in zip(saved['passes'],packets):
            source_request_keys.append(digest({'contract':'context-student/1','provider':'claude','body':body}))
            assert source_request_keys[-1] in {c['id'] for c in new}
            assert P.request_bytes(body)==stage['request_bytes']<=20000
            for review in stage['result']['unit_reviews']:submitted.append(review['source_unit_id'])
        for unit in by_unit.values():
            if unit['kind']!='narrative':continue
            original=next(v['text'] for v in text_fields(sources[unit['source_ref']]) if v['field']==unit['field'])
            assert original[unit['start']:unit['end']]==unit['text'];narrative_count+=1
        audits.append({'context_id':context['id'],'units':len(by_unit),'source_passes':len(saved['passes']),'request_bytes':[P.request_bytes(b) for b,u in packets]})
    assert len(submitted)==len(set(submitted))==53
    retriever=Retriever(stack_path())
    try:
        http=json.loads((OUT/'http-verification.json').read_text());result=A.inspect(retriever,{'id':http['snapshot_id']})
        prepared,refs,job,claims,key=R.prepare(retriever,{'snapshot_id':http['snapshot_id']})
        ledger_adapter=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3'))
        try:answer=Q.inspect(retriever,{'id':http['reconciliation_id']},ledger_adapter) if http['local_recovery'] else R.inspect(retriever,{'id':http['reconciliation_id']})
        finally:ledger_adapter.close()
        assert key==answer.get('failed_reconciliation_id',answer['id']) and len(claims)==answer['coverage']['accounted_claims']
        originals={a['id']:{f['id']:f for p in a['passes'] for f in p['result']['findings']} for a in result['answers']}
        for reference,claim in zip(claims,job['original_findings']):
            original=originals[reference['answer_id']][reference['original_id']]
            assert {k:v for k,v in claim.items() if k!='id'}=={k:v for k,v in original.items() if k!='id'}
        citation_count=0
        for finding in answer['result']['findings']:
            for citation in finding['citations']:
                item=job['units'][citation['unit']];unit=item['unit'];q=citation['quote'];start=item['text'].index(q)
                assert item['text'].count(q)==1 and citation['source_unit_id']==unit['id']
                assert citation['source']['record_id']==unit['source_ref']
                assert citation['start']==start+(unit['start'] if unit['kind']=='narrative' else 0)
                citation_count+=1
        assert len({r['input_id'] for r in answer['result']['claim_reviews']})==len(claims)
    finally:retriever.close()
    summary={'status':'passed','prior_protected_files':prior_count,'protected_files':len(protected),
        'prior_ledger_rows_unchanged':len(oldledger),'new_validated_calls':len(new)-len(failed),'new_failed_calls':len(failed),
        'failed_request_hashes':failed,'system_unassessed_claim_reviews':answer['coverage'].get('system_unassessed_claim_reviews',0),
        'new_estimated_api_usd':sum(costs),'new_source_calls':len(source_request_keys),'history_counts':{k:len(v) for k,v in histories.items()},
        'semantic_readings_unchanged':len(semantic['readings']),'human_reviews_created':0,'documents_unchanged':len(docs),'pinned_source_routes':33,
        'verified_source_records':24,'verified_source_units':53,'exact_narrative_units':narrative_count,'verified_final_citations':citation_count,
        'source_request_audits':audits,'clinical_validated':False,'source_migration':False}
    for name,value in [('protected-for-continuation',{'schema':'agere-protected-continuation/1','protected_files':protected}),('history-baseline',histories),('ledger-row-baseline',ledger),('preservation',summary)]:
        (OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='source_request_audits'}))


if __name__=='__main__':main()
