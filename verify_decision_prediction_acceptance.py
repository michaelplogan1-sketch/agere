"""Finite prediction engineering gate; no model or clinical qualification."""
import json,hashlib
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.workflow_scorecard import capability_checks,acceptance_status,release_acceptance
R=Path('artifacts/agere-decision-prediction-v1')
def read(p):return json.loads(Path(p).read_text())
def evidence(p):return {'path':str(p),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}
def save(p,row):
 assert not p.exists(),str(p);p.write_text(json.dumps(row,indent=2)+'\n')
def main():
 v=read(R/'http/verification.json');assert v['status']=='passed' and len(v['programs'])==2
 assert read(R/'browser-verification.json')['status']=='passed' and read(R/'restart-verification.json')['status']=='passed'
 for domain in ('respiratory','behavioral'):
  r=read(R/f'http/{domain}/report.json');a=read(R/f'http/{domain}/independent-audit.json')
  assert r['patients_assessed']==len(r['members'])==1614 and len(r['work'])==4
  assert a['status']=='passed' and a['eligible_recorded_labels']==r['eligible_recorded_label_counts']
  assert r['qualification']['status']=='exact_task_evidence_match' and r['baseline_comparison']['status']=='not_fitted'
  assert all(m['patient_risk'] is None and not m['clinical_negative_established'] for m in r['members'])
  assert r['cost']['total_cost_usd'] is None and r['review_burden']['human_review_minutes'] is None
 specs={
  'availability_time_and_leakage_checks':'Independent audit verifies 6,360 index-day and 8,537 receipt-day baseline source hashes, cutoff clocks and index-source exclusions, plus 147 outcome references per query. Features, received labels and prior protected development qualification stay separate. No hidden generator truth, new fit or model scoring enters this path.',
  'outcome_horizon_censoring_and_missingness':'Every member is retained under the registered index/receipt, endpoint and 30-day horizon definitions. Independent timing eligibility reproduces 18 and 74 recorded-zero targets, with late/unknown index, baseline and immature follow-up reasons explicit. These observations do not establish clinical negatives.',
  'baseline_or_identified_nonestimability':'A current exact task/evidence report links the frozen constant-prevalence/logistic experiment and declared class-support failures. The selected sources and protected build both lack required classes; no discriminative comparison or clinical risk is estimated. Mismatched windows, unavailable qualification and empty cohorts retain explicit unknowns rather than borrow another population performance.',
  'versioned_query_result':'Both actual original population questions save and reopen a dated prediction-readiness follow-up with source/profile/contract/model-report identities, complete membership, exact source receipts and four unchanged owned tasks. Browser execution, exact replay, independent fresh-process reopen and stale evidence/work/receipt rejection pass.'}
 paths=[R/'http/verification.json',R/'restart-verification.json',R/'browser-verification.json',R/'focused-final.txt',R/'additional-boundaries.txt']+[R/f'http/{d}/{n}.json' for d in ('respiratory','behavioral') for n in ('report','independent-audit','reopen')]
 checks=[{'id':key,'status':'passed','detail':detail,'evidence':[evidence(p) for p in paths]} for key,detail in specs.items()]
 snapshot=read('config/agere/active_warehouse.json')['snapshot'];receipt=R/'prediction-acceptance.json'
 row={'schema':'agere-capability-acceptance/1','requirement_id':'longitudinal_prediction_path','snapshot_digest':digest(snapshot),'checks':checks,'scope':'Local question-to-source/work engineering with evidence-based inability to estimate. Model qualification, clinical validity and external execution remain incomplete.'};save(receipt,row)
 spec=next(s for s in read('config/agere/release_acceptance.json')['requirements'] if s['id']==row['requirement_id']);actual=capability_checks(spec,str(receipt),snapshot);assert acceptance_status(actual)=='passed'
 save(R/'acceptance-verification.json',{'status':'passed','checks':actual,'model_qualification':False})
 config=Path('config/agere/capability_acceptance.json');cap=read(config);assert len(cap)==2;cap['longitudinal_prediction_path']=str(receipt)
 score=release_acceptance(read('artifacts/agere-workflow-evaluation-v1/scorecard.json')['cases'],capability_evidence=cap,snapshot=snapshot);assert score['engineering_requirements_passed']==3 and score['engineering_requirements_total']==9
 save(R/'release-acceptance.json',score);config.write_text(json.dumps(cap,indent=2)+'\n');print('Prediction engineering 4/4; release engineering 3/9. No model qualification.')
if __name__=='__main__':main()
