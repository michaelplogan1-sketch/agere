"""Isolated nonblocked appointment replay; real source interpretation, no clinical targets."""
import copy,hashlib,json,sqlite3,sys
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen
from clinical_core.contracts import digest
from clinical_core.agere.clinical import question_decision as Q, decision_reassessment as R, record_context, longitudinal, source_synthesis as S
OUT=Path('artifacts/agere-decision-reassessment-v1');REPLAY=OUT/'replay'
SOURCE=Path(json.loads(Path('config/agere/active_warehouse.json').read_text())['build'])/'stack'

def save(name,value):
 p=OUT/(name+'.json')
 if p.exists():raise ValueError('Evidence already saved: '+str(p))
 p.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');return value

def boot():
 from clinical_core.agere.clinical import (literal_reference_runtime,structured_transport,reference_table_context,
  reference_reconciliation_transport,interpretation_recovery,linked_claim_integrity,meaning_source_dispatch)
 for m in (literal_reference_runtime,structured_transport,reference_table_context,reference_reconciliation_transport,interpretation_recovery,linked_claim_integrity):m.install()
 meaning_source_dispatch.install(REPLAY)
 S.configure_plan_answers(SimpleNamespace(path=REPLAY))

def init():
 old=json.loads(Path('artifacts/agere-buyer-decision-v1/appointment-decision.json').read_text())
 with urlopen('http://localhost:8885/api/stack/decision?id='+old['id']) as r:view=save('original-view',json.load(r))
 with urlopen('http://localhost:8885/api/stack/case-synthesis?id='+old['answer_id']) as r:answer=save('original-answer',json.load(r))
 task=view['tasks'][0]['task'];link=view['tasks'][0]['decision_binding'];old=view['decision']
 save('original-inventory',Q.received_inventory(SOURCE,old['patient_id']))
 REPLAY.mkdir()
 meta=json.loads((SOURCE/'run.json').read_text());meta.update(source_digest=digest(['reassessment-development',meta['source_digest']]),development_replay=True)
 (REPLAY/'run.json').write_text(json.dumps(meta,indent=2)+'\n')
 for name in ['annotation-link.json','operators.json']:(REPLAY/name).write_bytes((SOURCE/name).read_bytes())
 # Native retrieval files are read-only inputs. No retrieval experiment is run.
 for name in ['vectors.npy','dense-content-indices.npy']:(REPLAY/name).symlink_to((SOURCE/name).resolve())
 with sqlite3.connect(SOURCE/'stack.sqlite3') as src,sqlite3.connect(REPLAY/'stack.sqlite3') as dst:
  for table in ['patients','records','program_packages','contents','passages','result_cache','analyses','proposals']:
   sql=src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
   if not sql:continue
   dst.execute(sql[0])
   condition=" WHERE "+('id' if table=='patients' else 'patient')+" IN ('P-3002','P-1004')" if table in ['patients','records'] else ''
   if table not in ['patients','records','program_packages']:continue
   rows=src.execute('SELECT * FROM '+table+condition).fetchall()
   if rows:dst.executemany('INSERT INTO '+table+' VALUES ('+','.join('?'*len(rows[0]))+')',rows)
  dst.execute("CREATE VIRTUAL TABLE search USING fts5(text,tokenize='unicode61')")
 with sqlite3.connect(SOURCE/'program-work.sqlite3') as src,sqlite3.connect(REPLAY/'program-work.sqlite3') as dst:src.backup(dst)
 ev,lm=longitudinal.connect(SOURCE);lp=REPLAY/'longitudinal';lp.mkdir()
 with sqlite3.connect(lp/'events.sqlite3') as dst:
  dst.execute(ev.execute("SELECT sql FROM sqlite_master WHERE name='events' AND type='table'").fetchone()[0])
  rows=ev.execute("SELECT * FROM events WHERE patient IN ('P-3002','P-1004')").fetchall()
  dst.executemany('INSERT INTO events VALUES ('+','.join('?'*len(rows[0]))+')',rows)
 ev.close();lm={**lm,'source_digest':meta['source_digest'],'development_replay':True}
 (lp/'run.json').write_text(json.dumps(lm));(REPLAY/'longitudinal-link.json').write_text(json.dumps({'path':str(lp.resolve()),'manifest_sha256':hashlib.sha256((lp/'run.json').read_bytes()).hexdigest()}))
 # Add the complete observable baseline to an explicitly identified development
 # parent; preserve every original receipt and answer byte.
 old={**old,'received_inventory':Q.received_inventory(REPLAY,old['patient_id']),
      'finding_sources':[{'finding_id':f['id'],'finding_digest':digest(f),'records':{c['source']['record_id']:c['source']['record_hash'] for c in f['citations']}} for f in answer['findings']],
      'development_replay_parent':old['id']}
 old.pop('id');old['id']=digest(old);link={**link,'decision_id':old['id']}
 with Q.database(REPLAY) as db:
  db.execute('INSERT INTO question_decisions VALUES (?,?)',(old['id'],json.dumps(old)))
  db.execute('INSERT INTO question_decision_tasks VALUES (?,?,?)',(old['id'],task['id'],json.dumps(link)))
 save('parent',old);save('prior-task',task);save('unaffected-inventory-before',Q.received_inventory(REPLAY,'P-1004'))
 with sqlite3.connect(REPLAY/'stack.sqlite3') as db:
  rid='WHR-fd253d473164c8ea81c75317';prior=json.loads(db.execute('SELECT payload FROM records WHERE id=?',(rid,)).fetchone()[0])
  revised={**prior,'status':'cancelled','available_date':'2029-08-14','recorded_date':'2029-08-14',
   'development_fixture':'Controlled source-version correction; no clinical outcome truth supplied.'}
  # Remove obsolete row-origin assertion in this explicitly synthetic source edit.
  revised['_warehouse_source']={**revised['_warehouse_source'],'row_sha256':digest(['development-correction',revised['status']])}
  late={**prior,'record_id':'DEV-LATE-APPOINTMENT-STATUS','status':'fulfilled','available_date':'2029-08-15','recorded_date':'2029-08-14',
   'development_fixture':'Controlled late-arriving conflicting account; not an adjudicated outcome.'}
  late['_warehouse_source']={**late['_warehouse_source'],'row_sha256':digest(['development-late',late['status']])}
  db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(revised),rid))
  db.execute('INSERT INTO records VALUES (?,?,?,?)',(late['record_id'],late['patient_id'],late['source_table'],json.dumps(late)))
 with sqlite3.connect(lp/'events.sqlite3') as db:db.execute("DELETE FROM events WHERE json_extract(payload,'$.source.record_id')=?",(rid,))
 save('fixture-delta',{'changed':revised,'late_conflicting':late,'limits':'Controlled engineering fixture; not independent clinical or model qualification.'})
 boot();r=SimpleNamespace(path=REPLAY);origin={'decision_id':old['id'],'task_id':task['id']}
 p=save('preview',Q.review_preview(r,origin));prepared=save('prepared',R.prepare(r,{**origin,'preview_id':p['id']}))
 save('current-context',S.context(REPLAY,prepared['context_id']));save('dispatch-preview',S.preview(REPLAY,prepared['context_id']))
 print(json.dumps({'prepared':prepared['result_id'],'context':prepared['context_id'],'selected_records':len(p['selected_record_ids']),'patient':old['patient_id']}))

