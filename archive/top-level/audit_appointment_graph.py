"""Source/denominator audit for actual planner output, not model qualification."""
import json,sqlite3,hashlib
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.clinical import appointment_graph as A
OUT=Path('artifacts/agere-appointment-graph-v1')
ROOT=Path(json.loads(Path('config/agere/active_warehouse.json').read_text())['build'])/'stack'
report={}
with sqlite3.connect(ROOT/'stack.sqlite3') as db:
 roster=sorted(r[0] for r in db.execute('SELECT id FROM patients'))
 for case in ('cross_domain','population'):
  saved=json.loads((OUT/('final-'+case+'.json')).read_text());r=saved['response'];assert saved['status']==200
  nodes=[n for n in r['results'] if n['node']['operation']=='appointment_followthrough'];assert nodes
  selected=['P-3002'] if case=='cross_domain' else roster
  n=next(n for n in nodes if n['node']['parameters']['filter']=='all');d=n['result']
  assert d['patient_ids']==selected and d['patients_evaluated']==len(selected)
  row=A.inspect(ROOT,d['appointment_graph_id']);assert row['parameters']['available_by']=='2029-08-15'
  source_count=0;terminal_count=0
  for patient in row['patients']:
   raw={x[0]:json.loads(x[1]) for x in db.execute("SELECT id,payload FROM records WHERE patient=? AND stream='appointments'",(patient['patient_id'],))}
   for source in patient['sources']:
    record=raw[source['record_id']];assert source['record_hash']==digest(record)
    assert source['patient_id']==patient['patient_id']
    assert not source['available_date'] or source['available_date']<=row['parameters']['available_by']
    for k,v in source['fields'].items():assert record.get(k)==v
    source_count+=1
   for key in patient['terminal_record_ids']:
    record=raw[key];assert patient['index']['source_identity']==[record[k] for k in ['patient_id','source_system','organization_id','source_patient_id','appointment_id']]
    if patient['status'] in ('explicit_fulfilled','explicit_no_show'):
     assert record['status']==patient['terminal_status'] and patient['calendar_mature']
     assert patient['window']['event_start']<=record['event_date']<=patient['window']['event_end']
     assert record['available_date']<=patient['window']['receipt_end']
    terminal_count+=1
  assert source_count==d['source_count']
  if case=='population':
   prior=json.loads(Path('artifacts/agere-appointment-followthrough-v1/population-summary.json').read_text());assert d['status_counts']==prior['status_counts']
   assert sorted(d['patient_ids'])==sorted(prior['patient_ids'])
  assert r['question']==saved['request']['question'] and not r['clinical_execution_authorized']
  report[case]={'patients':len(selected),'source_hashes_and_literal_fields_checked':source_count,'terminal_records_checked':terminal_count,'status_counts':d['status_counts'],'question_preserved':True,'result_id':r['result_id'],'receipt':row['id'],'seconds':saved['elapsed_seconds'],'new_paid_api_calls':r['new_paid_api_calls']}
with (OUT/'source-audit.json').open('x') as f:json.dump({'status':'passed','cases':report,'qualification':'documentary engineering only'},f,indent=2)
print(json.dumps(report,indent=2))
