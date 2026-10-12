"""Actual payer reanswer replay; controlled source correction, no clinical targets."""
import copy,hashlib,json,sqlite3,sys
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen,Request
from clinical_core.contracts import digest
from clinical_core.agere.clinical import question_decision as Q,decision_reassessment as R,source_synthesis as S,longitudinal
OUT=Path('artifacts/agere-grouped-reassessment-v1');REPLAY=OUT/'replay'
SOURCE=Path(json.loads(Path('config/agere/active_warehouse.json').read_text())['build'])/'stack'

def save(name,v):
 p=OUT/(name+'.json')
 if p.exists():raise ValueError('Immutable evidence already exists: '+str(p))
 p.write_text(json.dumps(v,indent=2,allow_nan=False)+'\n');return v

def http(route,body=None,port=8887):
 req=Request(f'http://localhost:{port}/api/stack/'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urlopen(req,timeout=180) as f:return json.load(f)

def boot():
 from clinical_core.agere.clinical import (literal_reference_runtime,structured_transport,reference_table_context,
  reference_reconciliation_transport,interpretation_recovery,linked_claim_integrity,meaning_source_dispatch)
 for m in (literal_reference_runtime,structured_transport,reference_table_context,reference_reconciliation_transport,interpretation_recovery,linked_claim_integrity):m.install()
 meaning_source_dispatch.install(REPLAY);S.configure_plan_answers(SimpleNamespace(path=REPLAY))

def init():
 # P-1006 is a separate payer case; neither blocked patient's data is supplied.
 with sqlite3.connect(SOURCE/'program-work.sqlite3') as db:
  candidates=[r[0] for r in db.execute("SELECT q.id FROM question_decisions q JOIN question_decision_tasks t ON q.id=json_extract(t.payload,'$.decision_id') WHERE json_extract(q.payload,'$.patient_id')='P-1006' AND (SELECT count(*) FROM json_each(q.payload,'$.source_versions.records'))>20")]
 if not candidates:raise ValueError('No actual owned payer decision')
 view=save('original-view',http('decision?id='+next(x['id'] for x in json.loads((OUT/'payer-candidate-status.json').read_text()) if x['current']),port=8885));old=view['decision'];task=view['tasks'][0]['task'];link=view['tasks'][0]['decision_binding']
 answer=save('original-answer',http('case-synthesis?id='+old['answer_id'],port=8885))
 REPLAY.mkdir();meta=json.loads((SOURCE/'run.json').read_text());meta.update(source_digest=digest(['grouped-payer-development',meta['source_digest']]),development_replay=True)
 (REPLAY/'run.json').write_text(json.dumps(meta));
 for name in ['annotation-link.json','operators.json']:(REPLAY/name).write_bytes((SOURCE/name).read_bytes())
 for name in ['vectors.npy','dense-content-indices.npy']:(REPLAY/name).symlink_to((SOURCE/name).resolve())
 with sqlite3.connect(SOURCE/'stack.sqlite3') as src,sqlite3.connect(REPLAY/'stack.sqlite3') as dst:
  for table in ['patients','records','program_packages','contents','passages','result_cache','analyses','proposals']:
   sql=src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
   if not sql:continue
   dst.execute(sql[0])
   if table not in ['patients','records','program_packages']:continue
   condition=" WHERE "+('id' if table=='patients' else 'patient')+" IN ('P-1006','P-1004')" if table in ['patients','records'] else ''
   rows=src.execute('SELECT * FROM '+table+condition).fetchall()
   if rows:dst.executemany('INSERT INTO '+table+' VALUES ('+','.join('?'*len(rows[0]))+')',rows)
  dst.execute("CREATE VIRTUAL TABLE search USING fts5(text,tokenize='unicode61')")
 with sqlite3.connect(SOURCE/'program-work.sqlite3') as src,sqlite3.connect(REPLAY/'program-work.sqlite3') as dst:src.backup(dst)
 ev,lm=longitudinal.connect(SOURCE);lp=REPLAY/'longitudinal';lp.mkdir()
 with sqlite3.connect(lp/'events.sqlite3') as dst:
  dst.execute(ev.execute("SELECT sql FROM sqlite_master WHERE name='events' AND type='table'").fetchone()[0]);rows=ev.execute("SELECT * FROM events WHERE patient IN ('P-1006','P-1004')").fetchall()
  dst.executemany('INSERT INTO events VALUES ('+','.join('?'*len(rows[0]))+')',rows)
 ev.close();lm={**lm,'source_digest':meta['source_digest'],'development_replay':True};(lp/'run.json').write_text(json.dumps(lm));(REPLAY/'longitudinal-link.json').write_text(json.dumps({'path':str(lp.resolve()),'manifest_sha256':hashlib.sha256((lp/'run.json').read_bytes()).hexdigest()}))
 save('active-inventory-before',Q.received_inventory(SOURCE,'P-1006'))
 old={**old,'received_inventory':Q.received_inventory(REPLAY,'P-1006'),'finding_sources':[{'finding_id':f['id'],'finding_digest':digest(f),'records':{c['source']['record_id']:c['source']['record_hash'] for c in f['citations']}} for f in answer['findings']],'development_replay_parent':old['id']};old.pop('id');old['id']=digest(old);link={**link,'decision_id':old['id']}
 with Q.database(REPLAY) as db:
  db.execute('INSERT INTO question_decisions VALUES (?,?)',(old['id'],json.dumps(old)));db.execute('INSERT INTO question_decision_tasks VALUES (?,?,?)',(old['id'],task['id'],json.dumps(link)))
 save('parent',old);save('prior-task',task);save('unaffected-inventory-before',Q.received_inventory(REPLAY,'P-1004'))
 with sqlite3.connect(REPLAY/'stack.sqlite3') as db:
  records=[json.loads(r[0]) for r in db.execute("SELECT payload FROM records WHERE patient='P-1006'")]
  # A source-category change is not required. A controlled corrected narrative
  # exposes disagreement without inventing eligibility or actual coverage.
  prior=next(r for r in records if r['record_id'] in old['source_versions']['records'] and r['source_table']=='coverage_verifications')
  revised=copy.deepcopy(prior);revised['development_source_note']='Controlled received correction: coverage verification remains pending; this account does not establish current benefits.'
  revised['available_date']='2029-08-15';revised['_warehouse_source']={**revised['_warehouse_source'],'row_sha256':digest(['development-correction',revised['development_source_note']])}
  db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(revised),revised['record_id']))
 with sqlite3.connect(lp/'events.sqlite3') as db:db.execute("DELETE FROM events WHERE json_extract(payload,'$.source.record_id')=?",(prior['record_id'],))
 save('fixture-delta',{'before':prior,'after':revised,'clinical_adjudicated':False})
 boot();r=SimpleNamespace(path=REPLAY);origin={'decision_id':old['id'],'task_id':task['id']};save('origin',origin)
 p=save('preview',Q.review_preview(r,origin));prep=save('prepared',R.prepare(r,{**origin,'preview_id':p['id']}))
 assert prep['schema']==R.GROUP_PREPARATION
 plan=save('plan',Q.source_plans.inspect(r,{'id':prep['source_plan_id']}))
 dispatch=[S.preview(REPLAY,g['context_id']) for g in plan['plan']['groups']];save('dispatch-previews',dispatch)
 print(json.dumps({'patient':'P-1006','groups':len(plan['plan']['groups']),'records':len(p['selected_record_ids']),'preparation':prep['result_id']}))

