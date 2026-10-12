"""Finite engineering acceptance only; no significance, causal or clinical gate."""
import hashlib,json
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.workflow_scorecard import capability_checks,acceptance_status,release_acceptance
R=Path('artifacts/agere-population-discovery-v1')
def read(p):return json.loads(Path(p).read_text())
def evidence(p):return {'path':str(p),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}
def save(p,row):
 assert not p.exists(),p;p.write_text(json.dumps(row,indent=2)+'\n')
def main():
 r=read(R/'report.json');a=read(R/'independent-audit.json');v=read(R/'http-verification.json')
 assert a['status']==v['status']==read(R/'browser-verification.json')['status']==read(R/'restart-verification.json')['status']=='passed'
 assert a['members']==r['denominators']['selected_members']==1614 and a['source_hashes']==55
 assert r['selection']['patient_ids']==read(R/'reopen.json')['report']['selection']['patient_ids']
 assert len(r['work'])==4 and v['work_unchanged'] and v['exact_replay']
 assert r['search_scope']['p_value'] is r['search_scope']['false_discovery_rate'] is r['causal_effect'] is None
 assert not r['clinical_change_established'] and r['cost']['total_cost_usd'] is None
 assert a['exhaustive_partitions_per_series']==2048 and len(r['alternatives'])==3
 checks={
  'source_linked_finding':'All 1614 selected members retained. Independent raw audit matches all55 source hashes,12 monthly counts, exact dates and receipt boundaries; inspectable member sources bind the descriptive two-block finding.',
  'artifact_and_multiplicity_controls':'Exact within-patient received-text sensitivity, source/arrival clocks, no-record members and excluded dates are explicit. One declared stream/window, fixed penalty6,2048 possible partitions. Output is restricted to descriptive hypotheses: p-value/FDR/causal effect are null, no clinical alerts or automatic actions. Additional queries explicitly expand the search family. This is not calibrated inferential multiplicity control; confirmatory/large-search qualification remains unavailable.',
  'competing_explanations_and_assumptions':'Batching/delay, repeated documentation and changed activity/capture each have observable checks and concrete missing discriminating evidence. Calendar exposure is not person-time or clinical surveillance; independent Poisson and fixed-penalty assumptions are exposed.',
  'simple_baseline_comparison':'Constant-rate baseline and existing segmentation use identical counts/calendar exposure. Independent exhaustive enumeration reproduces the optimal penalized score for original and repeat-sensitivity series. In-sample gain does not claim predictive or clinical superiority.',
  'question_path_execution':'Actual API and browser run/save/reopen, unchanged four original question-owned tasks, member received-text inspection, exact replay and fresh-process reopen pass. Fixture source/work changes invalidate results; mid-assembly changes prevent saves; immutable history rejects updates.'}
 paths=[R/(n+'.json') for n in ('predeclaration','predeclaration-schema-correction','report','independent-audit','http-verification','restart-verification','browser-verification')]+[R/'focused-final.txt']
 snapshot=read('config/agere/active_warehouse.json')['snapshot'];receipt=R/'discovery-acceptance.json'
 row={'schema':'agere-capability-acceptance/1','requirement_id':'discovery_explanation_path','snapshot_digest':digest(snapshot),
  'checks':[{'id':k,'status':'passed','detail':v,'evidence':[evidence(p) for p in paths]} for k,v in checks.items()],
  'scope':'Local descriptive source-process investigation. Clinical/causal interpretation, calibrated discovery search, replication and model qualification remain unassessed.'}
 save(receipt,row)
 spec=next(s for s in read('config/agere/release_acceptance.json')['requirements'] if s['id']==row['requirement_id'])
 actual=capability_checks(spec,str(receipt),snapshot);assert acceptance_status(actual)=='passed'
 save(R/'acceptance-verification.json',{'status':'passed','checks':actual,'clinical_or_method_qualification':False})
 p=Path('config/agere/capability_acceptance.json');cap=read(p);assert len(cap)==3;cap[row['requirement_id']]=str(receipt)
 score=release_acceptance(read('artifacts/agere-workflow-evaluation-v1/scorecard.json')['cases'],capability_evidence=cap,snapshot=snapshot)
 assert score['engineering_requirements_passed']==4 and score['engineering_requirements_total']==9
 save(R/'release-acceptance.json',score);p.write_text(json.dumps(cap,indent=2)+'\n')
 print('Discovery engineering5/5; local release4/9. Method/clinical qualification remains unassessed.')
if __name__=='__main__':main()
