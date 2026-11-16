"""Independent Python scan verifies SQL evidence-query execution, not clinical truth."""
from pathlib import Path
from collections import defaultdict
import json,sqlite3
from clinical_core.agere.clinical.evidence_queries import CATALOG,execute,validate_plan
from clinical_core.agere.clinical.relation_learning import write
ROOT=Path(__file__).resolve().parent/'artifacts/agere-evidence-queries-v1'

def matches(row,clause):
 if row['kind']!=clause['source']:return False
 for field in ('concept','assertion','subject','time','relation'):
  if field not in clause:continue
  if field=='concept' and clause['concept_match']=='prefix':
   if not row[field].startswith(clause[field]):return False
  elif row[field]!=clause[field]:return False
 return 'value_equals' not in clause or json.loads(row['value_json'])==clause['value_equals']

if __name__=='__main__':
 db=sqlite3.connect((ROOT/'evidence.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
 patients={r[0] for r in db.execute('SELECT id FROM patients')};observed=[set() for _ in range(sum(len(q['plan']['clauses']) for q in CATALOG))]
 clauses=[c for q in CATALOG for c in validate_plan(q['plan'])['clauses']]
 count=0
 for row in db.execute('SELECT patient,kind,concept,assertion,subject,time,relation,value_json FROM evidence'):
  count+=1
  for i,c in enumerate(clauses):
   if matches(row,c):observed[i].add(row['patient'])
 checks=[];offset=0
 for q in CATALOG:
  plan=validate_plan(q['plan']);sets=[]
  for c in plan['clauses']:
   values=observed[offset];offset+=1;sets.append(patients-values if c['observation']=='not_observed' else values)
  expected=set.intersection(*sets) if plan['match']=='all' else set.union(*sets)
  actual=execute(ROOT,plan,limit=100);assert actual['patients_matching_evidence_plan']==len(expected)
  assert [p['patient_id'] for p in actual['patients']]==sorted(expected)[:100]
  assert all(not p['care_gap_established'] and not p['execution_authorized'] for p in actual['patients'])
  checks.append({'query':q['id'],'python_matches':len(expected),'sql_matches':actual['patients_matching_evidence_plan'],'sample_patient_ids_match':True})
 db.close();write(ROOT/'execution-audit.json',{'status':'passed','evidence_rows_scanned':count,'queries':checks,'scope':'SQL versus independent Python set evaluation; no medical or semantic validation.'});print(json.dumps(checks,indent=2))
