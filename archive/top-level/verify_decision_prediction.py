"""Actual shared question/work path and independent received-source timing audit."""
import json,sqlite3,zlib,time,urllib.request
from pathlib import Path
from collections import Counter
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
R=Path('artifacts/agere-decision-prediction-v1/http')
BASE='http://localhost:8885/api/stack/'
def save(name,row):
 p=R/(name+'.json');p.parent.mkdir(parents=True,exist_ok=True);assert not p.exists(),str(p);p.write_text(json.dumps(row,indent=2)+'\n');return row
def call(route,body=None):
 r=urllib.request.Request(BASE+route,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(r,timeout=600) as f:return json.load(f)
def ledger():
 with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:return dict(db.execute('SELECT id,status FROM calls'))
def audit(report):
 # Never reads private split rows, generator labels, or fitted predictions.
 path=stack_path();adapter=report['request']['adapter'];name='baseline-evidence.sqlite3' if adapter=='index_day' else 'decision-landmarks.sqlite3'
 src=sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
 db=sqlite3.connect((path/name).resolve().as_uri()+'?mode=ro',uri=True)
 odb=sqlite3.connect((path/'outcomes.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) if adapter=='index_day' else None
 labels=Counter();reasons=Counter();counts=Counter();seen=[]
 try:
  for m in report['members']:
   pid=m['patient_id'];seen.append(pid)
   raw=json.loads(zlib.decompress(db.execute('SELECT payload FROM receipts WHERE id=?',(m['baseline_receipt_id'],)).fetchone()[0]))
   target=json.loads(zlib.decompress(odb.execute('SELECT payload FROM receipts WHERE id=?',(m['outcome_receipt_id'],)).fetchone()[0])) if odb else raw
   b=raw if odb else raw['baseline']
   assert digest(raw)==m['baseline_receipt_hash'] and digest(target)==m['outcome_receipt_hash']
   assert b['patient_id']==target['patient_id']==pid and m['feature_cutoff']==b['cutoff']
   # Reconstruct timing eligibility from source outcome/baseline receipts without runtime assess().
   e=[]
   if odb:
    if b['status']!='baseline_observations_available':e.append('no_baseline_observations')
    if not target['indexed']:e.append('no_resolved_index')
    else:
     if not target['calendar_mature']:e.append('incomplete_calendar_followup')
     if target['recorded_label'] not in (0,1):e.append('unresolved_recorded_target')
    if target['index']:
     dates=[target['evidence'][k].get('arrival_date') for k in target['index']['evidence_ids']]
     if not any(v and v<=target['index']['date'] for v in dates):e.append('index_not_known_by_index_day')
     if not b['cutoff'] or b['cutoff']>=target['index']['date']:e.append('invalid_pre_index_feature_cutoff')
   else:
    if not target['decision']:e.append(target['status'])
    elif target['decision']['date']>report['binding']['task']['parameters']['available_by']:e.append('decision_after_available_cutoff')
    else:
     if not target['calendar_mature']:e.append('incomplete_calendar_followup')
     if target['recorded_label'] is None:e.append('unresolved_recorded_target')
    if b['status']!='baseline_observations_available':e.append('no_baseline_observations')
    if target['decision'] and b['cutoff']!=target['decision']['feature_cutoff']:e.append('invalid_decision_feature_cutoff')
   assert e==m['readiness']['exclusion_reasons'] and (not e)==m['readiness']['timing_eligible_observed_target']
   reasons.update(e)
   if not e:labels[str(target['recorded_label'])]+=1
   # Exact adapter sources and clocks; no outcome value enters the baseline source set.
   index_ids={x['source']['record_id'] for x in b['anchor'].get('source_evidence',[])}
   for ref in b['included_sources']:
    rec=json.loads(src.execute('SELECT payload FROM records WHERE id=? AND patient=?',(ref['record_id'],pid)).fetchone()[0])
    assert digest(rec)==ref['record_hash'] and rec['patient_id']==pid
    assert b['window_start']<=rec['event_date']<=b['cutoff'] and rec['available_date']<=b['cutoff']
    assert ref['record_id'] not in index_ids
    counts['independent_baseline_source_clock_checks']+=1
   for ev in target['evidence'].values():
    ref=ev['source'];rec=json.loads(src.execute('SELECT payload FROM records WHERE id=? AND patient=?',(ref['record_id'],pid)).fetchone()[0]);assert digest(rec)==ev['record_hash']
    counts['independent_outcome_source_hash_checks']+=1
   counts['members']+=1
  assert sorted(seen)==sorted(report['selection']['patient_ids']) and len(set(seen))==len(seen)
  assert dict(labels)==report['eligible_recorded_label_counts'] and dict(reasons)==report['exclusion_reason_counts']
  assert sum(labels.values())==report['timing_eligible']
  return {'status':'passed','checks':dict(counts),'eligible_recorded_labels':dict(labels),'exclusions':dict(reasons),'source_capture_established':False,'model_evaluation_performed':False}
 finally:
  src.close();db.close()
  if odb:odb.close()
def main():
 before=ledger();done=[]
 cases=[('respiratory','index_day','discharge_admission_clinical_30d'),('behavioral','receipt_day','received_discharge_future_admission_30d')]
 for domain,adapter,contract in cases:
  d=json.loads(Path('artifacts/agere-population-decision-v1/verified-v2/'+domain+'-decision.json').read_text());view=call('decision?id='+d['id']);assert view['dependencies_current']
  options=call('decision/population-prediction-options',{'decision_id':d['id']});assert any(c['id']==contract for c in options['contracts'])
  req={'decision_id':d['id'],'adapter':adapter,'contract':contract,'start':'2026-01-01','through':d['decision_date']}
  start=time.monotonic();r=save(domain+'/report',call('decision/population-prediction',req));elapsed=time.monotonic()-start
  print(json.dumps({'domain':domain,'result':r['result_id'],'seconds':round(elapsed,3)}),flush=True)
  assert r['patients_assessed']==1614 and len(r['work'])==4 and r['question']==d['question'] and r['qualification']['status']=='exact_task_evidence_match'
  assert r['baseline_comparison']['status']=='not_fitted' and r['status']=='estimate_unavailable'
  assert r['qualification']['report']['source_qualification']['timing_eligible_label_counts']==r['eligible_recorded_label_counts']
  assert call('decision/population-prediction',req)==r
  a=save(domain+'/independent-audit',audit(r))
  sample=next(m for m in r['members'] if m['readiness']['timing_eligible_observed_target'])
  opened=save(domain+'/member',call('decision/population-prediction-open',{'decision_id':d['id'],'result_id':r['result_id'],'patient_id':sample['patient_id']}));assert opened['current']
  after=call('decision?id='+d['id']);assert view['tasks']==after['tasks'] and any(f['result_id']==r['result_id'] for f in after['followups'])
  save(domain+'/reopen',{'current':after['dependencies_current'],'same_tasks':True,'result_id':r['result_id']})
  done.append({'domain':domain,'decision_id':d['id'],'result_id':r['result_id'],'seconds':elapsed,'members':1614,'owned_work':4,'timing_eligible':r['timing_eligible'],'checks':a['checks']})
 assert ledger()==before
 save('verification',{'status':'passed','programs':done,'provider_calls_added':0,'new_scores_or_fits':0,'tasks_changed':0,'external_actions':0,'clinical_qualification':False})
if __name__=='__main__':main()
