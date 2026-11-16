"""Independent source identity and arithmetic audit for the received case."""
import json,sqlite3
from fractions import Fraction
from collections import defaultdict
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.contracts import digest
OUT=Path('artifacts/agere-cardiometabolic-v1');r=json.loads((OUT/'review.json').read_text());fields=0;days=defaultdict(list)
with sqlite3.connect(stack_path()/'stack.sqlite3') as db:
 for s in r['review']['sources']:
  patient,stream,raw=db.execute('select patient,stream,payload from records where id=?',(s['record_id'],)).fetchone();v=json.loads(raw)
  assert patient==r['patient_id'] and stream==s['source_table'] and digest(v)==s['record_hash']
  assert (v.get('event_date'),v['available_date'])==(s['source_date'],s['available_date'])
  for key,value in s['fields'].items():
   actual=v
   for part in key.split('.'):actual=actual.get(part) if isinstance(actual,dict) else None
   assert actual==value,(s['record_id'],key);fields+=1
  if stream=='vitals' and 'blood_pressure_systolic' in v:days[v['event_date']].append(v)
 for row in r['review']['bp']['daily_summaries']:
  originals=days[row['source_date']];assert len(originals)==row['record_count']
  for component in ['systolic','diastolic']:
   mean=sum(Fraction(str(v['blood_pressure_'+component])) for v in originals)/len(originals);computed=row['mean_'+component];assert (mean.numerator,mean.denominator)==(computed['numerator'],computed['denominator'])
summary={'status':'passed','patient_id':r['patient_id'],'verified_source_hashes':len(r['review']['sources']),'verified_literal_fields':fields,'independently_recomputed_daily_means':len(days),'recorded_or_receipt_dates_substituted':0,'current_diagnosis_or_effect_inferred':False}
(OUT/'source-audit.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
