"""Explicit synthetic delayed/corrected receipt tests in a consistent private clone.
Run while the owned server is stopped; never modify original received sources.
These fixtures test engineering invariants, not held-out clinical quality.
"""
from pathlib import Path
import json,sqlite3,subprocess,tempfile,time
from clinical_core.contracts import digest
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical import graph_decision as G,graph_observation as B,question_decision as Q
from verify_reference_assessment_changes import install
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-graph-observation-v1';SOURCE=ROOT/'artifacts/agere-healthcare-v123/stack'

def save(name,value):
 p=OUT/(name+'.json')
 if p.exists():raise ValueError('Capture exists: '+str(p))
 p.write_text(json.dumps(value,indent=2)+'\n')

def result(path,parent):
 graph=G.graph_receipt(path,parent['graph_result_id'])
 return next(r['result'] for r in graph['results'] if r['node']['operation']==B.OPERATION)

def put(path,record):
 with sqlite3.connect(path/'stack.sqlite3') as db:
  db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(record),record['record_id']))

def main():
 source=json.loads((OUT/'workflow-verification.json').read_text());start=time.perf_counter()
 with tempfile.TemporaryDirectory(prefix='agere-observation-change-') as directory:
  path=Path(directory)/'stack';subprocess.run(['/bin/cp','-cR',str(SOURCE),str(path)],check=True)
  print('consistent_private_clone_ready',flush=True)
  stack=Stack(path,allow_live=False);r=stack.retriever;install(path,r)
  before=Q.load(path,source['decision_id']);G.inspect(path,before);original=result(path,before)
  original_request=Q.saved_followup(path,source['request_id'])
  with sqlite3.connect(path/'program-work.sqlite3') as db:
   work={table:[digest(list(row)) for row in db.execute('SELECT * FROM '+table)] for table in json.loads((OUT/'work-baseline.json').read_text())}
  with sqlite3.connect(path/'stack.sqlite3') as db:
   rows=[json.loads(raw) for raw, in db.execute("SELECT payload FROM records WHERE patient=? AND stream='received_hospital_summaries' ORDER BY id",('SYN-21D743BD-A1B',))]
  earlier,later=sorted(rows,key=lambda x:x['available_date']);assert earlier['patient_id']==later['patient_id']
  delayed={**later,'available_date':'2029-08-14','available_time':'2029-08-14T10:00:00+00:00'}
  put(path,delayed);save('private-delayed-source',{'purpose':'Explicit engineering fixture, not generator or clinical truth','before':later,'after':delayed})
  changed=G.refresh(stack,{'decision_id':before['id']});save('private-delayed-refresh',changed)
  after=Q.load(path,changed['current_decision_id']);now=result(path,after)
  assert changed['changed_member_count']==1 and {x['patient_id'] for x in changed['changes']}=={later['patient_id']}
  assert now['arrival_comparison']['counts']!=original['arrival_comparison']['counts']
  assert now['documented_date_comparison']['counts']==original['documented_date_comparison']['counts']
  assert now['denominators']['included_records']==55 and now['patients_evaluated']==1614
  assert not now['clinical_change_established'] and not now['feed_outage_established']
  stale=B.open_request(r,{'request_id':source['request_id']});assert not stale['current'];save('private-historical-request',stale)
  assert Q.saved_followup(path,source['request_id'])==original_request and Q.load(path,before['id'])==before
  replay=G.refresh(stack,{'decision_id':after['id']});assert replay['changed_member_count']==0
  save('private-delayed-replay',replay)
  # A declared replacement pointer changes provenance, never automatically drops
  # a clinical event or equates two records with one admission.
  corrected={**delayed,'supersedes_record_id':earlier['record_id']};put(path,corrected)
  save('private-correction-source',{'purpose':'Explicit same-patient/source correction-pointer fixture','record':corrected})
  correction=G.refresh(stack,{'decision_id':after['id']});save('private-correction-refresh',correction)
  corrected_parent=Q.load(path,correction['current_decision_id']);final=result(path,corrected_parent)
  assert correction['changed_member_count']==1
  assert final['arrival_comparison']['counts']==now['arrival_comparison']['counts']
  assert final['documented_date_comparison']['counts']==now['documented_date_comparison']['counts']
  pointer=next(p for p in final['correction_pointers'] if p['record_id']==corrected['record_id'])
  assert pointer['status']=='same_patient_source_pointer' and not pointer['replacement_applied']
  assert final['denominators']['included_records']==55 and not final['clinical_change_established']
  assert Q.load(path,before['id'])==before and Q.load(path,after['id'])==after
  # A record moved beyond the receipt cutoff remains preserved but not visible
  # in either clock series at this cutoff. Unknown capture never means no care.
  future={**corrected,'available_date':'2029-08-16','available_time':'2029-08-16T10:00:00+00:00'};put(path,future)
  excluded=G.refresh(stack,{'decision_id':corrected_parent['id']});save('private-cutoff-refresh',excluded)
  cutoff=result(path,Q.load(path,excluded['current_decision_id']))
  assert excluded['changed_member_count']==1 and cutoff['denominators']['included_records']==54
  assert sum(cutoff['documented_date_comparison']['counts'])==sum(final['documented_date_comparison']['counts'])-1
  assert all(p['record_id']!=corrected['record_id'] for p in cutoff['correction_pointers'])
  assert cutoff['patients_evaluated']==1614 and not cutoff['clinical_change_established']
  assert all(x['new_paid_api_calls']==0 for x in [changed,replay,correction,excluded])
  with sqlite3.connect(path/'program-work.sqlite3') as db:
   for table,hashes in work.items():assert set(hashes)<={digest(list(row)) for row in db.execute('SELECT * FROM '+table)},table
  summary={'status':'passed','fixture_kind':'Invented private engineering interventions; not held-out clinical/model evaluation','population':1614,'changed_member_count_each_edit':1,'unchanged_members_each_edit':1613,'arrival_before':original['arrival_comparison']['counts'],'arrival_after_delay':now['arrival_comparison']['counts'],'recorded_dates_unchanged_after_delay':True,'correction_pointer':pointer,'no_automatic_record_replacement':True,'cutoff_included_records':cutoff['denominators']['included_records'],'old_question_and_requests_retained':True,'old_work_tables_unchanged':len(work),'unchanged_replay_members':replay['changed_member_count'],'new_paid_api_calls':0,'seconds':time.perf_counter()-start,'clinical_change_established':False,'feed_outage_established':False}
  save('change-verification',summary);stack.close();print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
