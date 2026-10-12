"""Independent raw-record cent, snapshot and link audit; no private truth used."""
import argparse,json,sqlite3,zlib
from collections import Counter
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import financial_accounts as F


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();stack=stack_path()
    answer=json.loads((a.output/'question.json').read_text());result=answer['results'][-1]['result'];prep=F.prepare(stack,result['parameters'])
    assert result['identity']==prep['identity'];db=sqlite3.connect(stack/'stack.sqlite3');receipts=F.storage(stack)
    counters=Counter();statuses=Counter();totals={};patients=list(db.execute('SELECT id FROM patients ORDER BY id'))
    try:
        for (pid,) in patients:
            row=json.loads(zlib.decompress(receipts.execute('SELECT payload FROM receipts WHERE id=?',(F.receipt_id(prep,pid),)).fetchone()[0]))
            source={r['record_id']:r for (raw,) in db.execute('SELECT payload FROM records WHERE patient=?',(pid,)) if (r:=json.loads(raw))}
            for acct in row['accounts']:
                statuses[acct['status']]+=1;counters['accounts']+=1
                for link in acct['links']+acct['account_binding_evidence']:
                    for side in ('origin','target'):
                        ref=link[side];raw=source[ref['record_id']]
                        assert ref['record_hash']==digest(raw) and raw['patient_id']==pid and raw['available_date']<=result['parameters']['available_by']
                    assert source[link['origin']['record_id']][link['field']]==link['target']['record_id'];counters['explicit_links']+=1
                for ref in acct['selected_snapshots']:
                    raw=source[ref['record_id']];assert digest(raw)==ref['record_hash'] and raw['patient_id']==pid
                    # Direct raw timestamp check, independent of the selection helper.
                    peers=[r for r in source.values() if r['source_table']=='patient_billing_accounts' and r.get('account_id')==acct['account_id'] and r.get('available_date') and r['available_date']<=result['parameters']['available_by']]
                    assert raw.get('event_time')==max((r.get('event_time') or '' for r in peers),default='') or all(not r.get('event_time') for r in peers)
                    counters['current_snapshots_verified']+=1
                for calc in acct['equations']:
                    values=acct['snapshot_amounts'];coeff=calc['coefficients']
                    expected=sum(values[k]*v for k,v in coeff.items()) if all(type(values[k]) is int for k in coeff) else None
                    assert calc['residual_cents']==expected;counters['integer_identities']+=1
                for comp in acct['cash_comparisons']:
                    total=0
                    for tx in comp['included']:
                        raw=source[tx['source']['record_id']]
                        assert raw['patient_id']==pid and raw['account_id']==acct['account_id']
                        assert raw['available_date']<=result['parameters']['available_by']
                        assert digest(raw)==tx['source']['record_hash'] and type(raw[tx['field']]) is int
                        assert raw['event_time']<=source[acct['selected_snapshots'][0]['record_id']]['event_time']
                        total+=raw[tx['field']];counters['cash_source_refs']+=1
                    assert total==comp['received_transactions_cents']
                    assert comp['difference_cents']==(comp['snapshot_reported_cents']-total if type(comp['snapshot_reported_cents']) is int else None)
                    assert comp['capture_complete'] is False
                if acct['currency'] and acct['snapshot_amounts'] and all(type(v) is int for v in acct['snapshot_amounts'].values()):
                    target=totals.setdefault(acct['currency'],Counter());target.update(acct['snapshot_amounts']);target['accounts']+=1
        assert len(patients)==result['patients_evaluated'];assert dict(statuses)==result['account_status_counts']
        assert totals==result['selected_snapshot_totals_by_currency'];assert counters['accounts']==result['accounts_evaluated']
        report={'synthetic':True,'passed':True,'patients_evaluated':len(patients),'checks':dict(counters),
            'account_status_counts':dict(statuses),'snapshot_totals_match':True,'amounts_are_received_reports':True,
            'clinical_validity_or_contract_settlement_established':False,'new_paid_api_calls':0}
        (a.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    finally:db.close();receipts.close()


if __name__=='__main__':main()
