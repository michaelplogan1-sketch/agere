"""Current-source exact review/dispatch workflow. Never creates reviewer records."""
import json,sqlite3,urllib.error,os
from pathlib import Path
import verify_scoped_review_compiler as H
ROOT=Path('artifacts/agere-review-dispatch-v1')
OUT=ROOT/os.environ.get('AGERE_REVIEW_OUT','http');OUT.mkdir(exist_ok=False)
def save(name,row):
 (OUT/(name+'.json')).write_text(json.dumps(row,indent=2)+'\n');return row
def stable(row):return {k:v for k,v in row.items() if k!='request_execution_id'}
def counts():
 with sqlite3.connect('artifacts/agere-healthcare-v123/stack/semantic-readings.sqlite3') as db:
  return {t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ['readings','reviews']}
def main():
 H.BASE=os.environ.get('AGERE_REVIEW_BASE','http://localhost:8887/api/stack/')
 req=json.loads(Path('artifacts/agere-reader-contract-v1/workflow-request.json').read_text());req['role']='kev'
 before=save('before',H.call('decision?id='+req['decision_id']));ledger=H.ledger();saved_counts=counts()
 baseline=save('baseline',H.call('reader-comparison/prepare',{'target':req['target'],'cohort_id':req['cohort_id'],'roles':['kev']}));assert baseline['cache_hit']
 row=save('statement',H.call('decision/statement-review',req))
 replay=save('statement-replay',H.call('decision/statement-review',req));assert stable(row)==stable(replay)
 assert H.call('decision/statement-review-open',{'result_id':row['result_id']})==stable(row)
 status=save('review-status',H.call('decision/statement-review-status',{'result_id':row['result_id']}))
 assert status['status']=='not_reviewed' and status['review_revision']==0 and not status['human_review_created']
 bad={'result_id':row['result_id'],'reading_id':row['reading_id'],'review_revision':1,'review_digest':'f'*64,'effort':{'seconds':None,'declared_by':None}}
 try:H.call('decision/statement-review-attach',bad)
 except urllib.error.HTTPError as e:assert e.code in (400,409);save('unreviewed-rejection',{'http_status':e.code,'body':json.loads(e.read())})
 else:raise AssertionError('unreviewed accepted')
 for change in [{'task_id':'f'*64},{'target':{**req['target'],'patient_id':'P-1003'}}]:
  try:H.call('decision/statement-review',{**req,**change})
  except urllib.error.HTTPError as e:assert e.code in (400,409)
  else:raise AssertionError('wrong work/source accepted')
 abstain=save('abstain',H.call('decision/statement-review',{**req,'role':'cohort_lexical'}));assert abstain['reading_id'] is None
 lineage=save('lineage',H.call('decision/lineage',{'decision_id':req['decision_id']}))
 for result in [row,replay]:
  trace=next(t for t in lineage['traces'] if t['execution_id']==result['request_execution_id'])
  event=next(e for e in trace['events'] if e['kind']=='local_reader_returned')
  assert event['comparison_cache_hit'] and event['inference_this_attempt'] is False and event['reading_id']==row['reading_id']
  assert not any(e['kind']=='reserved' for e in trace['events'])
 failures=[t for t in lineage['traces'] if t['events'][0]['operation'].endswith('statement-review-attach')]
 assert failures[-1]['status']=='operation_failed'
 after=save('after',H.call('decision?id='+req['decision_id']))
 assert before['decision']==after['decision'] and before['tasks']==after['tasks']
 assert counts()==saved_counts and H.ledger()==ledger
 save('verification',{'status':'passed','case':'existing original respiratory question/task/source','new_review_rows':0,'new_readings':0,'new_paid_calls':0,'ledger_digest':ledger,'original_question_answer_work_unchanged':True,'review_status':'not_reviewed; positive attachments verified only in isolated fixtures','statement_result_id':row['result_id'],'comparison_id':baseline['comparison']['id'],'checks':['exact replay','current cached comparison vs historical inference','same-source baseline','incomplete head abstains','unreviewed attachment rejected','wrong work/patient rejected','failed attachment bound to original question','restart persistable history'],'timings':H.TIMINGS})
 print(json.dumps({'status':'passed','out':str(OUT),'result_id':row['result_id']}))
if __name__=='__main__':main()
