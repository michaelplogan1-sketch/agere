"""Independent source projection and interval audit against the received stack."""
import json,sqlite3,hashlib
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from clinical_core.contracts import digest
OUT=Path('artifacts/agere-operational-context-v1');db=sqlite3.connect('file:artifacts/agere-healthcare-v123/stack/stack.sqlite3?mode=ro',uri=True)
checks={'source_hashes':0,'literal_fields':0,'independent_intervals':0,'independent_arrival_delays':0,'independent_summary_statistics':0};scope={}
for kind in ('resource','dependency'):
 ctx=json.loads((OUT/(kind+'-context.json')).read_text());scenario=json.loads((OUT/(kind+'-sensitivity.json')).read_text())
 assert digest({k:v for k,v in ctx.items() if k!='id'})==ctx['id'];assert scenario['context_hash']==digest(ctx)
 for report in ctx['reports']:
  raw={rid:json.loads(payload) for rid,payload in db.execute('SELECT id,payload FROM records WHERE patient=? AND stream IN (?,?,?)',(report['patient_id'],'appointments','encounters','referrals'))};assert set(raw)=={x['record_id'] for x in report['sources']}
  for source in report['sources']:
   r=raw[source['record_id']];assert digest(r)==source['record_hash'];checks['source_hashes']+=1
   for field,value in source['fields'].items():assert r.get(field)==value;checks['literal_fields']+=1
  n=0
  for observation in report['appointment_observations']:
   if observation['interval_issue'] is not None:continue
   a=raw[observation['appointment_record_id']];e=raw[observation['interval_encounter_record_id']];assert a['completed_encounter_id']==e['encounter_id'];assert a['status']=='fulfilled' and e['status']=='completed'
   for k in ('patient_id','source_system','organization_id','source_patient_id'):assert a[k]==e[k]
   start=datetime.fromisoformat(e['start_time']);end=datetime.fromisoformat(e['end_time']);d=Fraction(str((end-start).total_seconds()))/60;assert d==Fraction(observation['interval_minutes']['numerator'],observation['interval_minutes']['denominator']);checks['independent_intervals']+=1;n+=1
   if observation['arrival_delay_minutes'] is not None:
    arrived=datetime.fromisoformat(e['available_time']);d=Fraction(str((arrived-end).total_seconds()))/60;assert d==Fraction(observation['arrival_delay_minutes']['numerator'],observation['arrival_delay_minutes']['denominator']);checks['independent_arrival_delays']+=1
  assert n==report['denominators']['included_unique_linked_encounters']
  for group in report['interval_summaries']:
   values=sorted(Fraction(x['interval_minutes']['numerator'],x['interval_minutes']['denominator']) for x in group['raw_intervals']);n=len(values)
   expected={'mean_minutes':sum(values)/n,'median_minutes':(values[(n-1)//2]+values[n//2])/2,'min_minutes':values[0],'max_minutes':values[-1]}
   for k,v in expected.items():assert v==Fraction(group[k]['numerator'],group[k]['denominator']);checks['independent_summary_statistics']+=1
  scope[report['patient_id']]=report['denominators']
 for case,comp in scenario['comparisons'].items():
  a=comp['original'];b=comp['supplied_duration_scenario'];old={j['id']:j for j in a['schedule']+a['blocked_tasks']};new={j['id']:j for j in b['schedule']+b['blocked_tasks']};assert set(old)==set(new)==set(scenario['selected_task_ids'])
  for key in old:
   assert new[key]['duration_minutes']==old[key]['duration_minutes']+5
   for field in ('patient_id','service_id','ready_minute'):assert old[key][field]==new[key][field]
  assert comp['completion_count_difference']==b['completed_by_horizon']-a['completed_by_horizon']
result={'status':'passed',**checks,'distinct_source_records':sum(x['received_appointment_records']+x['received_encounter_records']+x['received_referral_records'] for x in scope.values()),'counts_scope':'Both resource and dependency attachments audit the same exact patient sources; distinct records reported separately.','source_patient_scope':scope,'duration_conversion_from_encounter':False,'original_task_scope_and_readiness_preserved':True}
(OUT/'source-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