def infer():
 from run_integrated_stack import keys
 from clinical_core.agere.gateway import BudgetLedger
 keys();boot();plan=json.loads((OUT/'plan.json').read_text());ledger=BudgetLedger('artifacts/agere-full/provider-budget.sqlite3')
 try:
  for g in plan['plan']['groups']:
   name='group-answer-'+str(g['group_number'])
   if (OUT/(name+'.json')).exists():continue
   result=S.synthesize(REPLAY,g['context_id'],ledger,allow_live=True);save(name,result)
   print(json.dumps({'group':g['group_number'],'status':result['status'],'calls':result['new_paid_api_calls']}),flush=True)
 finally:ledger.db.close()


def local_check(name='http-verification'):
 from urllib.error import HTTPError
 p=json.loads((OUT/'prepared.json').read_text());origin=json.loads((OUT/'origin.json').read_text())
 preview=http('decision/review-preview',origin);assert preview==p['preview']
 assert http('decision/reanswer-prepare',{**origin,'preview_id':preview['id']})==p
 assert http('decision/reanswer-prepare',{**origin,'preview_id':preview['id']})==p
 plan=http('retrieval-source-plan/inspect',{'id':p['source_plan_id']});assert plan==json.loads((OUT/'plan.json').read_text())
 listing=http('source-plan-answers/listing',{'plan_id':p['source_plan_id']});assert listing['answered_groups']==0 and listing['total_groups']==2
 failures=[]
 for route,body,status in [('source-plan-answers/prepare',{'plan_id':p['source_plan_id'],'attachment_ids':[]},400),('decision/reanswer-prepare',{**origin,'preview_id':'0'*64},409)]:
  try:http(route,body)
  except HTTPError as e:
   assert e.code==status;failures.append({'route':route,'status':e.code,'body':json.loads(e.read())})
  else:raise AssertionError('Incomplete or stale request accepted')
 view=http('decision?id='+origin['decision_id']);task=next(t['task'] for t in view['tasks'] if t['task']['id']==origin['task_id']);assert task==json.loads((OUT/'prior-task.json').read_text())
 save(name,{'actual_preparation_idempotent':True,'exact_groups':2,'records':24,'source_units':53,'missing_groups_block':True,'stale_preview_block':True,'failures':failures,'original_task_unchanged':True,'actual_changed_answer':False,'provider_calls':0})
 print('Actual HTTP preparation, grouping, missing-answer and stale-preview rejection passed.')

