"""Actual API and independent raw-count/exhaustive-partition audit of declared scope."""
import calendar, itertools, json, math, sqlite3, time, urllib.request
from collections import Counter
from datetime import date
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere import narrative_sources
from clinical_core.contracts import digest

R=Path('artifacts/agere-population-discovery-v1')
def save(name,row):
 p=R/(name+'.json');assert not p.exists(),p;p.write_text(json.dumps(row,indent=2)+'\n');return row
def call(route,body=None):
 req=urllib.request.Request('http://localhost:8885/api/stack/'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=180) as f:return json.load(f)
def ledger():
 with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:return dict(db.execute('SELECT id,status FROM calls'))
def audit(r):
 selected=set(r['selection']['patient_ids']);refs={x['record_id']:(m['patient_id'],x) for m in r['members'] for x in m['records']}
 assert len(r['members'])==len(selected)==1614
 assert len(refs)==sum(len(m['records']) for m in r['members'])
 raw=[]
 with sqlite3.connect((stack_path()/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
  for rid,pid,payload in db.execute('SELECT id,patient,payload FROM records WHERE stream=?',(r['request']['stream'],)):
   if pid not in selected:continue
   rec=json.loads(payload);assert refs[rid][0]==pid and refs[rid][1]['record_hash']==digest(rec)
   raw.append(rec)
 assert len(raw)==len(refs)
 counts=Counter();exclusions=Counter();source=Counter();lags=Counter();daycounts=Counter();included=[]
 start=r['window_start'];end=r['window_end'];labels=r['months']
 days=[calendar.monthrange(int(m[:4]),int(m[5:]))[1] for m in labels];days[-1]=int(end[-2:]);assert days==r['calendar_days']
 for rec in raw:
  avail=rec.get('available_date')
  try:valid=isinstance(avail,str) and date.fromisoformat(avail).isoformat()==avail
  except ValueError:valid=False
  status='unknown_arrival_date' if not valid else 'after_cutoff' if avail>end else 'before_window' if avail<start else 'included'
  assert refs[rec['record_id']][1]['status']==status
  if status!='included':exclusions[status]+=1;continue
  counts[avail[:7]]+=1;daycounts[avail]+=1
  included.append(rec)
  event=rec.get('event_date')
  try:valid=isinstance(event,str) and date.fromisoformat(event).isoformat()==event
  except ValueError:valid=False
  if not valid:lags['unknown_source_date']+=1
  else:
   delta=(date.fromisoformat(avail)-date.fromisoformat(event)).days
   lags['source_after_arrival' if delta<0 else 'same_day' if delta==0 else 'arrival_after_source_date']+=1
   if event<start:lags['source_before_window']+=1
   elif event>end:lags['source_after_cutoff']+=1
   else:source[event[:7]]+=1
 assert [counts[m] for m in labels]==r['arrival_comparison']['counts']
 assert dict(exclusions)==r['denominators']['excluded_records_by_reason']
 assert dict(lags)==r['source_date_sensitivity']['clock_counts']
 assert [source[m] for m in labels]==r['source_date_sensitivity']['counts']
 assert dict(daycounts)==r['arrival_day_counts']
 seen={};unique=Counter();repeats=Counter()
 for rec in sorted(included,key=lambda x:(x['available_date'],x['record_id'])):
  # Received field adapter is the approved field boundary; grouping/arithmetic is independent.
  parts=list(narrative_sources.text_fields(rec));key=(rec['patient_id'],digest(parts)) if parts else None
  expected=seen.get(key) if key else None
  assert refs[rec['record_id']][1]['repeat_of']==expected
  if expected:repeats[rec['available_date'][:7]]+=1
  else:
   unique[rec['available_date'][:7]]+=1
   if key:seen[key]=rec['record_id']
 assert [unique[m] for m in labels]==r['repeat_sensitivity']['counts']
 assert [repeats[m] for m in labels]==r['repeat_sensitivity']['repeat_counts']
 objectives=[]
 for comparison in (r['arrival_comparison'],r['repeat_sensitivity']):
  values=comparison['counts'];best=-math.inf
  for mask in itertools.product((False,True),repeat=len(values)-1):
   cuts=[0]+[i+1 for i,x in enumerate(mask) if x]+[len(values)];score=0
   for a,b in zip(cuts,cuts[1:]):
    n=sum(values[a:b]);t=sum(days[a:b]);score+=(n*math.log(n/t) if n else 0)-6
   best=max(score,best)
  n=sum(values);t=sum(days);constant=(n*math.log(n/t) if n else 0)-6
  assert math.isclose(best,comparison['candidate']['objective'],abs_tol=1e-8)
  assert math.isclose(constant,comparison['baseline']['objective'],abs_tol=1e-8)
  assert math.isclose(best-constant,comparison['penalized_objective_gain'],abs_tol=1e-8)
  objectives.append(best)
 return {'status':'passed','source_hashes':len(raw),'included_records':len(included),'members':len(selected),'count_series':[counts[m] for m in labels],
  'exact_text_repeats':sum(repeats.values()),'exhaustive_partitions_per_series':2**(len(labels)-1),'independent_best_objectives':objectives,
  'limits':'Reuses approved text-field boundary only; source counts, repeat grouping, clock arithmetic and exhaustive partition objective are independently computed. No clinical adjudication.'}
def main():
 pre=json.loads((R/'predeclaration-schema-correction.json').read_text());req={k:pre[k] for k in ('decision_id','stream','months')}
 before=ledger();view=call('decision?id='+req['decision_id']);assert view['dependencies_current']
 opts=call('decision/population-discovery-options',{'decision_id':req['decision_id']});assert req['stream'] in opts['streams']
 start=time.monotonic();report=call('decision/population-discovery',req);elapsed=time.monotonic()-start;save('report',report)
 assert call('decision/population-discovery',req)==report
 a=save('independent-audit',audit(report))
 pid=next(m['patient_id'] for m in report['members'] if m['included_records'])
 opened=save('reopen',call('decision/population-discovery-open',{'decision_id':req['decision_id'],'result_id':report['result_id'],'patient_id':pid}));assert opened['current']
 after=call('decision?id='+req['decision_id']);assert view['tasks']==after['tasks'] and report in after['followups']
 assert ledger()==before and report['question']==view['decision']['question']
 assert len(report['work'])==4 and report['selection']==view['decision']['selection']
 save('http-verification',{'status':'passed','latency_seconds':elapsed,'original_question_retained':True,'full_population_retained':True,'work_items':4,'work_unchanged':True,'new_provider_calls':0,'exact_replay':True,'source_audit':a,'result_id':report['result_id']})
 print(json.dumps({'status':'passed','seconds':elapsed,'denominators':report['denominators'],'blocks':len(report['findings']),'audit':a}))
if __name__=='__main__':main()
