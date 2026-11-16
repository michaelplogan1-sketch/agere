"""Local API and independent received-source audit for the two saved populations."""
import json,sqlite3,zlib,time,urllib.request,urllib.error
from pathlib import Path
from collections import Counter
from datetime import datetime
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path

OUT=Path('artifacts/agere-population-accountability-v1/http')
BASE='http://localhost:8885/api/stack/'
def save(name,row):
    p=OUT/(name+'.json');p.parent.mkdir(parents=True,exist_ok=True);assert not p.exists(),str(p);p.write_text(json.dumps(row,indent=2)+'\n');return row
def call(route,body=None):
    r=urllib.request.Request(BASE+route,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(r,timeout=900) as f:return json.load(f)
def ledger():
    with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:return dict(db.execute('SELECT id,status FROM calls'))
def audit(report):
    # Independently read received adapter rows, not runtime projection helpers or generator truth.
    path=stack_path();src=sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    fdb=sqlite3.connect((path/'financial-accounts.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    odb=sqlite3.connect((path/'outcomes.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    rules=json.loads(Path('clinical_core/agere/clinical/financial_account_maps.json').read_text())
    streams={rules['snapshot_stream'],*rules['context_streams'],*[t['stream'] for t in rules['transactions']]}
    totals={};status=Counter();checks=Counter();outcomes=Counter();cutoff=report['request']['outcome']['available_by'];seen=[];unassigned=0
    try:
        for m in report['members']:
            pid=m['patient_id'];seen.append(pid)
            f=json.loads(zlib.decompress(fdb.execute('SELECT payload FROM receipts WHERE id=?',(m['financial_receipt_id'],)).fetchone()[0]))
            o=json.loads(zlib.decompress(odb.execute('SELECT payload FROM receipts WHERE id=?',(m['outcome_receipt_id'],)).fetchone()[0]))
            assert digest(f)==m['financial_receipt_hash'] and digest(o)==m['outcome_receipt_hash']
            assert f['patient_id']==o['patient_id']==pid;outcomes[o['status']]+=1
            assert o['clinical_outcome_established'] is False and o['clinical_negative_established'] is False
            raw={r['record_id']:r for (s,) in src.execute('SELECT payload FROM records WHERE patient=?',(pid,)) if (r:=json.loads(s))}
            received={k for k,r in raw.items() if r['source_table'] in streams and (not r.get('available_date') or r['available_date']<=cutoff)}
            assigned={k for a in f['accounts'] for k in a['source_record_ids']};unbound={x['source']['record_id'] for x in f['unassigned_sources']}
            assert assigned.isdisjoint(unbound) and assigned|unbound==received
            checks['received_financial_rows']+=len(received);unassigned+=len(unbound)
            for a in f['accounts']:
                checks['accounts']+=1;status[a['status']]+=1
                for ref in a['selected_snapshots']:
                    s=raw[ref['record_id']];assert digest(s)==ref['record_hash'];assert s['account_id']==a['account_id']
                    peers=[r for r in raw.values() if r['source_table']=='patient_billing_accounts' and r.get('account_id')==a['account_id'] and r.get('available_date') and r['available_date']<=cutoff]
                    assert s.get('event_time')==max((r.get('event_time') or '' for r in peers),default='') or all(not r.get('event_time') for r in peers)
                    assert {k:s.get(k) for k in rules['snapshot_amounts']}==a['snapshot_amounts'];checks['selected_snapshot_refs']+=1
                for e in a['equations']:
                    x=a['snapshot_amounts'];v=sum(x[k]*c for k,c in e['coefficients'].items()) if all(type(x[k]) is int for k in e['coefficients']) else None
                    assert e['residual_cents']==v;checks['accounting_identities']+=1
                for c in a['cash_comparisons']:
                    rule=next(t for t in rules['transactions'] if t['stream']==c['stream'])
                    txids={k for k in a['source_record_ids'] if raw[k]['source_table']==c['stream']}
                    included={t['source']['record_id'] for t in c['included']};excluded={t['source']['record_id'] for t in c['excluded']};unknown={t['source']['record_id'] for t in c['unresolved']}
                    assert not (included&excluded or included&unknown or excluded&unknown) and included|excluded|unknown==txids
                    total=0
                    for t in c['included']:
                        r=raw[t['source']['record_id']];assert digest(r)==t['source']['record_hash'];assert type(r[rule['amount_field']]) is int
                        assert r.get('account_id')==a['account_id']
                        if rule['status_field']:assert r[rule['status_field']]==rule['accepted_status']
                        assert datetime.fromisoformat(r['event_time'])<=datetime.fromisoformat(raw[a['selected_snapshots'][0]['record_id']]['event_time'])
                        total+=r[rule['amount_field']];checks['cash_transaction_refs']+=1
                    assert c['received_transactions_cents']==total
                    assert c['difference_cents']==(c['snapshot_reported_cents']-total if type(c['snapshot_reported_cents']) is int else None)
                    assert c['capture_complete'] is False
                for link in a['links']+a['account_binding_evidence']:
                    for side in ('origin','target'):
                        r=raw[link[side]['record_id']];assert digest(r)==link[side]['record_hash'] and r['patient_id']==pid
                    assert raw[link['origin']['record_id']][link['field']]==link['target']['record_id'];checks['service_adjudication_account_links']+=1
                if a['currency'] and a['snapshot_amounts'] and all(type(v) is int for v in a['snapshot_amounts'].values()):
                    bucket=totals.setdefault(a['currency'],Counter());bucket.update(a['snapshot_amounts']);bucket['accounts']+=1
            assert m['work_outcome_link']==m['work_payment_link']=='not_established'
        assert sorted(seen)==sorted(report['selection']['patient_ids']) and len(set(seen))==len(seen)
        assert totals==report['financial']['selected_snapshot_totals_by_currency']
        assert dict(status)==report['financial']['account_status_counts'] and unassigned==report['financial']['unassigned_source_count']
        assert dict(outcomes)==report['outcomes']['status_counts']
        return {'status':'passed','patients':len(seen),'checks':dict(checks),'financial_status_counts':dict(status),'unassigned_sources':unassigned,'snapshot_totals':totals,'outcome_status_counts':dict(outcomes),'no_causal_or_payment_entitlement_claim':True}
    finally:src.close();fdb.close();odb.close()

def main():
    before=ledger();programs=[]
    for domain in ('respiratory','behavioral'):
        d=json.loads(Path('artifacts/agere-population-decision-v1/verified-v2/'+domain+'-decision.json').read_text())
        v=call('decision?id='+d['id']);assert v['dependencies_current'];tasks=[t['task']['id'] for t in v['tasks']]
        req={'decision_id':d['id'],'accountability_contract':'program_payment_evidence','scenario_id':'','outcome':{'contract':'discharge_admission_clinical_30d','index_from':'2029-01-01','index_through':d['decision_date'],'available_by':d['decision_date'],'filter':'all'}}
        started=time.monotonic();r=save(domain+'/report',call('decision/population-accountability',req))
        print(json.dumps({'program':domain,'report_saved':r['result_id'],'seconds':round(time.monotonic()-started,3)}),flush=True)
        assert r['question']==d['question'] and len(r['members'])==1614 and len(r['work'])==4
        assert sorted(t['task']['id'] for t in r['work'])==sorted(tasks)
        assert call('decision/population-accountability',req)==r
        assert r['attributable_program_payment_cents'] is None and r['causal_savings_cents'] is None and not r['observed_amounts_netted']
        a=save(domain+'/independent-audit',audit(r));print(json.dumps({'program':domain,'audit':a}),flush=True)
        sample=next(m for m in r['members'] if m['task_ids'])
        opened=save(domain+'/member',call('decision/population-accountability-open',{'decision_id':d['id'],'result_id':r['result_id'],'patient_id':sample['patient_id']}))
        assert opened['current'] and opened['member_evidence']['accountability']['patient_id']==sample['patient_id']
        after=call('decision?id='+d['id']);assert v['tasks']==after['tasks'];assert any(f['result_id']==r['result_id'] for f in after['followups'])
        save(domain+'/reopen',{'dependencies_current':after['dependencies_current'],'same_tasks':True,'saved_result_id':r['result_id'],'original_question':after['decision']['question']})
        programs.append({'domain':domain,'decision_id':d['id'],'result_id':r['result_id'],'members':1614,'owned_work':4,'accounts':r['financial']['accounts_evaluated'],'unknown_contract_and_attribution':True})
    assert ledger()==before
    save('verification',{'status':'passed','programs':programs,'provider_calls_added':0,'tasks_changed':0,'external_actions':0,'clinical_validation':False})

if __name__=='__main__':main()