def finish():
 # Run only after specifically authorized real inference saved every group.
 p=json.loads((OUT/'prepared.json').read_text());plan=json.loads((OUT/'plan.json').read_text());old=json.loads((OUT/'parent.json').read_text());prior=json.loads((OUT/'prior-task.json').read_text());ids=[]
 for g in plan['plan']['groups']:
  answer=json.loads((OUT/('group-answer-'+str(g['group_number'])+'.json')).read_text())
  row=http('source-plan-answers/attach',{'plan_id':p['source_plan_id'],'answer_id':answer['id']});ids.append(row['attachment']['id'])
 selection=save('actual-selection',http('source-plan-answers/prepare',{'plan_id':p['source_plan_id'],'attachment_ids':ids}))
 answer=save('actual-complete-answer',http('source-plan-answers/compose',{'snapshot_id':selection['snapshot']['id']}))
 req={'preparation_id':p['result_id'],'answer_id':answer['id'],'coverage':old['request']['coverage']}
 revised=save('actual-reassessment',http('decision/reanswer-refresh',req));assert http('decision/reanswer-refresh',req)==revised
 view=save('actual-replacement-view',http('decision?id='+revised['replacement_decision_id']));assert view['dependencies_current']
 replacement=view['decision'];assert all(replacement[k]==old[k] for k in ('question','patient_id','analysis_window','buyer_id'))
 option=next(o for o in replacement['matching']['options'] if o['capability']['id']==prior['capability_id'])
 work=save('actual-replacement-work',http('decision/reanswer-queue',{'reassessment_id':revised['result_id'],'option_id':option['id'],'actor':'Synthetic grouped reassessment integration'}))
 assert work['replacement_task_id']!=prior['id'];assert Q.care_readiness.task(REPLAY,prior['id'])==prior
 assert Q.received_inventory(REPLAY,'P-1004')==json.loads((OUT/'unaffected-inventory-before.json').read_text())
 save('actual-finish-verification',{'actual_current_group_answers':len(ids),'actual_current_replacement_work':True,'original_question_window_buyer_retained':True,'original_task_retained':True,'unaffected_patient_inventory_retained':True,'clinical_validated':False,'cross_group_interpretation':answer['coverage']['cross_group_relations_assessed']})
 print('Actual current interpretation and replacement internal work verified.')

if __name__=='__main__':{'init':init,'infer':infer,'local':local_check,'restart':lambda:local_check('restart-verification'),'finish':finish}[sys.argv[1]]()
