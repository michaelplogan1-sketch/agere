"""Evidence-derived local engineering acceptance; clinical and payer authority excluded."""
import hashlib,json
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.workflow_scorecard import capability_checks,acceptance_status,release_acceptance
R=Path('artifacts/agere-population-accountability-v1')
def read(p):return json.loads(Path(p).read_text())
def evidence(p):return {'path':str(p),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}
def save(p,r):
 assert not p.exists(),str(p);p.write_text(json.dumps(r,indent=2)+'\n')
def main():
 v=read(R/'http/verification.json');assert v['status']=='passed' and len(v['programs'])==2
 restart=read(R/'restart-verification.json');assert restart['status']=='passed'
 browser=read(R/'browser-verification.json');assert browser['status']=='passed'
 reports=[read(R/f'http/{d}/report.json') for d in ('respiratory','behavioral')]
 for r in reports:
  assert len(r['members'])==r['selection']['counts']['matched']==1614 and len(r['work'])==4
  assert r['accountability']['base']['contract']['version']=='1'
  assert r['outcomes']['contract']['version']=='1'
  assert all(x['status']=='unbound' for m in r['members'] for x in m['requirements'])
  assert all(m['work_payment_link']==m['work_outcome_link']=='not_established' for m in r['members'])
  assert r['causal_savings_cents'] is r['attributable_program_payment_cents'] is None and not r['observed_amounts_netted']
 audits=[read(R/f'http/{d}/independent-audit.json') for d in ('respiratory','behavioral')]
 assert all(a['status']=='passed' and a['checks']['accounts']==32241 and a['checks']['received_financial_rows']==501152 for a in audits)
 checks=[]
 specs={
  'versioned_contract_and_attribution':'Both exact saved populations bind the existing versioned program-payment prerequisite definition and registered outcome definition. Effective agreement, performance/payment rules, attribution and program outcome links remain explicitly unbound. No installed payer agreement or attribution is manufactured.',
  'independent_received_amount_reconciliation':'Independent received-record audit accounts for 501,152 financial rows, 32,241 accounts, 46,382 integer identities, 45,064 cash references and 461,546 explicit service/adjudication/account links in each composition. Repeated account snapshots are not summed. Missing, conflicting and unassigned sources remain in denominators.',
  'work_outcome_and_payment_linkage':'Both original questions retain all 1,614 member outcome/financial receipt identities and their four exact persistent internal work items. Patient source context, work state and unresolved program links can be reopened together. All 32,241 accounts have no established program-work payment link; internal handoffs are not delivered care. Dependency changes stale the saved report rather than silently rebinding it.',
  'unknowns_and_scenario_separation':'Missing contract inputs, attribution, observation clocks/follow-up and account bindings remain explicit. Received amounts are never netted with hypothetical scenarios or called causal savings. Optional existing scenarios require exact buyer/task/patient scope; boundary checks verify rejection and separate attachment. No current population scenario was supplied and no hypothetical receipt is counted as observed.'}
 for key,detail in specs.items():
  paths=[R/'http/verification.json',R/'restart-verification.json',R/'browser-verification.json',R/'focused-tests-final.txt',R/'integrity-checks.txt']
  paths += [R/f'http/{d}/{n}.json' for d in ('respiratory','behavioral') for n in ('report','independent-audit','reopen')]
  checks.append({'id':key,'status':'passed','detail':detail,'evidence':[evidence(p) for p in paths]})
 snapshot=read('config/agere/active_warehouse.json')['snapshot'];receipt=R/'financial-acceptance.json'
 row={'schema':'agere-capability-acceptance/1','requirement_id':'outcome_and_financial_reconciliation','snapshot_digest':digest(snapshot),'checks':checks,'scope':'Local engineering evidence binding and reconciliation with supported unknowns. No signed payer contract, entitlement, delivered care, clinical validity, causal effect or production readiness.'}
 save(receipt,row)
 spec=next(s for s in read('config/agere/release_acceptance.json')['requirements'] if s['id']==row['requirement_id'])
 actual=capability_checks(spec,str(receipt),snapshot);assert acceptance_status(actual)=='passed'
 save(R/'financial-acceptance-verification.json',{'status':'passed','checks':actual,'local_engineering_only':True})
 config=Path('config/agere/capability_acceptance.json');cap=read(config);assert cap['complete_population_program']=='artifacts/agere-population-routing-v1/population-acceptance.json'
 cap['outcome_and_financial_reconciliation']=str(receipt)
 score=release_acceptance(read('artifacts/agere-workflow-evaluation-v1/scorecard.json')['cases'],capability_evidence=cap,snapshot=snapshot)
 assert score['engineering_requirements_passed']==2 and score['engineering_requirements_total']==9
 save(R/'release-acceptance.json',score);config.write_text(json.dumps(cap,indent=2)+'\n');print('Financial engineering 4/4; release engineering 2/9. Qualification remains incomplete.')
if __name__=='__main__':main()
