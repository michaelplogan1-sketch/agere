import gzip,json,sqlite3
from pathlib import Path
from collections import Counter
from datetime import date,timedelta
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
out=Path('artifacts/agere-appointment-followthrough-v1');s=json.loads((out/'population-summary.json').read_text());r=json.loads((stack_path()/'appointment-followthrough'/(s['id']+'.json')).read_text());db=sqlite3.connect((stack_path()/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
checked=fields=labels=0;noindex_households=set();source_counts=Counter()
for p in r['patients']:
 raw={x[0]:json.loads(x[1]) for x in db.execute("SELECT id,payload FROM records WHERE patient=? AND stream='appointments'",(p['patient_id'],))}
 for source in p['sources']:
  src=raw[source['record_id']];assert digest(src)==source['record_hash'];checked+=1
  for k,v in source['fields'].items():assert src.get(k)==v;fields+=1
 source_counts.update([p['status']])
 if p['index']:
  idx=p['index'];bookings=[x for x in raw.values() if x.get('status')=='booked' and x.get('available_date') and '2026-01-01'<=x['available_date']<=r['source']['snapshot']['as_of']]
  assert idx['receipt_date']==min(x['available_date'] for x in bookings)
 if p['recorded_label'] is not None:
  labels+=1;assert p['calendar_mature'];assert p['terminal_status'] in ('fulfilled','no_show');assert bool(p['recorded_label'])==(p['terminal_status']=='fulfilled');assert p['terminal_record_ids']
  for key in p['terminal_record_ids']:
   x=raw[key];assert x['status']==p['terminal_status'];assert p['window']['event_start']<=x['event_date']<=p['window']['event_end'];assert p['decision']['feature_cutoff']<x['available_date']<=p['window']['receipt_end'];assert [x.get(k) for k in ('patient_id','source_system','organization_id','source_patient_id','appointment_id')]==p['index']['source_identity']
 else:
  for payload, in db.execute('SELECT payload FROM records WHERE patient=?',(p['patient_id'],)):
   h=json.loads(payload).get('household_id')
   if h:noindex_households.add(str(h))
assert not noindex_households, 'Unindexed household links require protection metadata repair before fitting'
xs={x['patient_id']:x for x in map(json.loads,(out/'dataset/features.jsonl').read_text().splitlines())};targets={x['patient_id']:x for x in map(json.loads,(out/'dataset/targets.jsonl').read_text().splitlines())};pop={p['patient_id']:p for p in r['patients']};baseline_count=baseline_sources=0
with gzip.open(out/'dataset/baselines.jsonl.gz','rt') as f:
 for line in f:
  b=json.loads(line);x=xs[b['patient_id']];y=targets[b['patient_id']];p=pop[b['patient_id']];base=b['baseline'];baseline_count+=1
  assert b['id']==x['baseline_receipt_id']==y['baseline_receipt_id'];assert x['values']==base['features'];assert y['recorded_label']==p['recorded_label'];assert y['clinical_label'] is None
  index_ids=set(p['index']['record_ids']) if p['index'] else set();target_ids=set(p['terminal_record_ids']);baseline_ids={s['record_id'] for s in base['included_sources']};assert not baseline_ids&(index_ids|target_ids)
  for e in base['included_sources']:
   assert e['source_date']<=base['cutoff'] and e['arrival_date']<=base['cutoff'];baseline_sources+=1
q=json.loads((out/'learning/qualification.json').read_text());sets={}
for row in q['rows']:
 for kind in ('patient_id','family','component'):sets.setdefault((kind,row[kind]),set()).add(row['partition'])
assert all(len(v)==1 for v in sets.values());assert q['outer_evaluation_used_for_fitting_or_scoring'] is False
# Independently reproduce the held-out constant baseline and calibrated Brier scores.
model=json.loads((out/'learning/models/run.json').read_text());pred=json.loads((out/'learning/models/development-predictions.json').read_text());train=[x['recorded_label'] for x in q['rows'] if x['partition']=='train'];prevalence=sum(train)/len(train);n=len(pred);brier=sum((p['recorded_label']-prevalence)**2 for p in pred)/n;assert abs(brier-model['constant_training_prevalence']['brier'])<1e-12
for name in model['models']:
 measured=sum((p['recorded_label']-p['models'][name])**2 for p in pred)/n;assert abs(measured-model['models'][name]['calibrated']['brier'])<1e-12
result={'status':'passed','exact_source_records':checked,'literal_fields':fields,'explicit_binary_targets':labels,'states':dict(source_counts),'baseline_patient_receipts':baseline_count,'baseline_source_clock_checks':baseline_sources,'baseline_target_record_overlap':0,'unindexed_household_links':0,'related_partitions_overlap':0,'outer_evaluation_scored':False,'independent_brier_checks':3,'heldout_development_patients':n,'independent_clinical_validation':False};(out/'source-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));db.close()