def infer():
 from run_integrated_stack import keys
 keys()  # Existing credentials; never print or persist values.
 from clinical_core.agere.gateway import BudgetLedger
 boot();p=json.loads((OUT/'prepared.json').read_text());ledger=BudgetLedger('artifacts/agere-full/provider-budget.sqlite3')
 try:save('current-answer',S.synthesize(REPLAY,p['context_id'],ledger,allow_live=True))
 finally:ledger.db.close()
 print('Saved actual current source interpretation')

def finish():
 boot();r=SimpleNamespace(path=REPLAY);p=json.loads((OUT/'prepared.json').read_text());a=json.loads((OUT/'current-answer.json').read_text());old=json.loads((OUT/'parent.json').read_text());task=json.loads((OUT/'prior-task.json').read_text())
 answer=save('verified-answer',S.inspect(REPLAY,a['id']))
 result=save('reassessment',R.refresh(r,{'preparation_id':p['result_id'],'answer_id':a['id'],'coverage':old['request']['coverage']}))
 assert result==R.refresh(r,{'preparation_id':p['result_id'],'answer_id':a['id'],'coverage':old['request']['coverage']})
 new=save('replacement-decision',Q.inspect(r,result['replacement_decision_id']))
 option=next(o for o in new['matching']['options'] if o['capability']['id']==task['capability_id'])
 work=save('replacement-work',R.queue(r,{'reassessment_id':result['result_id'],'option_id':option['id'],'actor':'Synthetic reassessment integration'}))
 assert work==R.queue(r,{'reassessment_id':result['result_id'],'option_id':option['id'],'actor':'Synthetic reassessment integration'})
 assert Q.care_readiness.task(REPLAY,task['id'])==task
 assert Q.received_inventory(REPLAY,'P-1004')==json.loads((OUT/'unaffected-inventory-before.json').read_text())
 assert Q.received_inventory(SOURCE,'P-3002')==json.loads((OUT/'original-inventory.json').read_text())
 save('reopened',Q.reopen(r,old['id']))
 save('verification',{'status':'passed','actual_source_interpretation':True,'changed_records':len(p['changes']),
  'same_question_and_window':new['question']==old['question'] and new['analysis_window']==old['analysis_window'],
  'current_findings':len(answer['findings']),'replacement_task':work['replacement_task_id'],'prior_task_unchanged':True,
  'other_patient_inventory_unchanged':True,'active_warehouse_unchanged':True,
  'same_input_cached_replay':True,'clinical_or_model_qualification':False,
  'limitations':['Controlled development correction and late conflicting account','No fine-grained semantic equivalence inferred for paraphrases','Missing-source acquisition remains separate acceptance gap']})
 print(json.dumps({'status':'passed','decision':new['id'],'work':work['replacement_task_id']}))

if __name__=='__main__':{'init':init,'infer':infer,'finish':finish}[sys.argv[1]]()
