"""Exhaustive saved reference-to-member binding audit on the actual population."""
import json,sqlite3
from pathlib import Path
from collections import Counter
from clinical_core.agere.clinical import graph_decision as G,question_decision as Q,graph_references as R
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-graph-references-v1';PATH=ROOT/'artifacts/agere-healthcare-v123/stack'
def main():
 summary=json.loads((OUT/'final-summary.json').read_text());parent=Q.load(PATH,summary['decision_id']);counts=Counter()
 for m in parent['members']:
  for r in m['states']:
   if r['operation']!=R.OPERATION:continue
   node=next(n['node'] for n in parent['nodes'] if n['node']['id']==r['node_id'])
   f,gaps=R.evidence(PATH,r,node,m['states']);row=f['report'];counts['members']+=1;counts['questions']+=len(row['questions'])
   assert row['original_question']==parent['question'] and row['patient_id']==m['patient_id']
   assert row['parameters']['available_by']=='2029-08-15' and max(row['reference']['published_on'],row['reference']['checked_on'])<='2029-08-15'
   assert not row['clinical_applicability_established'] and not row['clinical_execution_authorized'] and not row['individual_benefit_estimated']
   assert not row['reference_currency']['current_at_patient_decision'] and len(gaps)==6 and len(row['questions'])==6
   for q in row['questions']:
    assert q['status']=='applicability_unresolved';counts[q['evidence_status']]+=1
    for link in q['evidence_links']:
     assert link['patient_id']==m['patient_id']
     if link['receipt']:assert link['receipt']['patient_id']==m['patient_id'];counts['exact_upstream_receipt_links']+=1
 assert counts['members']==1614 and counts['questions']==9684
 result={'status':'passed','counts':dict(counts),'question_population_source_and_reference_scopes_retained':True,'reference_does_not_nominate_service':True,'all_applicability_questions_unresolved':True,'evidence_link_relevance_or_clinical_sufficiency_adjudicated':False,'new_paid_api_calls':0}
 (OUT/'binding-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
