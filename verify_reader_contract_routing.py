"""Verify full-contract abstention and existing native proposal on identical source.
No semantic scoring, target edits, training or protected-diagnostic rerun.
"""
import json,sqlite3,urllib.error,time,os
from pathlib import Path
import verify_scoped_review_compiler as H
from clinical_core.contracts import digest
ROOT=Path('artifacts/agere-reader-contract-v1')
OUT=Path(os.environ.get('AGERE_READER_VERIFY_OUT',str(ROOT)))
OUT.mkdir(exist_ok=True)

def save(name,row):
 p=OUT/(name+'.json');assert not p.exists();p.write_text(json.dumps(row,indent=2)+'\n');return row

def main():
 H.BASE=os.environ.get('AGERE_READER_VERIFY_BASE','http://localhost:8887/api/stack/')
 req=json.loads((ROOT/'workflow-request.json').read_text())
 save('workflow-predeclaration',{'request':req,'comparisons':['Existing unguarded lexical shadow proposal on identical passage','Full-contract lexical abstention','Full-contract windowed MiniLM abstention','Existing Kev proposal under complete vocabulary without automatic acceptance'], 'checks':['No incomplete-head inference','No labels emitted by abstention','Saved question/source/model/task bindings','Exact replay and reopen','Cross-patient, wrong task, extra contract rejected','Task/answer/provider ledger preserved'], 'semantic_quality_scoring':False,'clinical_review':False,'fit':False,'reason':'Mechanical contract and work integration regression; selected previously read source, not a fresh evaluation cohort.'})
 before=save('before-decision',H.call('decision?id='+req['decision_id']));assert before['dependencies_current']
 ledger=H.ledger()
 with sqlite3.connect('artifacts/agere-healthcare-v123/stack/semantic-readings.sqlite3') as db: readings_before=db.execute('select count(*) from readings').fetchone()[0];reviews_before=db.execute('select count(*) from reviews').fetchone()[0]
 baseline=save('baseline-shadow',H.call('reader-comparison/prepare',{'target':req['target'],'cohort_id':req['cohort_id'],'roles':['cohort_lexical']}))
 assert baseline['comparison']['variants']['cohort_lexical']['status']=='completed'
 with sqlite3.connect('artifacts/agere-healthcare-v123/stack/semantic-readings.sqlite3') as db: after_baseline=db.execute('select count(*) from readings').fetchone()[0]
 results=[]
 for role in ['cohort_lexical','cohort_windowed_minilm','kev']:
  request={**req,'role':role};r=save(role,H.call('decision/statement-review',request));results.append(r)
  if role!='kev':
   assert r['status']=='abstained_unsupported_contract' and r['proposed_labels'] is None and not r['reader_comparison_used']
   assert sum(len(v) for v in r['unavailable_labels'].values())==8
  else:assert r['status']=='experimental_proposal_only' and r['reading_id'] and not r['automatic_acceptance']
  assert H.call('decision/statement-review',request)==r
  assert H.call('decision/statement-review-open',{'result_id':r['result_id']})==r
 for mutation in [{'task_id':'f'*64},{'target':{**req['target'],'patient_id':'P-1003'}},{'required_labels':{'relation':['clinical_observation']}},{'role':'claude'}]:
  try:H.call('decision/statement-review',{**req,**mutation})
  except urllib.error.HTTPError as e:assert e.code in (400,409)
  else:raise AssertionError('Invalid contract accepted')
 after=save('decision-reopened',H.call('decision?id='+req['decision_id']))
 assert after['decision']==before['decision'] and after['tasks']==before['tasks']
 assert {r['result_id'] for r in results} <= {f['result_id'] for f in after['followups'] if f['schema']=='decision-statement-contract/1'}
 assert len({r['result_id'] for r in results})==3
 assert H.ledger()==ledger
 with sqlite3.connect('artifacts/agere-healthcare-v123/stack/semantic-readings.sqlite3') as db:
  readings_after=db.execute('select count(*) from readings').fetchone()[0];assert db.execute('select count(*) from reviews').fetchone()[0]==reviews_before
 assert readings_after==after_baseline  # Kev already saved; incomplete heads did not infer.
 save('http-verification',{'status':'passed','decision_id':req['decision_id'],'task_id':req['task_id'],'results':[r['result_id'] for r in results],'original_question_answer_work_unchanged':True,'new_review_rows':0,'new_semantic_readings':readings_after-readings_before,'new_paid_calls':0,'provider_ledger_digest':ledger,'full_contract_head_abstentions':2,'native_existing_reading_reused':True,'no_semantic_quality_or_accuracy_score':True,'timings':H.TIMINGS})
 print(json.dumps({'status':'passed','new_semantic_readings':readings_after-readings_before,'decision_id':req['decision_id']}))
if __name__=='__main__':main()
