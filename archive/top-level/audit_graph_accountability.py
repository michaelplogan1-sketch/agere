"""Exhaustive analytical receipt bindings plus raw-source financial arithmetic."""
import json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.clinical import graph_reports as R
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-graph-accountability-v1';STACK=ROOT/'artifacts/agere-healthcare-v123/stack'

def main():
 graph=json.loads((OUT/'final-question.json').read_text())['response'];audits=[];bindings=0;source_checks=0;snapshots=0;transactions=0
 with sqlite3.connect(STACK/'stack.sqlite3') as src:
  roster=sorted(p[0] for p in src.execute('SELECT id FROM patients'));known={}
  def record(rid,pid,h=None):
   nonlocal source_checks
   if rid not in known:
    p,raw=src.execute('SELECT patient,payload FROM records WHERE id=?',(rid,)).fetchone();known[rid]=(p,json.loads(raw))
   p,r=known[rid];assert p==pid
   if h:assert digest(r)==h;source_checks+=1
   return r
  for item in graph['results']:
   n,result=item['node'],item['result'];op=n['operation']
   if op not in R.OPERATIONS:continue
   assert sorted(m['patient_id'] for m in result['review_members'])==roster
   db=R.MODULES[op].storage(STACK);counts=Counter();totals=defaultdict(Counter)
   try:
    for ref in result['review_members']:
     row=R.read(db,op,ref['receipt_id']);pid=ref['patient_id'];assert row['patient_id']==pid and digest(row)==ref['receipt_digest'];bindings+=1
     status,value=R.status_value(op,row);assert status==ref['status'] and value==ref['value'];counts[status]+=1
     if op=='measure_review':assert set(row['components'])=={'denominator','numerator','exclusion','attribution'}
     if op=='outcome':
      assert row['parameters']=={k:v for k,v in n['parameters'].items() if k!='filter'}
      for rid,h in R.source_refs(op,row,{}).items():record(rid,pid,h)
     if op=='financial_accounts':
      for account in row['accounts']:
       amounts=account['snapshot_amounts']
       if amounts:
        rawrows=[record(x['record_id'],pid,x['record_hash']) for x in account['selected_snapshots']]
        assert all({k:r.get(k) for k in amounts}==amounts for r in rawrows);snapshots+=len(rawrows)
        for eq in account['equations']:
         if eq['status'] in ('balanced','difference'):assert sum(amounts[k]*v for k,v in eq['coefficients'].items())==eq['residual_cents']
        if account['currency'] and all(type(v) is int for v in amounts.values()):totals[account['currency']].update(amounts);totals[account['currency']]['accounts']+=1
       for comparison in account['cash_comparisons']:
        total=0
        for entry in comparison['included']:
         raw=record(entry['source']['record_id'],pid,entry['source']['record_hash']);assert raw[entry['field']]==entry['amount_cents'];total+=raw[entry['field']];transactions+=1
        assert total==comparison['received_transactions_cents']
        if comparison['difference_cents'] is not None:assert comparison['snapshot_reported_cents']-total==comparison['difference_cents']
      for rid,h in R.source_refs(op,row,{}).items():record(rid,pid,h)
    assert dict(counts)==result['member_status_counts']
    if op=='financial_accounts':assert {k:dict(v) for k,v in totals.items()}==result['selected_snapshot_totals_by_currency']
    audits.append({'operation':op,'patients':len(roster),'statuses':dict(counts)})
   finally:db.close()
  member=json.loads((OUT/'final-member.json').read_text())['response']
  for rid,h in member['source_versions']['records'].items():record(rid,member['patient_id'],h)
 report={'status':'passed','analytical_member_receipt_bindings':bindings,'patient_roster':len(roster),'source_hash_checks':source_checks,'unique_raw_records_checked':len(known),'selected_financial_snapshots_checked':snapshots,'included_cash_transactions_checked':transactions,'nodes':audits,'baseline':'Raw received snapshot fields, integer-cent equations, actual included payment/refund amounts and exact source hashes; does not establish completeness, entitlement, attribution, or causal effects.'}
 (OUT/'binding-audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
if __name__=='__main__':main()
